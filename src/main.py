from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from src.config import ACP_AGENT_WALLET, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TWITTER_BEARER_TOKEN
from src.data.models import AsyncSessionLocal, init_db, Job, JobStatus
from src.intake.agdp_bounty_monitor import AGDPBountyMonitor
from src.intake.telegram_bot import TelegramBot
from src.intake.twitter_intake import TwitterIntake
from src.web.dashboard import router as dashboard_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("demand-engine")


def _check_config() -> list[str]:
    errors = []
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set in .env")
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set in .env")
    if not ACP_AGENT_WALLET or not ACP_AGENT_WALLET.startswith("0x"):
        errors.append("ACP_AGENT_WALLET is not set or invalid in .env")
    return errors


async def main() -> None:
    log.info("Starting Demand Engine...")

    errors = _check_config()
    if errors:
        for e in errors:
            log.error(e)
        log.error(
            "Please set the required environment variables in .env and restart."
        )
        sys.exit(1)

    # Initialize database
    await init_db()
    log.info("Database initialized")

    # Start Telegram bot
    bot = TelegramBot()
    await bot.start()

    # Start Twitter intake (optional — only if credentials are set)
    twitter = TwitterIntake()
    if TWITTER_BEARER_TOKEN:
        await twitter.start()
        log.info("Twitter intake active")
    else:
        log.info("Twitter intake disabled (no TWITTER_BEARER_TOKEN)")

    # Start aGDP.io bounty monitor
    bounty_monitor = AGDPBountyMonitor()
    await bounty_monitor.start()
    log.info("aGDP.io Bounty Monitor active")

    log.info("Demand Engine is running. Press Ctrl+C to stop.")

    # FastAPI server for Railway health check + Telegram webhook
    app = FastAPI()

    @app.get("/")
    async def health():
        return JSONResponse({"status": "ok", "service": "demand-engine"})

    @app.post("/telegram-webhook")
    async def telegram_webhook(request: Request):
        data = await request.json()
        await bot.process_webhook_update(data)
        return JSONResponse({"ok": True})

    @app.post("/agdp-callback/{job_id}")
    async def agdp_callback(job_id: int, request: Request):
        """Receive delivery confirmation from aGDP / ACP."""
        try:
            data = await request.json()
        except Exception:
            data = {}
        log.info("aGDP callback for job #%d: %s", job_id, data)
        status = str(data.get("status", "")).lower()
        if status in ("fulfilled", "completed", "paid", "accepted"):
            async with AsyncSessionLocal() as session:
                db_job = await session.get(Job, job_id)
                if db_job and db_job.status != JobStatus.COMPLETED:
                    db_job.status = JobStatus.COMPLETED
                    await session.commit()
                    log.info("Job #%d marked COMPLETED via aGDP callback", job_id)
        return JSONResponse({"ok": True})

    app.include_router(dashboard_router)

    port = int(os.getenv("PORT", "8080"))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Health check server on port %d", port)

    # Keep alive
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        await bounty_monitor.stop()
        await twitter.stop()
        await bot.stop()
        log.info("Demand Engine stopped.")


if __name__ == "__main__":
    asyncio.run(main())
