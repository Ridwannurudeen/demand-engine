from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models import Agent, AsyncSessionLocal
from src.execution.acp_client import ACPClient
from src.intelligence.task_classifier import Classification

log = logging.getLogger(__name__)


@dataclass
class AgentCandidate:
    wallet_address: str
    name: str
    offering_id: str
    price: float
    relevance_score: float  # 0-1, how well this agent fits the task
    historical_score: float  # 0-1, past performance (0 if unknown)

    @property
    def combined_score(self) -> float:
        # Weight relevance more for new agents, historical more for known ones
        if self.historical_score > 0:
            return 0.4 * self.relevance_score + 0.6 * self.historical_score
        return self.relevance_score


class AgentMatcher:
    def __init__(self) -> None:
        self.acp = ACPClient()

    async def find_agents(
        self,
        classification: Classification,
        budget: float,
        max_results: int = 5,
    ) -> list[AgentCandidate]:
        # Search ACP registry for matching agents
        query = f"{classification.category} {classification.summary}"
        raw_agents = await self.acp.browse_agents(query)

        candidates: list[AgentCandidate] = []
        for agent_data in raw_agents[:max_results * 2]:
            price = agent_data.get("price", 0)
            if price > budget:
                continue

            wallet = agent_data.get("wallet", "")
            candidate = AgentCandidate(
                wallet_address=wallet,
                name=agent_data.get("name", "Unknown"),
                offering_id=agent_data.get("offering_id", ""),
                price=price,
                relevance_score=self._score_relevance(agent_data, classification),
                historical_score=await self._get_historical_score(wallet),
            )
            candidates.append(candidate)

        # Sort by combined score descending
        candidates.sort(key=lambda c: c.combined_score, reverse=True)
        result = candidates[:max_results]

        log.info("Found %d candidate agents for '%s' (budget $%.2f)",
                 len(result), classification.category, budget)
        return result

    def _score_relevance(
        self, agent_data: dict, classification: Classification
    ) -> float:
        score = 0.5  # base

        # Boost if agent categories overlap
        agent_cats = agent_data.get("categories", [])
        if isinstance(agent_cats, str):
            agent_cats = [agent_cats]
        if classification.category in agent_cats:
            score += 0.3

        # Boost if agent has description matching the summary
        desc = agent_data.get("description", "").lower()
        keywords = classification.summary.lower().split()
        matches = sum(1 for kw in keywords if kw in desc)
        score += min(0.2, matches * 0.05)

        return min(1.0, score)

    async def _get_historical_score(self, wallet: str) -> float:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Agent).where(Agent.wallet_address == wallet)
            )
            agent = result.scalar_one_or_none()
            if agent and agent.total_jobs > 0:
                return (agent.avg_quality + agent.success_rate) / 2.0
        return 0.0
