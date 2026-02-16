from __future__ import annotations

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import TELEGRAM_BOT_TOKEN
from src.data.models import AsyncSessionLocal, Job, JobStatus
from src.execution.delivery import DeliveryManager
from src.execution.job_manager import JobManager
from src.intake.base import IntakeSource
from src.intelligence.agent_matcher import AgentMatcher
from src.intelligence.pricing_engine import PricingEngine
from src.intelligence.task_classifier import TaskClassifier

log = logging.getLogger(__name__)


class TelegramBot(IntakeSource):
    def __init__(self) -> None:
        self.classifier = TaskClassifier()
        self.pricer = PricingEngine()
        self.matcher = AgentMatcher()
        self.job_mgr = JobManager()
        self.delivery = DeliveryManager()
        self.app: Application | None = None

    async def start(self) -> None:
        self.app = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .build()
        )

        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("jobs", self._cmd_jobs))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        log.info("Telegram bot started")

    async def stop(self) -> None:
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        log.info("Telegram bot stopped")

    async def send_message(self, client_id: str, message: str) -> None:
        if self.app:
            await self.app.bot.send_message(chat_id=int(client_id), text=message)

    # ── Commands ─────────────────────────────────────────────────────

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.message.reply_text(
            "Welcome to the Demand Engine!\n\n"
            "I connect you with AI agents on the Virtuals Protocol to get work done.\n\n"
            "Just tell me what you need — content creation, research, code, "
            "data analysis, social media management, and more.\n\n"
            "Commands:\n"
            "/help — What I can do\n"
            "/status <job_id> — Check job status\n"
            "/jobs — List your recent jobs"
        )

    async def _cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await update.message.reply_text(
            "How it works:\n\n"
            "1. Send me a description of what you need\n"
            "2. I'll classify it and give you a price quote\n"
            "3. Accept the quote to kick off the job\n"
            "4. I find the best AI agent and manage the work\n"
            "5. You get the result delivered right here\n\n"
            "Example requests:\n"
            '- "Write a 1000-word blog post about DeFi trends"\n'
            '- "Research the top 10 AI tokens by market cap"\n'
            '- "Create a Python script that tracks whale wallets"\n'
            '- "Analyze sentiment on Twitter for $VIRTUAL"'
        )

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /status <job_id>")
            return

        try:
            job_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Invalid job ID.")
            return

        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job or str(db_job.client_id) != str(update.effective_user.id):
                await update.message.reply_text("Job not found.")
                return

            status_text = (
                f"Job #{db_job.id}\n"
                f"Status: {db_job.status.value}\n"
                f"Category: {db_job.category.value if db_job.category else 'pending'}\n"
                f"Price: ${db_job.client_price:.2f}" if db_job.client_price else ""
            )

            if db_job.status in (JobStatus.DELIVERED, JobStatus.COMPLETED):
                result = await self.delivery.format_for_telegram(job_id)
                status_text += f"\n\n{result}"

            await update.message.reply_text(status_text or "Job found but no details yet.")

    async def _cmd_jobs(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from sqlalchemy import select

        user_id = str(update.effective_user.id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Job)
                .where(Job.client_id == user_id)
                .order_by(Job.created_at.desc())
                .limit(10)
            )
            jobs = result.scalars().all()

        if not jobs:
            await update.message.reply_text("No jobs yet. Send me a request to get started!")
            return

        lines = ["Your recent jobs:\n"]
        for j in jobs:
            price = f"${j.client_price:.2f}" if j.client_price else "pending"
            lines.append(f"#{j.id} — {j.status.value} — {price}")
        await update.message.reply_text("\n".join(lines))

    # ── Message handling (new service requests) ──────────────────────

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        text = update.message.text.strip()
        user = update.effective_user

        if len(text) < 10:
            await update.message.reply_text(
                "Please describe what you need in more detail (at least a sentence)."
            )
            return

        await update.message.reply_text("Analyzing your request...")

        # Create job record
        async with AsyncSessionLocal() as session:
            job = Job(
                client_platform="telegram",
                client_id=str(user.id),
                client_username=user.username or user.first_name,
                raw_request=text,
                status=JobStatus.PENDING,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        # Classify the task
        try:
            classification = await self.classifier.classify(text)
        except Exception as e:
            log.error("Classification failed: %s", e)
            await update.message.reply_text(
                "Sorry, I had trouble understanding your request. "
                "Could you rephrase it with more detail?"
            )
            return

        if not classification.is_feasible:
            reason = classification.rejection_reason or "This request doesn't seem feasible for AI agents."
            await update.message.reply_text(f"I can't fulfill this request: {reason}")
            async with AsyncSessionLocal() as session:
                db_job = await session.get(Job, job_id)
                db_job.status = JobStatus.CANCELLED
                db_job.result_summary = reason
                await session.commit()
            return

        # Generate quote
        quote = self.pricer.generate_quote(classification)

        # Update job with classification data
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.category = classification.category
            db_job.complexity = classification.complexity
            db_job.client_price = quote.client_price
            db_job.status = JobStatus.QUOTED
            await session.commit()

        # Present quote with accept/reject buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Accept", callback_data=f"accept_{job_id}"),
                InlineKeyboardButton("Decline", callback_data=f"decline_{job_id}"),
            ]
        ])

        await update.message.reply_text(
            f"Here's your quote (Job #{job_id}):\n\n{quote.breakdown}\n\n"
            "Accept to proceed?",
            reply_markup=keyboard,
        )

    # ── Callback handling (accept/decline quotes) ────────────────────

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data

        if data.startswith("accept_"):
            job_id = int(data.split("_", 1)[1])
            await self._accept_job(query, job_id)
        elif data.startswith("decline_"):
            job_id = int(data.split("_", 1)[1])
            await self._decline_job(query, job_id)

    async def _accept_job(self, query, job_id: int) -> None:
        await query.edit_message_text(
            f"Job #{job_id} accepted! Finding the best agent..."
        )

        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                await query.edit_message_text("Job not found.")
                return
            db_job.status = JobStatus.MATCHING
            await session.commit()

            raw_request = db_job.raw_request
            agent_budget = db_job.client_price * 0.4 if db_job.client_price else 20.0

        # Re-classify to get structured data for matching
        try:
            classification = await self.classifier.classify(raw_request)
        except Exception:
            await query.edit_message_text(
                f"Job #{job_id}: Error during agent matching. We'll retry shortly."
            )
            return

        # Find agents
        candidates = await self.matcher.find_agents(
            classification, budget=agent_budget
        )

        if not candidates:
            await query.edit_message_text(
                f"Job #{job_id}: No suitable agents found right now. "
                "We'll keep looking and notify you when one is available."
            )
            return

        best = candidates[0]

        # Create ACP job
        try:
            acp_job_id = await self.job_mgr.create_acp_job(db_job, best)
        except Exception as e:
            log.error("ACP job creation failed: %s", e)
            await query.edit_message_text(
                f"Job #{job_id}: Failed to create the job on-chain. "
                "Our team has been notified."
            )
            return

        await query.edit_message_text(
            f"Job #{job_id} is now live!\n\n"
            f"Agent: {best.name}\n"
            f"ACP Job: {acp_job_id}\n\n"
            "I'll notify you when the result is ready. "
            f"Check progress anytime with /status {job_id}"
        )

        # Start monitoring the ACP job
        await self.job_mgr.start_monitoring(job_id, acp_job_id)

    async def _decline_job(self, query, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if db_job:
                db_job.status = JobStatus.CANCELLED
                await session.commit()

        await query.edit_message_text(
            f"Job #{job_id} cancelled. Send me another request anytime!"
        )
