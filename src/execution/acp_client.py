from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from src.config import ACP_AGENT_WALLET, ACP_CLI_PATH

log = logging.getLogger(__name__)


@dataclass
class ACPJobResult:
    job_id: str
    status: str
    data: dict


class ACPClient:
    """Wrapper around the openclaw-acp CLI. Calls it via subprocess."""

    def __init__(self) -> None:
        self.cli_path = ACP_CLI_PATH
        self.wallet = ACP_AGENT_WALLET

    async def _run(self, *args: str, timeout: float = 60.0) -> dict:
        cmd = ["npx", "tsx", str(self.cli_path / "bin" / "acp.ts"), *args, "--json"]
        log.debug("ACP CLI: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"ACP CLI timed out after {timeout:.0f}s: {' '.join(cmd)}"
            )
        out = stdout.decode().strip()
        err = stderr.decode().strip()

        if proc.returncode != 0:
            log.error("ACP CLI error (rc=%d): %s", proc.returncode, err)
            raise RuntimeError(f"ACP CLI failed: {err}")

        # Try to parse as JSON; fall back to raw text
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"raw": out}

    async def browse_agents(self, query: str) -> list[dict]:
        result = await self._run("browse", query)
        if isinstance(result, dict) and "raw" in result:
            return []
        if isinstance(result, list):
            return result
        return result.get("agents", result.get("results", []))

    async def create_job(
        self,
        agent_wallet: str,
        offering_id: str,
        params: dict,
    ) -> ACPJobResult:
        result = await self._run(
            "job", "create",
            agent_wallet, offering_id,
            "--requirements", json.dumps(params),
        )
        return ACPJobResult(
            job_id=result.get("job_id", result.get("id", "")),
            status=result.get("status", "created"),
            data=result,
        )

    async def get_job_status(self, job_id: str) -> ACPJobResult:
        result = await self._run("job", "status", job_id)
        return ACPJobResult(
            job_id=job_id,
            status=result.get("status", "unknown"),
            data=result,
        )

    async def send_memo(self, job_id: str, message: str) -> dict:
        return await self._run("job", "memo", job_id, "--message", message)

    async def complete_job(self, job_id: str) -> dict:
        return await self._run("job", "complete", job_id)

    async def cancel_job(self, job_id: str) -> dict:
        return await self._run("job", "cancel", job_id)

    async def get_offerings(self, agent_wallet: str) -> list[dict]:
        result = await self._run("offerings", "--agent", agent_wallet)
        if isinstance(result, list):
            return result
        return result.get("offerings", [])
