from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import anthropic
from sqlalchemy import select

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.data.models import (
    Agent,
    AgentScore,
    AsyncSessionLocal,
    Job,
    JobStatus,
    Revenue,
)
from src.execution.acp_client import ACPClient
from src.intelligence.agent_matcher import AgentCandidate

log = logging.getLogger(__name__)

EVAL_SYSTEM = """\
You are evaluating the output of an AI agent that was hired to complete a task.
Given the original request and the agent's deliverable, score the result:

{
  "quality_score": float 0.0-1.0,
  "meets_requirements": boolean,
  "summary": "brief assessment",
  "issues": ["list of issues if any"]
}

Output ONLY valid JSON."""


class JobManager:
    def __init__(
        self,
        on_complete: Callable[[int], Awaitable[None]] | None = None,
        on_failure: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self.acp = ACPClient()
        self.claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._poll_tasks: dict[int, asyncio.Task] = {}
        self.on_complete = on_complete
        self.on_failure = on_failure

    async def create_acp_job(
        self, job: Job, candidate: AgentCandidate
    ) -> str:
        """Create a job on ACP and update the database record."""
        params = {}
        if job.requirements_json:
            params = json.loads(job.requirements_json)
        params["request"] = job.raw_request

        result = await self.acp.create_job(
            agent_wallet=candidate.wallet_address,
            offering_id=candidate.offering_id,
            params=params,
        )

        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job.id)
            db_job.acp_job_id = result.job_id
            db_job.acp_agent_wallet = candidate.wallet_address
            db_job.acp_offering_id = candidate.offering_id
            db_job.agent_price = candidate.price
            db_job.margin = (db_job.client_price or 0) - candidate.price
            db_job.status = JobStatus.ACP_CREATED
            await session.commit()

        log.info("ACP job created: %s for job #%d", result.job_id, job.id)
        return result.job_id

    async def start_monitoring(self, job_id: int, acp_job_id: str) -> None:
        """Start polling an ACP job for completion."""
        task = asyncio.create_task(self._poll_job(job_id, acp_job_id))
        self._poll_tasks[job_id] = task

    async def _poll_job(
        self, job_id: int, acp_job_id: str, interval: float = 30.0
    ) -> None:
        """Poll ACP job status until completion or failure."""
        log.info("Monitoring ACP job %s (job #%d)", acp_job_id, job_id)
        while True:
            try:
                result = await self.acp.get_job_status(acp_job_id)
                status = result.status.lower()

                if status in ("completed", "delivered"):
                    await self._handle_completion(job_id, result.data)
                    break
                elif status in ("failed", "cancelled", "rejected"):
                    await self._handle_failure(job_id, status, result.data)
                    break
                else:
                    async with AsyncSessionLocal() as session:
                        db_job = await session.get(Job, job_id)
                        if db_job and db_job.status != JobStatus.IN_PROGRESS:
                            db_job.status = JobStatus.IN_PROGRESS
                            await session.commit()

            except Exception as e:
                log.error("Error polling job #%d: %s", job_id, e)

            await asyncio.sleep(interval)

        self._poll_tasks.pop(job_id, None)

    async def _handle_completion(self, job_id: int, data: dict) -> None:
        deliverable = data.get("deliverable", data.get("result", ""))
        if isinstance(deliverable, dict):
            deliverable_text = json.dumps(deliverable)
        else:
            deliverable_text = str(deliverable)

        # Evaluate quality via Claude
        evaluation = await self._evaluate_deliverable(job_id, deliverable_text)

        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.status = JobStatus.DELIVERED
            db_job.result_data = deliverable_text
            db_job.result_summary = evaluation.get("summary", "Completed")
            db_job.completed_at = datetime.now(timezone.utc)
            await session.commit()

            # Record agent score
            agent_result = await session.execute(
                select(Agent).where(
                    Agent.wallet_address == db_job.acp_agent_wallet
                )
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                agent = Agent(
                    wallet_address=db_job.acp_agent_wallet,
                    name=db_job.acp_agent_wallet[:16],
                )
                session.add(agent)
                await session.flush()

            # Calculate speed_score from actual elapsed time
            estimated_hours = 8.0  # fallback cap
            if db_job.created_at and db_job.completed_at:
                elapsed = (db_job.completed_at - db_job.created_at).total_seconds()
                speed_score = max(0.0, min(1.0, 1.0 - (elapsed / (estimated_hours * 3600))))
            else:
                speed_score = 0.5

            score = AgentScore(
                job_id=job_id,
                agent_id=agent.id,
                quality_score=evaluation.get("quality_score", 0.5),
                speed_score=speed_score,
            )
            session.add(score)

            # Update agent averages
            agent.total_jobs += 1
            n = agent.total_jobs
            agent.avg_quality = (
                (agent.avg_quality * (n - 1) + score.quality_score) / n
            )
            agent.avg_speed = (
                (agent.avg_speed * (n - 1) + score.speed_score) / n
            )
            agent.success_rate = (
                (agent.success_rate * (n - 1) + 1.0) / n
            )
            agent.last_used = datetime.now(timezone.utc)

            # Record revenue
            if db_job.client_price and db_job.agent_price:
                protocol_fee = db_job.agent_price * 0.20
                revenue = Revenue(
                    job_id=job_id,
                    client_paid=db_job.client_price,
                    agent_paid=db_job.agent_price,
                    protocol_fee=protocol_fee,
                    net_margin=db_job.client_price - db_job.agent_price - protocol_fee,
                )
                session.add(revenue)

            await session.commit()

        log.info("Job #%d completed. Quality: %.2f",
                 job_id, evaluation.get("quality_score", 0))

        if self.on_complete:
            await self.on_complete(job_id)

    async def _handle_failure(
        self, job_id: int, status: str, data: dict
    ) -> None:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.status = JobStatus.FAILED
            db_job.result_summary = f"ACP job {status}: {data.get('reason', 'unknown')}"
            await session.commit()
        log.warning("Job #%d failed: %s", job_id, status)

        if self.on_failure:
            await self.on_failure(job_id)

    async def _evaluate_deliverable(
        self, job_id: int, deliverable: str
    ) -> dict:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            request = db_job.raw_request if db_job else ""

        prompt = (
            f"Original request:\n{request}\n\n"
            f"Agent deliverable:\n{deliverable[:3000]}"
        )

        try:
            response = await self.claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=512,
                system=EVAL_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text)
        except Exception as e:
            log.error("Evaluation failed for job #%d: %s", job_id, e)
            return {"quality_score": 0.5, "summary": "Evaluation unavailable"}

    async def complete_and_pay(self, job_id: int) -> None:
        """Mark job as completed on ACP (releases escrow)."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job or not db_job.acp_job_id:
                return
            await self.acp.complete_job(db_job.acp_job_id)
            db_job.status = JobStatus.COMPLETED
            await session.commit()
        log.info("Job #%d completed and paid", job_id)
