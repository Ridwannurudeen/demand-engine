"""Tests for the critical gap fixes (Fixes 1-8)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.data.models import Base, Job, JobStatus, Revenue, has_used_free_trial


# ── Helpers ───────────────────────────────────────────────────────────────


async def _setup_test_db():
    """Create an in-memory SQLite database and patch AsyncSessionLocal."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory


# ── Fix 1: PaymentMonitor validates wallet ────────────────────────────────


class TestPaymentMonitorValidation:
    def test_empty_wallet_raises(self):
        with patch("src.execution.payment.ACP_AGENT_WALLET", ""):
            from src.execution.payment import PaymentMonitor

            with pytest.raises(ValueError, match="ACP_AGENT_WALLET is empty or invalid"):
                PaymentMonitor()

    def test_invalid_wallet_raises(self):
        with patch("src.execution.payment.ACP_AGENT_WALLET", "not-a-wallet"):
            from src.execution.payment import PaymentMonitor

            with pytest.raises(ValueError, match="ACP_AGENT_WALLET is empty or invalid"):
                PaymentMonitor()

    def test_valid_wallet_succeeds(self):
        with patch("src.execution.payment.ACP_AGENT_WALLET", "0xAbC123"):
            from src.execution.payment import PaymentMonitor

            monitor = PaymentMonitor()
            assert monitor.wallet == "0xabc123"


# ── Fix 2: Basescan API key included in requests ─────────────────────────


class TestBasescanApiKey:
    @pytest.mark.asyncio
    async def test_apikey_included_when_set(self):
        with (
            patch("src.execution.payment.ACP_AGENT_WALLET", "0xAbC123"),
            patch("src.execution.payment.BASESCAN_API_KEY", "test-key-123"),
        ):
            from src.execution.payment import PaymentMonitor

            monitor = PaymentMonitor()

            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "1", "result": []}

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch("httpx.AsyncClient", return_value=mock_client):
                await monitor._get_recent_transfers()

            # Verify apikey was in the params
            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert params["apikey"] == "test-key-123"

    @pytest.mark.asyncio
    async def test_apikey_omitted_when_empty(self):
        with (
            patch("src.execution.payment.ACP_AGENT_WALLET", "0xAbC123"),
            patch("src.execution.payment.BASESCAN_API_KEY", ""),
        ):
            from src.execution.payment import PaymentMonitor

            monitor = PaymentMonitor()

            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "1", "result": []}

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)

            with patch("httpx.AsyncClient", return_value=mock_client):
                await monitor._get_recent_transfers()

            call_kwargs = mock_client.get.call_args
            params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
            assert "apikey" not in params


# ── Fix 3: ACP CLI timeout ───────────────────────────────────────────────


class TestACPClientTimeout:
    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self):
        from src.execution.acp_client import ACPClient

        client = ACPClient()

        proc = AsyncMock()
        killed = False

        async def _hang():
            await asyncio.sleep(999)
            return (b"", b"")

        def _kill():
            nonlocal killed
            killed = True

        proc.communicate = _hang
        proc.kill = _kill
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = proc

            with pytest.raises(RuntimeError, match="ACP CLI timed out"):
                await client._run("test", timeout=0.1)

            assert killed, "subprocess.kill() was not called"

    @pytest.mark.asyncio
    async def test_normal_execution_within_timeout(self):
        from src.execution.acp_client import ACPClient

        client = ACPClient()
        mock_output = json.dumps({"status": "ok"})

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
            mock_exec.return_value = proc

            result = await client._run("test", timeout=10.0)
            assert result == {"status": "ok"}


# ── Fix 5: has_used_free_trial scoped by platform ────────────────────────


class TestFreeTrialPlatformScope:
    @pytest.mark.asyncio
    async def test_different_platforms_independent(self):
        engine, session_factory = await _setup_test_db()

        with patch("src.data.models.AsyncSessionLocal", session_factory):
            # Create a Telegram free trial job
            async with session_factory() as session:
                job = Job(
                    client_platform="telegram",
                    client_id="12345",
                    raw_request="test request",
                    status=JobStatus.COMPLETED,
                    is_free_trial=True,
                )
                session.add(job)
                await session.commit()

            # Telegram user 12345 has used trial
            assert await has_used_free_trial("12345", platform="telegram") is True
            # Twitter user 12345 has NOT used trial
            assert await has_used_free_trial("12345", platform="twitter") is False

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_same_platform_detected(self):
        engine, session_factory = await _setup_test_db()

        with patch("src.data.models.AsyncSessionLocal", session_factory):
            async with session_factory() as session:
                job = Job(
                    client_platform="twitter",
                    client_id="67890",
                    raw_request="test",
                    status=JobStatus.COMPLETED,
                    is_free_trial=True,
                )
                session.add(job)
                await session.commit()

            assert await has_used_free_trial("67890", platform="twitter") is True

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_no_platform_checks_all(self):
        """Without platform filter, any trial for that client_id is detected."""
        engine, session_factory = await _setup_test_db()

        with patch("src.data.models.AsyncSessionLocal", session_factory):
            async with session_factory() as session:
                job = Job(
                    client_platform="telegram",
                    client_id="11111",
                    raw_request="test",
                    status=JobStatus.COMPLETED,
                    is_free_trial=True,
                )
                session.add(job)
                await session.commit()

            # No platform filter — should still find it
            assert await has_used_free_trial("11111") is True

        await engine.dispose()


