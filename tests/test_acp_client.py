from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.execution.acp_client import ACPClient, ACPJobResult


@pytest.fixture
def client():
    return ACPClient()


@pytest.mark.asyncio
async def test_browse_agents_returns_list(client):
    mock_output = json.dumps([
        {"wallet": "0xabc", "name": "Agent1", "offering_id": "off1", "price": 10.0},
        {"wallet": "0xdef", "name": "Agent2", "offering_id": "off2", "price": 20.0},
    ])

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
        mock_exec.return_value = proc

        result = await client.browse_agents("content creation")

    assert len(result) == 2
    assert result[0]["name"] == "Agent1"


@pytest.mark.asyncio
async def test_create_job_returns_result(client):
    mock_output = json.dumps({"job_id": "job123", "status": "created"})

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
        mock_exec.return_value = proc

        result = await client.create_job("0xabc", "off1", {"request": "test"})

    assert isinstance(result, ACPJobResult)
    assert result.job_id == "job123"
    assert result.status == "created"


@pytest.mark.asyncio
async def test_cli_error_raises(client):
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"Command not found"))
        mock_exec.return_value = proc

        with pytest.raises(RuntimeError, match="ACP CLI failed"):
            await client.browse_agents("test")
