from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.intelligence.task_classifier import Classification, TaskClassifier


@pytest.fixture
def classifier():
    return TaskClassifier()


def _mock_response(data: dict) -> MagicMock:
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    response = MagicMock()
    response.content = [content_block]
    return response


VALID_CLASSIFICATION = {
    "category": "content_creation",
    "complexity": 0.4,
    "estimated_hours": 2.0,
    "feasibility_score": 0.85,
    "summary": "Write a blog post about DeFi trends",
    "rejection_reason": None,
}


@pytest.mark.asyncio
async def test_classify_valid_request(classifier):
    with patch.object(
        classifier.client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=_mock_response(VALID_CLASSIFICATION),
    ):
        result = await classifier.classify("Write a 1000-word blog post about DeFi trends")

    assert isinstance(result, Classification)
    assert result.category == "content_creation"
    assert result.is_feasible
    assert result.complexity == 0.4
    assert result.estimated_hours == 2.0


@pytest.mark.asyncio
async def test_classify_rejected_request(classifier):
    data = {
        **VALID_CLASSIFICATION,
        "feasibility_score": 0.1,
        "rejection_reason": "Request is too vague",
    }
    with patch.object(
        classifier.client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=_mock_response(data),
    ):
        result = await classifier.classify("Do something")

    assert not result.is_feasible
    assert result.rejection_reason == "Request is too vague"


@pytest.mark.asyncio
async def test_classify_handles_markdown_fences(classifier):
    content_block = MagicMock()
    content_block.text = "```json\n" + json.dumps(VALID_CLASSIFICATION) + "\n```"
    response = MagicMock()
    response.content = [content_block]

    with patch.object(
        classifier.client.messages,
        "create",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await classifier.classify("Write a blog post")

    assert result.category == "content_creation"
