from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, TASK_CATEGORIES

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a task classification engine for an AI service marketplace.
Given a user's service request, you must output a JSON object with these fields:

{
  "category": one of """ + json.dumps(TASK_CATEGORIES) + """,
  "complexity": float 0.0-1.0 (0=trivial, 1=extremely complex),
  "estimated_hours": float (estimated hours to complete),
  "feasibility_score": float 0.0-1.0 (how likely an AI agent can do this well),
  "summary": "one-line summary of what the client wants",
  "rejection_reason": null or "string explaining why this cannot be fulfilled"
}

Reject requests that are:
- Illegal or unethical
- Physically impossible for AI
- Too vague to act on (feasibility_score < 0.2, ask for clarification instead)

Output ONLY valid JSON, no markdown fences or explanation."""


@dataclass
class Classification:
    category: str
    complexity: float
    estimated_hours: float
    feasibility_score: float
    summary: str
    rejection_reason: str | None = None

    @property
    def is_feasible(self) -> bool:
        return self.rejection_reason is None and self.feasibility_score >= 0.3


class TaskClassifier:
    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    async def classify(self, raw_request: str) -> Classification:
        log.info("Classifying request: %s", raw_request[:80])
        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_request}],
        )
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(text)
        return Classification(
            category=data["category"],
            complexity=data["complexity"],
            estimated_hours=data["estimated_hours"],
            feasibility_score=data["feasibility_score"],
            summary=data["summary"],
            rejection_reason=data.get("rejection_reason"),
        )
