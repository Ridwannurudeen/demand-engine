from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.data.models import AsyncSessionLocal, Job, JobStatus, Revenue
from src.execution.direct_fulfillment import CATEGORY_PROMPTS

log = logging.getLogger(__name__)


@dataclass
class SubTask:
    title: str
    description: str
    category: str


class Orchestrator:
    """Breaks complex requests into sub-tasks, fulfills in parallel, assembles results."""

    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def execute_job(self, job_id: int) -> None:
        """Schedule job fulfillment as a non-blocking background task."""
        asyncio.create_task(self._run_and_log(job_id))

    async def _run_and_log(self, job_id: int) -> None:
        try:
            await self.fulfill(job_id)
        except Exception as e:
            log.error(
                "Background fulfillment failed for job #%d: %s",
                job_id, e, exc_info=True,
            )

    async def decompose(self, request: str) -> list[SubTask]:
        """Use Claude to break a request into sub-tasks."""
        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=(
                "You are a project manager. Break the user's request into 2-5 "
                "independent sub-tasks that can be worked on in parallel. "
                "Respond with a JSON array of objects with keys: "
                "title, description, category. "
                "Categories: content_creation, research, code, data_analysis, "
                "social_media, trading, design, other. "
                "Only output the JSON array, nothing else."
            ),
            messages=[{"role": "user", "content": request}],
        )
        text = response.content[0].text.strip()
        # Handle markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        tasks = json.loads(text)
        return [SubTask(**t) for t in tasks]

    async def _fulfill_subtask(self, subtask: SubTask) -> str:
        """Fulfill a single sub-task via Claude."""
        system = CATEGORY_PROMPTS.get(subtask.category, CATEGORY_PROMPTS["other"])
        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": subtask.description}],
        )
        return response.content[0].text

    async def _assemble(self, request: str, results: list[tuple[str, str]]) -> str:
        """Assemble sub-task results into a cohesive deliverable."""
        parts = "\n\n".join(
            f"=== {title} ===\n{result}" for title, result in results
        )
        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=(
                "You are an editor. The user made a request and multiple specialists "
                "produced separate parts. Combine them into one polished, cohesive "
                "deliverable. Remove redundancy, ensure flow, and maintain quality."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Original request: {request}\n\n"
                        f"Sub-task results:\n\n{parts}\n\n"
                        "Please combine into one cohesive document."
                    ),
                }
            ],
        )
        return response.content[0].text

    async def fulfill(self, job_id: int) -> str | None:
        """Orchestrate a complex job: decompose, fulfill in parallel, assemble."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return None
            request = db_job.raw_request

        log.info("Orchestrating job #%d — decomposing...", job_id)

        try:
            subtasks = await self.decompose(request)
        except Exception as e:
            log.error("Decomposition failed for job #%d: %s", job_id, e)
            return None

        log.info(
            "Job #%d decomposed into %d sub-tasks: %s",
            job_id,
            len(subtasks),
            [s.title for s in subtasks],
        )

        # Fulfill all sub-tasks in parallel
        try:
            coros = [self._fulfill_subtask(st) for st in subtasks]
            results_raw = await asyncio.gather(*coros, return_exceptions=True)
        except Exception as e:
            log.error("Parallel fulfillment failed for job #%d: %s", job_id, e)
            return None

        results = []
        for st, res in zip(subtasks, results_raw):
            if isinstance(res, Exception):
                log.error("Sub-task '%s' failed: %s", st.title, res)
                results.append((st.title, f"[Failed: {res}]"))
            else:
                results.append((st.title, res))

        # Assemble into final deliverable
        log.info("Job #%d — assembling %d results...", job_id, len(results))
        try:
            final = await self._assemble(request, results)
        except Exception as e:
            log.error("Assembly failed for job #%d: %s", job_id, e)
            # Fall back to concatenated results
            final = "\n\n---\n\n".join(
                f"## {title}\n\n{result}" for title, result in results
            )

        # Update job as delivered
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.status = JobStatus.DELIVERED
            db_job.result_data = final
            db_job.result_summary = (
                f"Orchestrated: {len(subtasks)} sub-tasks"
            )
            db_job.completed_at = datetime.now(timezone.utc)
            db_job.agent_price = 0.0
            db_job.margin = db_job.client_price or 0.0

            if db_job.client_price is not None:
                revenue = Revenue(
                    job_id=job_id,
                    client_paid=db_job.client_price,
                    agent_paid=0.0,
                    protocol_fee=0.0,
                    net_margin=db_job.client_price,
                )
                session.add(revenue)

            await session.commit()

        log.info("Job #%d orchestrated. Final: %d chars", job_id, len(final))
        return final
