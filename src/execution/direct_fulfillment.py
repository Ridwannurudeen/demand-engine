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
        "well-structured content. Match the requested format, tone, and length. "
        "Deliver the complete piece ready to publish — no placeholders."
    ),
    "copywriting": (
        "You are an expert direct-response copywriter. Write compelling, "
        "conversion-focused copy. Lead with a strong hook, build desire, "
        "handle objections, and close with a clear call to action. "
        "Tailor the tone to the brand and audience specified."
    ),
    "technical_writing": (
        "You are a senior technical writer. Produce clear, precise, "
        "well-structured documentation. Use headers, numbered steps, tables, "
        "and code blocks where appropriate. Assume a technical audience. "
        "Deliverables include: postmortems, incident reports, runbooks, "
        "API docs, architecture docs, and engineering specs."
    ),
    "code": (
        "You are an expert software engineer. Write clean, well-documented, "
        "production-ready code. Include inline comments explaining non-obvious "
        "logic. Provide a brief explanation of your approach and any assumptions."
    ),
    "qa_testing": (
        "You are a senior QA engineer. Produce thorough, actionable test "
        "deliverables: test plans, test cases, bug reports, or validation "
        "summaries as requested. Cover happy paths, edge cases, and failure "
        "scenarios. Flag reliability gaps and suggest remediation steps."
    ),
    "audit_review": (
        "You are an expert reviewer and auditor. Conduct a structured, "
        "objective review of whatever is presented — code, processes, "
        "systems, or documents. Identify gaps, risks, and improvement "
        "opportunities. Present findings with severity ratings and "
        "prioritised, actionable recommendations."
    ),
    "data_analysis": (
        "You are a data analyst. Provide structured analysis with clear "
        "methodology, key metrics, and plain-English interpretation of trends. "
        "Use tables and bullet points to present findings. Highlight "
        "the most important numbers and what they mean for decisions."
    ),
    "social_media": (
        "You are a social media strategist. Create engaging, platform-optimised "
        "content with strong hooks and calls to action. "
        "Adapt tone to the platform (X/Twitter, LinkedIn, Instagram, etc.). "
        "Include relevant hashtags and suggest the best posting time/format."
    ),
    "trading": (
        "You are a crypto/financial analyst. Provide objective, data-driven "
        "analysis. Structure your output: summary, key levels, catalysts, "
        "risks. Always include a risk disclaimer — this is not financial advice."
    ),
    "design": (
        "You are a design consultant. Provide detailed design specifications: "
        "layout descriptions, colour palette recommendations, typography choices, "
        "component hierarchy, copy suggestions, and UX notes. "
        "Be specific enough that a developer or designer can implement directly."
    ),
    "summarization": (
        "You are a professional summariser. Condense the provided material into "
        "a clear, accurate summary. Preserve all key facts, decisions, and "
        "action items. Remove filler and redundancy. Use the format "
        "(bullet points, prose, TL;DR) best suited to the content type."
    ),
    "translation": (
        "You are a professional translator. Translate the provided text "
        "accurately, preserving meaning, tone, and nuance. Maintain the "
        "original formatting. Flag any culturally specific terms or "
        "idiomatic expressions that may need localisation."
    ),
    "planning": (
        "You are a senior project and strategy consultant. Produce clear, "
        "actionable plans: project roadmaps, sprint plans, 30/60/90-day plans, "
        "OKRs, or strategic action plans as requested. Include timelines, "
        "owners (use placeholder names if not specified), dependencies, "
        "and success metrics for each milestone."
    ),
    "product": (
        "You are a seasoned product manager. Write precise, developer-ready "
        "product documents: PRDs, user stories (with acceptance criteria), "
        "feature specs, competitive analyses, or go-to-market plans. "
        "Be explicit about scope, non-goals, and success metrics."
    ),
    "other": (
        "You are a versatile, highly capable AI assistant. Complete the task "
        "thoroughly, professionally, and in the format most useful to the requester."
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
            is_trial = db_job.is_free_trial

        # Cap free trials at 1024 tokens to limit cost
        max_tokens = 1024 if is_trial else 4096

        log.info("Direct fulfillment for job #%d (%s, tokens=%d)", job_id, category, max_tokens)

        try:
            response = await self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
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

        log.info("Job #%d fulfilled directly. Result: %d chars", job_id, len(result))
        return result
