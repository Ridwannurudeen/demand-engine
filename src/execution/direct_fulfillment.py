from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import anthropic

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from src.data.models import AsyncSessionLocal, Job, JobStatus, Revenue

log = logging.getLogger(__name__)

CATEGORY_PROMPTS = {
    "content_creation": (
        "You are a professional content writer. Produce high-quality, "
        "well-structured content. Match the requested format, tone, and length."
    ),
    "research": (
        "You are a senior research analyst. Produce detailed, data-driven "
        "analysis with clear sections, key findings, and actionable insights."
    ),
    "code": (
        "You are an expert software engineer. Write clean, well-documented, "
        "production-ready code. Include comments and explain your approach."
    ),
    "data_analysis": (
        "You are a data analyst. Provide structured analysis with clear "
        "methodology, key metrics, and visual descriptions of trends."
    ),
    "social_media": (
        "You are a social media strategist. Create engaging, platform-optimized "
        "content. Include hashtags, hooks, and calls to action."
    ),
    "trading": (
        "You are a crypto/financial analyst. Provide objective analysis based "
        "on available data. Include risk disclaimers. Not financial advice."
    ),
    "design": (
        "You are a design consultant. Provide detailed design specifications, "
        "copy suggestions, layout descriptions, and style recommendations."
    ),
    "other": (
        "You are a versatile AI assistant. Complete the task thoroughly "
        "and professionally."
    ),
}


class DirectFulfillment:
    """Fulfills jobs directly via Claude API instead of routing through ACP."""

    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def fulfill(self, job_id: int) -> str | None:
        """Execute a job directly and return the result text."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return None

            category = db_job.category.value if db_job.category else "other"
            system = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["other"])
            request = db_job.raw_request

        log.info("Direct fulfillment for job #%d (%s)", job_id, category)

        try:
            response = await self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": request}],
            )
            result = response.content[0].text
        except Exception as e:
            log.error("Direct fulfillment failed for job #%d: %s", job_id, e)
            async with AsyncSessionLocal() as session:
                db_job = await session.get(Job, job_id)
                db_job.status = JobStatus.FAILED
                db_job.result_summary = f"Fulfillment error: {e}"
                await session.commit()
            return None

        # Update job as delivered
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.status = JobStatus.DELIVERED
            db_job.result_data = result
            db_job.result_summary = f"Fulfilled directly ({category})"
            db_job.completed_at = datetime.now(timezone.utc)
            db_job.agent_price = 0.0  # no ACP agent cost
            db_job.margin = db_job.client_price or 0.0  # 100% margin

            # Record revenue
            if db_job.client_price:
                revenue = Revenue(
                    job_id=job_id,
                    client_paid=db_job.client_price,
                    agent_paid=0.0,
                    protocol_fee=0.0,
                    net_margin=db_job.client_price,
                )
                session.add(revenue)

            await session.commit()

        log.info("Job #%d fulfilled directly. Result: %d chars", job_id, len(result))
        return result
