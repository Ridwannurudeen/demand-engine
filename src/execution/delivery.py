from __future__ import annotations

import json
import logging

from sqlalchemy import select

from src.data.models import AsyncSessionLocal, Job, JobStatus

log = logging.getLogger(__name__)


class DeliveryManager:
    """Packages ACP agent results for client delivery."""

    async def get_deliverable(self, job_id: int) -> dict | None:
        """Retrieve the deliverable for a completed job."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return None
            if db_job.status not in (JobStatus.DELIVERED, JobStatus.COMPLETED):
                return None

            result = {
                "job_id": db_job.id,
                "status": db_job.status.value,
                "summary": db_job.result_summary or "No summary available",
                "category": db_job.category.value if db_job.category else "unknown",
            }

            # Parse result_data if it's JSON
            if db_job.result_data:
                try:
                    result["data"] = json.loads(db_job.result_data)
                except json.JSONDecodeError:
                    result["data"] = db_job.result_data

            return result

    async def format_for_telegram(self, job_id: int) -> str:
        """Format a deliverable as a Telegram message."""
        deliverable = await self.get_deliverable(job_id)
        if not deliverable:
            return "No results available yet."

        parts = [
            f"Job #{deliverable['job_id']} — {deliverable['status'].upper()}",
            "",
            deliverable["summary"],
        ]

        data = deliverable.get("data")
        if isinstance(data, str):
            # Truncate very long results for Telegram
            if len(data) > 3500:
                parts.append("")
                parts.append(data[:3500] + "\n\n[... truncated — full result available on request]")
            else:
                parts.append("")
                parts.append(data)
        elif isinstance(data, dict):
            # Format dict as readable text
            content = data.get("content", data.get("result", data.get("output", "")))
            if content:
                text = str(content)
                if len(text) > 3500:
                    text = text[:3500] + "\n\n[... truncated]"
                parts.append("")
                parts.append(text)

        return "\n".join(parts)

    async def mark_delivered_to_client(self, job_id: int) -> None:
        """Mark that the client has received the deliverable."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if db_job and db_job.status == JobStatus.DELIVERED:
                db_job.status = JobStatus.COMPLETED
                await session.commit()
        log.info("Job #%d delivered to client", job_id)