# ── Fix 6: Free trials create Revenue records ────────────────────────────


class TestFreeTrialRevenue:
    @pytest.mark.asyncio
    async def test_zero_price_creates_revenue(self):
        engine, session_factory = await _setup_test_db()

        with patch("src.data.models.AsyncSessionLocal", session_factory):
            # Create a free trial job with client_price=0.0
            async with session_factory() as session:
                job = Job(
                    client_platform="telegram",
                    client_id="99999",
                    raw_request="Write a haiku about testing",
                    status=JobStatus.IN_PROGRESS,
                    category="content_creation",
                    is_free_trial=True,
                    client_price=0.0,
                )
                session.add(job)
                await session.commit()
                job_id = job.id

            # Mock the Anthropic API call
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Tests pass in silence\nGreen checkmarks bloom like spring flowers\nCode works perfectly")]

            with patch("src.execution.direct_fulfillment.AsyncSessionLocal", session_factory):
                from src.execution.direct_fulfillment import DirectFulfillment

                df = DirectFulfillment()
                df.client = AsyncMock()
                df.client.messages.create = AsyncMock(return_value=mock_response)

                result = await df.fulfill(job_id)

            assert result is not None

            # Verify Revenue record was created
            async with session_factory() as session:
                rev_result = await session.execute(
                    select(Revenue).where(Revenue.job_id == job_id)
                )
                revenue = rev_result.scalar_one_or_none()

            assert revenue is not None
            assert revenue.client_paid == 0.0
            assert revenue.net_margin == 0.0

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_none_price_no_revenue(self):
        """When client_price is None (not yet quoted), no Revenue record."""
        engine, session_factory = await _setup_test_db()

        with patch("src.data.models.AsyncSessionLocal", session_factory):
            async with session_factory() as session:
                job = Job(
                    client_platform="telegram",
                    client_id="88888",
                    raw_request="Test request",
                    status=JobStatus.IN_PROGRESS,
                    category="other",
                    client_price=None,
                )
                session.add(job)
                await session.commit()
                job_id = job.id

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Result")]

            with patch("src.execution.direct_fulfillment.AsyncSessionLocal", session_factory):
                from src.execution.direct_fulfillment import DirectFulfillment

                df = DirectFulfillment()
                df.client = AsyncMock()
                df.client.messages.create = AsyncMock(return_value=mock_response)

                await df.fulfill(job_id)

            async with session_factory() as session:
                rev_result = await session.execute(
                    select(Revenue).where(Revenue.job_id == job_id)
                )
                revenue = rev_result.scalar_one_or_none()

            assert revenue is None

        await engine.dispose()


# ── Fix 8: speed_score from elapsed time ─────────────────────────────────


class TestSpeedScore:
    def test_instant_completion_scores_one(self):
        """A job completed instantly should score close to 1.0."""
        now = datetime.now(timezone.utc)
        created = now
        completed = now + timedelta(seconds=1)
        estimated_hours = 8.0
        elapsed = (completed - created).total_seconds()
        score = max(0.0, min(1.0, 1.0 - (elapsed / (estimated_hours * 3600))))
        assert score > 0.99

    def test_full_duration_scores_zero(self):
        """A job that takes the full estimated time should score 0.0."""
        now = datetime.now(timezone.utc)
        created = now
        completed = now + timedelta(hours=8)
        estimated_hours = 8.0
        elapsed = (completed - created).total_seconds()
        score = max(0.0, min(1.0, 1.0 - (elapsed / (estimated_hours * 3600))))
        assert score == pytest.approx(0.0, abs=0.01)

    def test_half_duration_scores_half(self):
        """A job that takes half the estimated time should score ~0.5."""
        now = datetime.now(timezone.utc)
        created = now
        completed = now + timedelta(hours=4)
        estimated_hours = 8.0
        elapsed = (completed - created).total_seconds()
        score = max(0.0, min(1.0, 1.0 - (elapsed / (estimated_hours * 3600))))
        assert score == pytest.approx(0.5, abs=0.01)

    def test_over_duration_clamped_to_zero(self):
        """A job that exceeds estimated time should be clamped to 0.0."""
        now = datetime.now(timezone.utc)
        created = now
        completed = now + timedelta(hours=16)
        estimated_hours = 8.0
        elapsed = (completed - created).total_seconds()
        score = max(0.0, min(1.0, 1.0 - (elapsed / (estimated_hours * 3600))))
        assert score == 0.0
