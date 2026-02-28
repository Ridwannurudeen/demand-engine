"""Submit fulfilled deliverables back to the ACP endpoint."""
from __future__ import annotations

import logging

import httpx

from src.config import LITE_AGENT_API_KEY
from src.data.models import AsyncSessionLocal, Job
from src.notifications import notify_operator

log = logging.getLogger(__name__)

ACP_BASE_URL = "https://claw-api.virtuals.io"


class AGDPDelivery:
    """POSTs job results to the ACP deliverable endpoint."""

    async def deliver(self, job_id: int) -> bool:
        """Submit the fulfilled result to ACP. Returns True on success."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return False
            acp_job_id = db_job.acp_job_id
            result = db_job.result_data

        if not acp_job_id or not result:
            log.warning(
                "Job #%d: missing acp_job_id or result_data, skipping delivery",
                job_id,
            )
            return False

        if not LITE_AGENT_API_KEY:
            log.warning("LITE_AGENT_API_KEY not set — cannot deliver job #%d", job_id)
            return False

        url = f"{ACP_BASE_URL}/acp/providers/jobs/{acp_job_id}/deliverable"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={"deliverable": result},
                    headers={"x-api-key": LITE_AGENT_API_KEY},
                )
                resp.raise_for_status()
                log.info(
                    "Job #%d delivered to ACP (acp_job_id=%s)", job_id, acp_job_id
                )
                await notify_operator(
                    f"Deliverable submitted!\n"
                    f"Job #{job_id} (ACP: {acp_job_id})\n"
                    f"Result sent to aGDP — awaiting payment confirmation."
                )
                return True
        except httpx.HTTPStatusError as e:
            log.error(
                "ACP delivery failed for job #%d: HTTP %d — %s",
                job_id,
                e.response.status_code,
                e.response.text[:200],
            )
            return False
        except Exception as e:
            log.error("ACP delivery failed for job #%d: %s", job_id, e)
            return False
