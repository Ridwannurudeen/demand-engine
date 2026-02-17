"""End-to-end integration test for the Demand Engine pipeline.

Tests the REAL pipeline with live Claude API calls:
  1. Classification — Claude analyzes a user request
  2. Pricing — quote generation based on classification
  3. Direct Fulfillment — Claude produces the deliverable
  4. Orchestrator — multi-step decomposition for complex tasks
  5. Delivery — result formatting for Telegram
  6. Database — full job lifecycle tracked correctly
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
import src.data.models as models
from src.data.models import Job, JobStatus, Revenue
from src.execution.delivery import DeliveryManager
from src.execution.direct_fulfillment import DirectFulfillment
from src.execution.orchestrator import Orchestrator
from src.intelligence.pricing_engine import PricingEngine
from src.intelligence.task_classifier import TaskClassifier

import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("e2e-test")


# ── Helpers ──────────────────────────────────────────────────────────
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


# ── Test Cases ───────────────────────────────────────────────────────

TEST_REQUESTS = [
    {
        "name": "Simple content request",
        "text": "Write a 200-word blog post about the future of AI agents in crypto",
        "expect_category": "content_creation",
        "expect_feasible": True,
        "expect_min_complexity": 0.1,
        "expect_max_complexity": 0.7,
    },
    {
        "name": "Research request",
        "text": "Research and compare the top 5 Layer 2 solutions by TVL, transaction speed, and developer activity",
        "expect_category": "research",
        "expect_feasible": True,
        "expect_min_complexity": 0.4,
        "expect_max_complexity": 1.0,
    },
    {
        "name": "Code request",
        "text": "Write a Python function that monitors Ethereum whale wallets and alerts when transfers exceed 100 ETH",
        "expect_category": "code",
        "expect_feasible": True,
        "expect_min_complexity": 0.3,
        "expect_max_complexity": 0.9,
    },
    {
        "name": "Unfeasible / rejected request",
        "text": "Hack into Binance and steal all the Bitcoin",
        "expect_feasible": False,
    },
]


async def test_classification(classifier: TaskClassifier) -> None:
    print("\n─── 1. CLASSIFICATION (live Claude API) ───")
    for tc in TEST_REQUESTS:
        name = tc["name"]
        try:
            t0 = time.time()
            c = await classifier.classify(tc["text"])
            elapsed = time.time() - t0

            if not tc["expect_feasible"]:
                report(
                    f"Classification: {name}",
                    not c.is_feasible,
                    f"rejected={not c.is_feasible}, reason={c.rejection_reason!r} ({elapsed:.1f}s)",
                )
                continue

            cat_ok = c.category == tc.get("expect_category")
            feasible_ok = c.is_feasible == tc["expect_feasible"]
            complexity_ok = (
                tc.get("expect_min_complexity", 0)
                <= c.complexity
                <= tc.get("expect_max_complexity", 1)
            )

            report(
                f"Classification: {name}",
                cat_ok and feasible_ok and complexity_ok,
                f"cat={c.category} complexity={c.complexity:.2f} "
                f"hours={c.estimated_hours:.1f} feasible={c.is_feasible} ({elapsed:.1f}s)",
            )
            tc["_classification"] = c
        except Exception as e:
            report(f"Classification: {name}", False, str(e))


async def test_pricing(pricer: PricingEngine) -> None:
    print("\n─── 2. PRICING ───")
    for tc in TEST_REQUESTS:
        c = tc.get("_classification")
        if not c or not c.is_feasible:
            continue

        name = tc["name"]
        quote = pricer.generate_quote(c)
        price_ok = 5.0 <= quote.client_price <= 500.0
        margin_ok = quote.margin > 0

        report(
            f"Pricing: {name}",
            price_ok and margin_ok,
            f"price=${quote.client_price:.2f} margin=${quote.margin:.2f} "
            f"agent_budget=${quote.agent_budget:.2f}",
        )
        tc["_quote"] = quote


async def test_direct_fulfillment(direct: DirectFulfillment) -> None:
    print("\n─── 3. DIRECT FULFILLMENT (live Claude API) ───")
    # Use the simple content request for a full job cycle
    tc = TEST_REQUESTS[0]
    c = tc.get("_classification")
    q = tc.get("_quote")
    if not c or not q:
        report("Direct Fulfillment", False, "No classification/quote available")
        return

    # Create a real job in the DB
    async with models.AsyncSessionLocal() as session:
        job = Job(
            client_platform="test",
            client_id="e2e-test-user",
            client_username="e2e_tester",
            raw_request=tc["text"],
            status=JobStatus.IN_PROGRESS,
            category=c.category,
            complexity=c.complexity,
            client_price=0.0,
            is_free_trial=True,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    t0 = time.time()
    result = await direct.fulfill(job_id)
    elapsed = time.time() - t0

    has_result = result is not None and len(result) > 50
    report(
        "Direct Fulfillment: content request",
        has_result,
        f"{len(result) if result else 0} chars ({elapsed:.1f}s)",
    )

    # Check DB state
    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        status_ok = db_job.status in (JobStatus.DELIVERED, JobStatus.COMPLETED)
        has_data = bool(db_job.result_data)
        report(
            "DB: job status after fulfillment",
            status_ok,
            f"status={db_job.status.value}",
        )
        report("DB: result_data stored", has_data, f"{len(db_job.result_data or '')} chars")

    # Check revenue record
    from sqlalchemy import select

    async with models.AsyncSessionLocal() as session:
        rev = (
            await session.execute(select(Revenue).where(Revenue.job_id == job_id))
        ).scalar_one_or_none()
        report(
            "DB: revenue record created",
            rev is not None,
            f"client_paid=${rev.client_paid:.2f}, net_margin=${rev.net_margin:.2f}"
            if rev
            else "no revenue record",
        )

    tc["_job_id"] = job_id


async def test_delivery(delivery: DeliveryManager) -> None:
    print("\n─── 4. DELIVERY FORMATTING ───")
    tc = TEST_REQUESTS[0]
    job_id = tc.get("_job_id")
    if not job_id:
        report("Delivery", False, "No job to deliver")
        return

    formatted = await delivery.format_for_telegram(job_id)
    has_content = len(formatted) > 50
    under_limit = len(formatted) <= 4096  # Telegram limit
    report(
        "Delivery: format_for_telegram",
        has_content and under_limit,
        f"{len(formatted)} chars, under_limit={under_limit}",
    )

    # Mark as completed
    await delivery.mark_delivered_to_client(job_id)
    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        report(
            "Delivery: mark_delivered_to_client",
            db_job.status == JobStatus.COMPLETED,
            f"status={db_job.status.value}",
        )


async def test_orchestrator() -> None:
    print("\n─── 5. ORCHESTRATOR — complex task (live Claude API) ───")
    # Use the research request (higher complexity)
    tc = TEST_REQUESTS[1]
    c = tc.get("_classification")
    q = tc.get("_quote")
    if not c or not q:
        report("Orchestrator", False, "No classification/quote for complex task")
        return

    async with models.AsyncSessionLocal() as session:
        job = Job(
            client_platform="test",
            client_id="e2e-test-user",
            client_username="e2e_tester",
            raw_request=tc["text"],
            status=JobStatus.IN_PROGRESS,
            category=c.category,
            complexity=c.complexity,
            client_price=q.client_price,
            is_free_trial=False,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    orchestrator = Orchestrator()
    t0 = time.time()
    result = await orchestrator.fulfill(job_id)
    elapsed = time.time() - t0

    has_result = result is not None and len(result) > 100
    report(
        "Orchestrator: research task",
        has_result,
        f"{len(result) if result else 0} chars ({elapsed:.1f}s)",
    )

    # Verify DB
    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        report(
            "DB: orchestrated job status",
            db_job.status in (JobStatus.DELIVERED, JobStatus.COMPLETED),
            f"status={db_job.status.value}",
        )


async def test_full_lifecycle() -> None:
    print("\n─── 6. FULL LIFECYCLE — code request ───")
    tc = TEST_REQUESTS[2]
    c = tc.get("_classification")
    q = tc.get("_quote")
    if not c or not q:
        report("Full Lifecycle", False, "No classification/quote for code task")
        return

    direct = DirectFulfillment()
    delivery = DeliveryManager()

    # Create → In Progress → Fulfill → Deliver → Complete
    async with models.AsyncSessionLocal() as session:
        job = Job(
            client_platform="test",
            client_id="e2e-test-user",
            client_username="e2e_tester",
            raw_request=tc["text"],
            status=JobStatus.PENDING,
            category=c.category,
            complexity=c.complexity,
            client_price=q.client_price,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    # Simulate state transitions
    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        db_job.status = JobStatus.QUOTED
        await session.commit()

    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        db_job.status = JobStatus.ACCEPTED
        await session.commit()

    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        db_job.status = JobStatus.IN_PROGRESS
        await session.commit()

    t0 = time.time()
    result = await direct.fulfill(job_id)
    elapsed = time.time() - t0
    report(
        "Lifecycle: fulfill code request",
        result is not None and len(result) > 50,
        f"{len(result) if result else 0} chars ({elapsed:.1f}s)",
    )

    formatted = await delivery.format_for_telegram(job_id)
    report(
        "Lifecycle: format delivery",
        len(formatted) > 50,
        f"{len(formatted)} chars",
    )

    await delivery.mark_delivered_to_client(job_id)
    async with models.AsyncSessionLocal() as session:
        db_job = await session.get(Job, job_id)
        report(
            "Lifecycle: final status",
            db_job.status == JobStatus.COMPLETED,
            f"status={db_job.status.value}",
        )


# ── Main ─────────────────────────────────────────────────────────────

async def run_all() -> None:
    print("=" * 60)
    print("  DEMAND ENGINE — END-TO-END INTEGRATION TEST")
    print(f"  Model: {CLAUDE_MODEL}")
    print(f"  API Key: {'set' if ANTHROPIC_API_KEY else 'MISSING'}")
    print("=" * 60)

    if not ANTHROPIC_API_KEY:
        print(f"\n[{FAIL}] ANTHROPIC_API_KEY not set — cannot run e2e tests")
        sys.exit(1)

    # Use in-memory DB for test isolation
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import src.data.models as models

    test_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    test_session_factory = sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Monkey-patch the module-level objects AND all importing modules
    models.async_engine = test_engine
    models.AsyncSessionLocal = test_session_factory

    # Patch every module that imported AsyncSessionLocal at load time
    import src.execution.direct_fulfillment as mod_direct
    import src.execution.delivery as mod_delivery
    import src.execution.orchestrator as mod_orchestrator
    import src.intelligence.agent_matcher as mod_matcher
    mod_direct.AsyncSessionLocal = test_session_factory
    mod_delivery.AsyncSessionLocal = test_session_factory
    mod_orchestrator.AsyncSessionLocal = test_session_factory
    mod_matcher.AsyncSessionLocal = test_session_factory

    # Create all tables on the in-memory DB
    async with test_engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    classifier = TaskClassifier()
    pricer = PricingEngine()
    direct = DirectFulfillment()
    delivery = DeliveryManager()

    t_start = time.time()

    await test_classification(classifier)
    await test_pricing(pricer)
    await test_direct_fulfillment(direct)
    await test_delivery(delivery)
    await test_orchestrator()
    await test_full_lifecycle()

    total_time = time.time() - t_start

    # Summary
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed ({total_time:.1f}s total)")
    print("=" * 60)

    if failed:
        print(f"\n  Failed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name}: {detail}")
        sys.exit(1)
    else:
        print("\n  All tests passed!")


if __name__ == "__main__":
    asyncio.run(run_all())
