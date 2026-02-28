from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Paths ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACP_CLI_PATH = Path(os.getenv("ACP_CLI_PATH", PROJECT_ROOT / "openclaw-acp"))

# ── API Keys ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")
TWITTER_API_KEY: str = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET: str = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN: str = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET: str = os.getenv("TWITTER_ACCESS_SECRET", "")

# ── ACP ──────────────────────────────────────────────────────────────────
ACP_AGENT_WALLET: str = os.getenv("ACP_AGENT_WALLET", "")
LITE_AGENT_API_KEY: str = os.getenv("LITE_AGENT_API_KEY", "")

# ── Notifications ─────────────────────────────────────────────────────────
# Your personal Telegram chat ID — send /myid to the bot to find it
OPERATOR_CHAT_ID: str = os.getenv("OPERATOR_CHAT_ID", "")

# ── Basescan ─────────────────────────────────────────────────────────────
BASESCAN_API_KEY: str = os.getenv("BASESCAN_API_KEY", "")

# ── Database ─────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demand_engine.db'}"
)

# ── Pricing ──────────────────────────────────────────────────────────────
DEFAULT_MARGIN_PERCENT: float = float(os.getenv("DEFAULT_MARGIN_PERCENT", "60"))
MIN_JOB_PRICE: float = float(os.getenv("MIN_JOB_PRICE", "5.0"))
MAX_JOB_PRICE: float = float(os.getenv("MAX_JOB_PRICE", "500.0"))

# ── Claude model ─────────────────────────────────────────────────────────
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# ── Task categories ──────────────────────────────────────────────────────
TASK_CATEGORIES = [
    "content_creation",
    "research",
    "trading",
    "code",
    "design",
    "data_analysis",
    "social_media",
    "other",
]
