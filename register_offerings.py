#!/usr/bin/env python3
"""Register DemandEngine job offerings in ACP marketplace."""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LITE_AGENT_API_KEY", "")
ACP_BASE = "https://claw-api.virtuals.io"

OFFERINGS = [
    {
        "name": "content_creation",
        "description": (
            "Professional AI content creation — blog posts, articles, Twitter threads, "
            "marketing copy, newsletters, whitepapers, email campaigns, ad copy. "
            "Specify topic, format, tone, and length. Delivered in plain text or markdown."
        ),
        "priceV2": {"type": "fixed", "value": 3},
        "slaMinutes": 30,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Full description of the content needed"},
                "format": {"type": "string", "description": "Output format (markdown, plain text, HTML)"},
            },
            "required": ["task"],
        },
        "deliverable": "Complete content piece in the requested format, ready to publish.",
    },
    {
        "name": "technical_writing",
        "description": (
            "Senior technical writer — postmortems, runbooks, incident reports, API docs, "
            "architecture docs, engineering specs, onboarding guides. Clear, structured, "
            "production-ready documentation with headers, tables, and code blocks."
        ),
        "priceV2": {"type": "fixed", "value": 4},
        "slaMinutes": 45,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Documentation task description"},
                "doc_type": {"type": "string", "description": "Type: postmortem, runbook, API_doc, spec, etc."},
            },
            "required": ["task"],
        },
        "deliverable": "Structured technical document in markdown format.",
    },
    {
        "name": "code_development",
        "description": (
            "Expert software engineer — write, debug, review, or refactor code in any language. "
            "Clean, documented, production-ready code with inline comments explaining key logic. "
            "Includes brief explanation of approach and assumptions."
        ),
        "priceV2": {"type": "fixed", "value": 5},
        "slaMinutes": 45,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Code task or problem description"},
                "language": {"type": "string", "description": "Programming language (optional)"},
            },
            "required": ["task"],
        },
        "deliverable": "Working code with explanation and usage notes.",
    },
    {
        "name": "data_analysis",
        "description": (
            "Data analyst — structured analysis with clear methodology, key metrics, and "
            "plain-English interpretation of trends. Tables, bullet points, and visualisation "
            "recommendations. Highlights the most important numbers and decision implications."
        ),
        "priceV2": {"type": "fixed", "value": 4},
        "slaMinutes": 45,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Data or analysis task description"},
                "data": {"type": "string", "description": "Data to analyse (inline or description)"},
            },
            "required": ["task"],
        },
        "deliverable": "Structured analysis report with key findings and recommendations.",
    },
    {
        "name": "audit_review",
        "description": (
            "Expert reviewer and auditor — structured, objective review of code, processes, "
            "systems, or documents. Identifies gaps, risks, and improvement opportunities. "
            "Presents findings with severity ratings and prioritised, actionable recommendations."
        ),
        "priceV2": {"type": "fixed", "value": 5},
        "slaMinutes": 60,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to audit/review and focus areas"},
                "content": {"type": "string", "description": "Content to review (code, doc, process description)"},
            },
            "required": ["task"],
        },
        "deliverable": "Audit report with findings, severity ratings, and remediation recommendations.",
    },
    {
        "name": "summarization",
        "description": (
            "Professional summariser — condenses documents, reports, meeting notes, threads, "
            "papers, or any text into clear, accurate summaries. Preserves all key facts, "
            "decisions, and action items. Uses the best format (bullets, prose, TL;DR) for the content."
        ),
        "priceV2": {"type": "fixed", "value": 2},
        "slaMinutes": 20,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to summarise and desired format"},
                "content": {"type": "string", "description": "Text to summarise"},
            },
            "required": ["task"],
        },
        "deliverable": "Concise, accurate summary in the requested format.",
    },
    {
        "name": "planning",
        "description": (
            "Senior project and strategy consultant — project roadmaps, sprint plans, "
            "30/60/90-day plans, OKRs, strategic action plans, go-to-market plans. "
            "Includes timelines, owners, dependencies, and success metrics."
        ),
        "priceV2": {"type": "fixed", "value": 4},
        "slaMinutes": 45,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Planning task and context"},
                "horizon": {"type": "string", "description": "Time horizon (e.g., 30-day, Q1, 6-month)"},
            },
            "required": ["task"],
        },
        "deliverable": "Structured plan with milestones, timelines, and success criteria.",
    },
    {
        "name": "quick_answer",
        "description": (
            "Instant AI answers — questions, definitions, explanations, calculations, "
            "translations, quick summaries, fact checks, short code snippets. "
            "Fast turnaround, any topic. The cheapest way to get a Claude-powered response."
        ),
        "priceV2": {"type": "fixed", "value": 0.01},
        "slaMinutes": 5,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Your question or request"},
            },
            "required": ["question"],
        },
        "deliverable": "Concise, accurate answer to your question.",
    },
    {
        "name": "general_task",
        "description": (
            "Versatile AI assistant — handle any task: writing, analysis, coding, research, "
            "translation, summarisation, copywriting, Q&A, brainstorming, product strategy, "
            "QA testing plans, and more. If Claude can do it, we can deliver it."
        ),
        "priceV2": {"type": "fixed", "value": 3},
        "slaMinutes": 60,
        "requiredFunds": False,
        "requirement": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Natural language description of the task"},
                "format": {"type": "string", "description": "Desired output format (optional)"},
            },
            "required": ["task"],
        },
        "deliverable": "Completed task output in the most useful format for the requester.",
    },
]


async def register_offering(client: httpx.AsyncClient, offering: dict) -> bool:
    try:
        resp = await client.post(
            f"{ACP_BASE}/acp/job-offerings",
            json={"data": offering},
            headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            print(f"  ✓ {offering['name']} (${offering['priceV2']['value']})")
            return True
        else:
            print(f"  ✗ {offering['name']}: HTTP {resp.status_code} — {resp.text[:150]}")
            return False
    except Exception as e:
        print(f"  ✗ {offering['name']}: {e}")
        return False


async def main() -> None:
    if not API_KEY:
        print("ERROR: LITE_AGENT_API_KEY not set")
        sys.exit(1)

    print(f"Registering {len(OFFERINGS)} offerings for DemandEngine...")
    print()

    ok = 0
    async with httpx.AsyncClient() as client:
        for offering in OFFERINGS:
            if await register_offering(client, offering):
                ok += 1

    print()
    print(f"Done: {ok}/{len(OFFERINGS)} offerings registered.")
    if ok < len(OFFERINGS):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
