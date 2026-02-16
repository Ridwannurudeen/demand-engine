from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.data.models import (
    Agent,
    AsyncSessionLocal,
    Job,
    JobStatus,
    Revenue,
)

log = logging.getLogger(__name__)


@dataclass
class PerformanceSnapshot:
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    completion_rate: float
    total_revenue: float
    total_margin: float
    avg_job_value: float
    top_category: str | None
    period: str


@dataclass
class AgentLeaderboard:
    wallet: str
    name: str
    total_jobs: int
    avg_quality: float
    success_rate: float


class Tracker:
    async def get_performance(
        self, days: int = 7
    ) -> PerformanceSnapshot:
        since = datetime.now(timezone.utc) - timedelta(days=days)

        async with AsyncSessionLocal() as session:
            # Job counts
            total = await session.scalar(
                select(func.count(Job.id)).where(Job.created_at >= since)
            ) or 0
            completed = await session.scalar(
                select(func.count(Job.id)).where(
                    Job.created_at >= since,
                    Job.status == JobStatus.COMPLETED,
                )
            ) or 0
            failed = await session.scalar(
                select(func.count(Job.id)).where(
                    Job.created_at >= since,
                    Job.status == JobStatus.FAILED,
                )
            ) or 0

            # Revenue
            rev_result = await session.execute(
                select(
                    func.coalesce(func.sum(Revenue.client_paid), 0),
                    func.coalesce(func.sum(Revenue.net_margin), 0),
                ).where(Revenue.created_at >= since)
            )
            row = rev_result.one()
            total_revenue = float(row[0])
            total_margin = float(row[1])

            # Top category
            cat_result = await session.execute(
                select(Job.category, func.count(Job.id).label("cnt"))
                .where(Job.created_at >= since, Job.category.isnot(None))
                .group_by(Job.category)
                .order_by(func.count(Job.id).desc())
                .limit(1)
            )
            cat_row = cat_result.first()
            top_category = cat_row[0].value if cat_row else None

        return PerformanceSnapshot(
            total_jobs=total,
            completed_jobs=completed,
            failed_jobs=failed,
            completion_rate=completed / total if total > 0 else 0.0,
            total_revenue=total_revenue,
            total_margin=total_margin,
            avg_job_value=total_revenue / completed if completed > 0 else 0.0,
            top_category=top_category,
            period=f"Last {days} days",
        )

    async def get_top_agents(self, limit: int = 10) -> list[AgentLeaderboard]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Agent)
                .where(Agent.total_jobs > 0)
                .order_by(Agent.avg_quality.desc())
                .limit(limit)
            )
            agents = result.scalars().all()

        return [
            AgentLeaderboard(
                wallet=a.wallet_address,
                name=a.name or a.wallet_address[:16],
                total_jobs=a.total_jobs,
                avg_quality=a.avg_quality,
                success_rate=a.success_rate,
            )
            for a in agents
        ]

    async def format_dashboard(self, days: int = 7) -> str:
        perf = await self.get_performance(days)
        top = await self.get_top_agents(5)

        lines = [
            f"=== Demand Engine Dashboard ({perf.period}) ===",
            "",
            f"Jobs: {perf.total_jobs} total, {perf.completed_jobs} completed, {perf.failed_jobs} failed",
            f"Completion rate: {perf.completion_rate:.0%}",
            f"Revenue: ${perf.total_revenue:.2f}",
            f"Margin: ${perf.total_margin:.2f}",
            f"Avg job value: ${perf.avg_job_value:.2f}",
            f"Top category: {perf.top_category or 'N/A'}",
        ]

        if top:
            lines.append("")
            lines.append("Top Agents:")
            for a in top:
                lines.append(
                    f"  {a.name} — {a.total_jobs} jobs, "
                    f"quality {a.avg_quality:.0%}, success {a.success_rate:.0%}"
                )

        return "\n".join(lines)
