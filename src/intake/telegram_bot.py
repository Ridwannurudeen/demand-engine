from __future__ import annotations

import asyncio
import json
import logging
import os

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
from src.data.models import AsyncSessionLocal, Job, JobStatus, has_used_free_trial
from src.intelligence.competitor_monitor import CompetitorMonitor
from src.execution.delivery import DeliveryManager
from src.execution.direct_fulfillment import DirectFulfillment
from src.execution.job_manager import JobManager
from src.execution.orchestrator import Orchestrator
from src.execution.payment import PaymentMonitor
from src.intake.base import IntakeSource
from src.intelligence.agent_matcher import AgentMatcher
from src.intelligence.pricing_engine import PricingEngine
from src.intelligence.task_classifier import Classification, TaskClassifier

log = logging.getLogger(__name__)


class TelegramBot(IntakeSource):
    def __init__(self, competitor_monitor: CompetitorMonitor | None = None) -> None:
        self.competitor_monitor = competitor_monitor
        self.classifier = TaskClassifier()
        self.pricer = PricingEngine()
        self.matcher = AgentMatcher()
        self.job_mgr = JobManager(
            on_complete=self._on_acp_complete,
            on_failure=self._on_acp_failure,
        )
        self.delivery = DeliveryManager()
        self.direct = DirectFulfillment()
        self.orchestrator = Orchestrator()
        self.payment = PaymentMonitor()
        self.app: Application | None = None
        self._webhook_url: str | None = None

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
        self.app.add_handler(CommandHandler("paid", self._cmd_paid))
        self.app.add_handler(CommandHandler("topage", self._cmd_topage))
        self.app.add_handler(CommandHandler("myid", self._cmd_myid))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self.app.initialize()
        await self.app.start()

        # Use webhook mode on Railway (has RAILWAY_PUBLIC_DOMAIN),
        # fall back to polling for local dev
        public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
        if public_domain:
            self._webhook_url = f"https://{public_domain}/telegram-webhook"
            await self.app.bot.set_webhook(
                url=self._webhook_url,
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
            log.info("Telegram bot started (webhook: %s)", self._webhook_url)
        else:
            await self.app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
            log.info("Telegram bot started (polling mode)")

    @property
    def is_webhook_mode(self) -> bool:
        return self._webhook_url is not None

    async def process_webhook_update(self, data: dict) -> None:
        """Process an incoming webhook update from FastAPI."""
        update = Update.de_json(data, self.app.bot)
        await self.app.process_update(update)

    async def stop(self) -> None:
        if self.app:
            if self._webhook_url:
                await self.app.bot.delete_webhook()
            else:
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
            "I connect you with AI agents to get work done — content creation, "
            "research, code, data analysis, social media, and more.\n\n"
            "Your first job is FREE — just describe what you need!\n\n"
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

    async def _cmd_myid(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reply with the user's Telegram chat ID (needed for OPERATOR_CHAT_ID env var)."""
        chat_id = update.effective_chat.id
        await update.message.reply_text(
            f"Your Telegram chat ID is: {chat_id}\n\n"
            f"Add this to your .env file:\n"
            f"OPERATOR_CHAT_ID={chat_id}"
        )

    async def _cmd_topage(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show top 5 aGDP.io agents and their winning patterns."""
        if not self.competitor_monitor:
            await update.message.reply_text("Competitor monitor is not enabled.")
            return

        await update.message.reply_text(
            "Fetching aGDP.io data and analysing top agents... (may take ~15s)"
        )
        try:
            analysis = await self.competitor_monitor.run_now()
        except Exception as e:
            log.error("Competitor analysis failed: %s", e)
            await update.message.reply_text(f"Analysis failed: {e}")
            return

        # Telegram messages max out at 4096 chars — split if needed
        if len(analysis) <= 4096:
            await update.message.reply_text(analysis)
        else:
            # Send in chunks of 4096
            for i in range(0, len(analysis), 4096):
                await update.message.reply_text(analysis[i:i + 4096])

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

        # Check free trial eligibility
        eligible_for_trial = not await has_used_free_trial(str(user.id), platform="telegram")

        # Update job with classification data
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            db_job.category = classification.category
            db_job.complexity = classification.complexity
            db_job.client_price = quote.client_price
            db_job.status = JobStatus.QUOTED
            await session.commit()

        # Present quote with accept/reject buttons
        if eligible_for_trial:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Try Free", callback_data=f"trial_{job_id}"
                    ),
                    InlineKeyboardButton(
                        "Decline", callback_data=f"decline_{job_id}"
                    ),
                ]
            ])
            await update.message.reply_text(
                f"Here's your quote (Job #{job_id}):\n\n{quote.breakdown}\n\n"
                "Your first job is FREE! Tap 'Try Free' to get started.",
                reply_markup=keyboard,
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "Accept", callback_data=f"accept_{job_id}"
                    ),
                    InlineKeyboardButton(
                        "Decline", callback_data=f"decline_{job_id}"
                    ),
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

        if data.startswith("trial_"):
            job_id = int(data.split("_", 1)[1])
            await self._start_free_trial(query, job_id)
        elif data.startswith("accept_"):
            job_id = int(data.split("_", 1)[1])
            await self._accept_job(query, job_id)
        elif data.startswith("decline_"):
            job_id = int(data.split("_", 1)[1])
            await self._decline_job(query, job_id)

    async def _start_free_trial(self, query, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                await query.edit_message_text("Job not found.")
                return
            db_job.status = JobStatus.ACCEPTED
            db_job.is_free_trial = True
            db_job.client_price = 0.0
            await session.commit()
            client_id = db_job.client_id

        await query.edit_message_text(
            f"Job #{job_id} — Free trial activated!\n\n"
            "Working on your request now..."
        )
        await self._process_paid_job(job_id, client_id)

    async def _accept_job(self, query, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                await query.edit_message_text("Job not found.")
                return
            db_job.status = JobStatus.ACCEPTED
            await session.commit()
            client_price = db_job.client_price or 0
            client_id = db_job.client_id

        # Show payment instructions
        instructions = self.payment.get_payment_instructions(client_price, job_id)
        await query.edit_message_text(
            f"Job #{job_id} accepted!\n\n{instructions}",
            parse_mode="Markdown",
        )

        # Start monitoring for payment in background
        import asyncio
        asyncio.create_task(
            self._wait_for_payment_and_process(job_id, client_price, client_id)
        )

    async def _wait_for_payment_and_process(
        self, job_id: int, amount: float, client_id: str
    ) -> None:
        """Background task: wait for USDC payment, then start the job."""
        payment = await self.payment.wait_for_payment(amount, timeout=600.0)
        if payment:
            await self.send_message(
                client_id,
                f"Payment received for Job #{job_id}! "
                f"({payment['amount']:.2f} USDC, tx: {payment['hash'][:16]}...)\n\n"
                "Finding the best agent now..."
            )
            await self._process_paid_job(job_id, client_id)
        else:
            await self.send_message(
                client_id,
                f"Job #{job_id}: Payment not detected within 10 minutes. "
                f"If you already paid, use /paid {job_id} to trigger manual verification."
            )

    async def _cmd_paid(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Manual payment confirmation trigger."""
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /paid <job_id>")
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
            if db_job.status != JobStatus.ACCEPTED:
                await update.message.reply_text(
                    f"Job #{job_id} is not awaiting payment (status: {db_job.status.value})."
                )
                return
            client_price = db_job.client_price or 0
            client_id = db_job.client_id

        await update.message.reply_text(
            f"Checking for payment on Base chain for Job #{job_id}..."
        )

        # Quick check for recent transfers
        payment = await self.payment.wait_for_payment(
            client_price, timeout=30.0, poll_interval=5.0
        )
        if payment:
            await update.message.reply_text(
                f"Payment confirmed! ({payment['amount']:.2f} USDC)\n"
                "Finding the best agent now..."
            )
            await self._process_paid_job(job_id, client_id)
        else:
            await update.message.reply_text(
                f"Payment not found yet for ${client_price:.2f} USDC. "
                "Make sure you sent USDC on the Base chain to the correct address. "
                "Try /paid again in a minute."
            )

    async def _process_paid_job(self, job_id: int, client_id: str) -> None:
        """After payment confirmed: fulfill the job via ACP agents or direct Claude."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return
            db_job.status = JobStatus.IN_PROGRESS
            complexity = db_job.complexity or 0.0
            is_trial = db_job.is_free_trial
            category = db_job.category.value if db_job.category else "other"
            client_price = db_job.client_price or 0.0
            raw_request = db_job.raw_request
            await session.commit()

        # Free trials always use direct (single call), no orchestration
        if is_trial:
            await self.send_message(
                client_id, f"Job #{job_id}: Working on your free trial..."
            )
            result = await self.direct.fulfill(job_id)
        elif complexity > 0.7:
            # Try ACP marketplace agents first for complex jobs
            acp_delegated, result = await self._try_acp_then_fallback(
                job_id, client_id, category, complexity, client_price, raw_request
            )
            if acp_delegated:
                # ACP job is async — result will be delivered via monitoring
                return
        else:
            await self.send_message(
                client_id, f"Job #{job_id}: Working on it now..."
            )
            result = await self.direct.fulfill(job_id)

        if result:
            formatted = await self.delivery.format_for_telegram(job_id)
            await self.send_message(client_id, formatted)
            await self.delivery.mark_delivered_to_client(job_id)
        else:
            await self.send_message(
                client_id,
                f"Job #{job_id}: Something went wrong during fulfillment. "
                "We're looking into it and will get back to you."
            )

    async def _try_acp_then_fallback(
        self,
        job_id: int,
        client_id: str,
        category: str,
        complexity: float,
        client_price: float,
        raw_request: str,
    ) -> tuple[bool, str | None]:
        """Try ACP marketplace agents; fall back to Orchestrator on failure.

        Returns (acp_delegated, result) — if acp_delegated is True, the job
        is being handled asynchronously and result should be ignored.
        """
        try:
            classification = Classification(
                category=category,
                complexity=complexity,
                estimated_hours=max(1.0, complexity * 8.0),
                feasibility_score=1.0,
                summary=raw_request[:200],
            )
            candidates = await self.matcher.find_agents(
                classification, budget=client_price
            )
            if candidates:
                best = candidates[0]
                await self.send_message(
                    client_id,
                    f"Job #{job_id}: Found agent '{best.name}' — "
                    "delegating your request..."
                )
                async with AsyncSessionLocal() as session:
                    db_job = await session.get(Job, job_id)
                acp_job_id = await self.job_mgr.create_acp_job(db_job, best)
                await self.job_mgr.start_monitoring(job_id, acp_job_id)
                return True, None
        except Exception as e:
            log.warning(
                "ACP delegation failed for job #%d, falling back to orchestrator: %s",
                job_id, e,
            )

        # Fallback: orchestrate directly
        await self.send_message(
            client_id,
            f"Job #{job_id}: This is a complex request — breaking it into "
            "sub-tasks and working on them in parallel..."
        )
        return False, await self.orchestrator.fulfill(job_id)

    # ── ACP completion/failure callbacks ────────────────────────────

    async def _on_acp_complete(self, job_id: int) -> None:
        """Notify the user when an ACP-delegated job completes."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return
            client_id = db_job.client_id

        formatted = await self.delivery.format_for_telegram(job_id)
        await self.send_message(client_id, formatted)
        await self.delivery.mark_delivered_to_client(job_id)

    async def _on_acp_failure(self, job_id: int) -> None:
        """Notify the user when an ACP-delegated job fails."""
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if not db_job:
                return
            client_id = db_job.client_id
            summary = db_job.result_summary or "Unknown error"

        await self.send_message(
            client_id,
            f"Job #{job_id} failed: {summary}\n\n"
            "We're sorry about that. You can try submitting a new request.",
        )

    async def _decline_job(self, query, job_id: int) -> None:
        async with AsyncSessionLocal() as session:
            db_job = await session.get(Job, job_id)
            if db_job:
                db_job.status = JobStatus.CANCELLED
                await session.commit()

        await query.edit_message_text(
            f"Job #{job_id} cancelled. Send me another request anytime!"
        )
