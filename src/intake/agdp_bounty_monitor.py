"""aGDP.io Bounty Monitor - automatically claim and fulfill bounties."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from src.config import ACP_AGENT_WALLET
from src.data.models import AsyncSessionLocal, Job, JobStatus
from src.execution.orchestrator import Orchestrator
from src.intake.base import IntakeSource
from src.intelligence.agent_matcher import AgentMatcher
from src.intelligence.pricing_engine import PricingEngine
from src.intelligence.task_classifier import Classification, TaskClassifier

log = logging.getLogger(__name__)

AGDP_API_URL = "https://agdp.io/api/bounties"
POLL_INTERVAL = 60  # Poll every 60 seconds


class AGDPBountyMonitor(IntakeSource):
    """Monitor aGDP.io bounties and automatically claim matching jobs."""

    def __init__(self) -> None:
        self.classifier = TaskClassifier()
        self.pricer = PricingEngine()
        self.matcher = AgentMatcher()
        self.orchestrator = Orchestrator()
        self.running = False
        self._task: asyncio.Task | None = None
        self._seen_bounty_ids: set[int] = set()

    async def start(self) -> None:
        """Start monitoring bounties."""
        self.running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info("aGDP Bounty Monitor started (polling every %ds)", POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop monitoring."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("aGDP Bounty Monitor stopped")

    async def send_message(self, client_id: str, message: str) -> None:
        """No-op — aGDP bounty monitor has no back-channel to the poster."""
        pass

    async def _poll_loop(self) -> None:
        """Continuous polling loop."""
        while self.running:
            try:
                await self._check_bounties()
            except Exception as e:
                log.error("Error checking bounties: %s", e, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _check_bounties(self) -> None:
        """Fetch and process new bounties."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(AGDP_API_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("Failed to fetch bounties: %s", e)
            return

        bounties = data.get("data", [])
        new_bounties = [
            b for b in bounties
            if b["status"] == "pending_match" and b["id"] not in self._seen_bounty_ids
        ]

        if not new_bounties:
            return

        log.info("Found %d new bounties to process", len(new_bounties))

        for bounty in new_bounties:
            self._seen_bounty_ids.add(bounty["id"])
            await self._process_bounty(bounty)

    async def _process_bounty(self, bounty: dict[str, Any]) -> None:
        """Process a single bounty: classify, match, and claim if suitable."""
        bounty_id = bounty["id"]
        title = bounty["title"]
        description = bounty["description"]
        budget = bounty.get("budget", 0.0)
        category = bounty.get("category", "digital")
        tags = bounty.get("tags", "")

        # Pre-filter by budget before spending tokens on classification
        if budget < 0.5 or budget > 100.0:
            log.debug("Bounty #%d skipped (budget %.2f out of range)", bounty_id, budget)
            return

        log.info(
            "Processing bounty #%d: %s (budget: %.2f USDC)",
            bounty_id,
            title[:50],
            budget,
        )

        # Classify the task
        try:
            classification = await self.classifier.classify(description)
        except Exception as e:
            log.error("Failed to classify bounty #%d: %s", bounty_id, e)
            return

        # Check if we can fulfill it
        can_fulfill = await self._can_fulfill(classification, budget)
        if not can_fulfill:
            log.info(
                "Bounty #%d skipped (category: %s, feasibility: %.2f, budget: %.2f)",
                bounty_id,
                classification.category,
                classification.feasibility_score,
                budget,
            )
            return

        # Create job in our database
        async with AsyncSessionLocal() as session:
            job = Job(
                client_platform="agdp",
                client_id=bounty.get("poster_wallet_address", "unknown"),
                client_username=bounty.get("poster_name"),
                raw_request=description,
                category=classification.category,
                complexity=classification.complexity,
                client_price=budget,
                is_free_trial=False,
                status=JobStatus.CLASSIFIED,
                result_summary=f"Bounty #{bounty_id}: {title}",
                acp_job_id=str(bounty_id),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        log.info(
            "Bounty #%d → Job #%d created (category: %s, price: %.2f USDC)",
            bounty_id,
            job_id,
            classification.category,
            budget,
        )

        # Claim bounty on aGDP.io
        claimed = await self._claim_bounty(bounty_id, job_id)
        if not claimed:
            log.warning("Failed to claim bounty #%d", bounty_id)
            return

        # Execute the job
        try:
            await self.orchestrator.execute_job(job_id)
            log.info("Bounty #%d (Job #%d) execution started", bounty_id, job_id)
        except Exception as e:
            log.error(
                "Failed to execute bounty #%d (Job #%d): %s",
                bounty_id,
                job_id,
                e,
                exc_info=True,
            )

    async def _can_fulfill(self, classification: Classification, budget: float) -> bool:
        """Check if we can fulfill this bounty."""
        # Skip if feasibility too low
        if classification.feasibility_score < 0.6:
            return False

        # Check if we have matching ACP agents
        try:
            matches = await self.matcher.find_agents(
                classification,
                budget=budget,
                max_results=3,
            )
            return len(matches) > 0
        except Exception:
            # If matcher fails, still try if it's a simple category
            return classification.category in [
                "content_creation",
                "research",
                "code",
                "data_analysis",
            ]

    async def _claim_bounty(self, bounty_id: int, job_id: int) -> bool:
        """Claim a bounty on aGDP.io."""
        claim_url = f"https://agdp.io/api/bounties/{bounty_id}/claim"
        
        payload = {
            "agent_wallet": ACP_AGENT_WALLET,
            "agent_name": "Demand Engine",
            "callback_url": f"http://75.119.153.252:8080/agdp-callback/{job_id}",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(claim_url, json=payload)
                resp.raise_for_status()
                log.info("Successfully claimed bounty #%d", bounty_id)
                return True
        except httpx.HTTPStatusError as e:
            log.error(
                "Failed to claim bounty #%d: HTTP %d - %s",
                bounty_id,
                e.response.status_code,
                e.response.text,
            )
            return False
        except Exception as e:
            log.error("Failed to claim bounty #%d: %s", bounty_id, e)
            return False
