"""Monitor top agents on aGDP.io and extract winning patterns."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from typing import Any

import httpx

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)

AGDP_API_URL = "https://agdp.io/api/bounties"
ANALYSIS_INTERVAL = 3600  # re-analyse every hour

# Statuses that count as "successfully completed" on aGDP
DONE_STATUSES = {"fulfilled", "completed", "paid", "accepted"}


class CompetitorMonitor:
    """Fetches aGDP.io bounty data and analyses top-agent success patterns."""

    def __init__(self) -> None:
        import anthropic
        self._ai = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self.running = False
        self._task: asyncio.Task | None = None
        self._last_analysis: str = "No analysis run yet — use /topage to trigger one."

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        self.running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Competitor monitor started (interval: %ds)", ANALYSIS_INTERVAL)

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self.running:
            try:
                self._last_analysis = await self._analyse()
                log.info("Competitor analysis updated")
            except Exception as e:
                log.error("Competitor analysis error: %s", e)
            await asyncio.sleep(ANALYSIS_INTERVAL)

    # ── Public API ───────────────────────────────────────────────────

    async def get_analysis(self) -> str:
        return self._last_analysis

    async def run_now(self) -> str:
        """Force a fresh analysis and cache + return the result."""
        self._last_analysis = await self._analyse()
        return self._last_analysis

    # ── Core logic ───────────────────────────────────────────────────

    async def _fetch_bounties(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(AGDP_API_URL)
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", [])

    async def _analyse(self) -> str:
        bounties = await self._fetch_bounties()
        total = len(bounties)

        fulfilled = [
            b for b in bounties
            if str(b.get("status", "")).lower() in DONE_STATUSES
        ]

        if not fulfilled:
            pending = [b for b in bounties if b.get("status") == "pending_match"]
            return (
                f"aGDP snapshot: {total} total bounties, 0 completed.\n"
                f"({len(pending)} still pending match)\n\n"
                "No completed bounties to analyse yet — check back later."
            )

        # ── Aggregate by agent ──────────────────────────────────────
        completions: Counter[str] = Counter()
        categories: dict[str, Counter[str]] = defaultdict(Counter)
        budgets: dict[str, list[float]] = defaultdict(list)
        titles: dict[str, list[str]] = defaultdict(list)
        display_names: dict[str, str] = {}

        for b in fulfilled:
            # Try every possible field name for the agent identifier
            wallet = (
                b.get("claimant_wallet")
                or b.get("agent_wallet")
                or b.get("claimed_by")
                or b.get("fulfiller_wallet")
                or "unknown"
            )
            name = (
                b.get("claimant_name")
                or b.get("agent_name")
                or b.get("fulfiller_name")
                or wallet[:12]
            )
            category = b.get("category", "unknown")
            budget = float(b.get("budget") or 0.0)
            title = b.get("title", "")

            completions[wallet] += 1
            categories[wallet][category] += 1
            budgets[wallet].append(budget)
            if len(titles[wallet]) < 5:
                titles[wallet].append(title)
            display_names[wallet] = name

        top5 = completions.most_common(5)

        # ── Build the raw data block for Claude ─────────────────────
        lines: list[str] = [
            f"aGDP.io snapshot: {total} bounties total, {len(fulfilled)} completed.\n"
        ]
        for rank, (wallet, count) in enumerate(top5, 1):
            name = display_names.get(wallet, wallet[:12])
            top_cats = dict(categories[wallet].most_common(3))
            avg_budget = (
                sum(budgets[wallet]) / len(budgets[wallet]) if budgets[wallet] else 0
            )
            sample = titles[wallet][:3]
            lines.append(
                f"#{rank} Agent: {name}\n"
                f"  Completed: {count} bounties\n"
                f"  Top categories: {top_cats}\n"
                f"  Avg budget: ${avg_budget:.2f} USDC\n"
                f"  Sample jobs: {'; '.join(sample) or 'n/a'}"
            )

        raw = "\n\n".join(lines)

        # ── Ask Claude for actionable insights ──────────────────────
        response = await self._ai.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            system=(
                "You are a competitive intelligence analyst for an AI agent marketplace. "
                "Given data on top-performing agents, identify 4-5 specific, actionable "
                "patterns behind their success. Focus on: which categories they dominate, "
                "the job types they accept, price ranges they target, and what we can copy "
                "or do better. Be brief, direct, and practical — bullet points only."
            ),
            messages=[{"role": "user", "content": raw}],
        )
        insight = response.content[0].text.strip()
        return f"{raw}\n\n--- Insights ---\n{insight}"
