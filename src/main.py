from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from src.config import ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
from src.data.models import init_db
from src.intake.telegram_bot import TelegramBot

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
    log.info("Demand Engine is running. Press Ctrl+C to stop.")

    # Health check server for Railway
    app = FastAPI()

    @app.get("/")
    async def health():
        return JSONResponse({"status": "ok", "service": "demand-engine"})

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
        await bot.stop()
        log.info("Demand Engine stopped.")


if __name__ == "__main__":
    asyncio.run(main())
