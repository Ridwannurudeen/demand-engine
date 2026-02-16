from __future__ import annotations

import asyncio
import logging

try:
    import tweepy
    from tweepy.asynchronous import AsyncClient
    HAS_TWEEPY = True
except (ImportError, Exception):
    HAS_TWEEPY = False
    AsyncClient = None

from src.config import (
    TWITTER_API_KEY,
    TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN,
    TWITTER_ACCESS_SECRET,
    TWITTER_BEARER_TOKEN,
)
from src.data.models import AsyncSessionLocal, Job, JobStatus, has_used_free_trial
from src.execution.delivery import DeliveryManager
from src.execution.direct_fulfillment import DirectFulfillment
from src.intake.base import IntakeSource
from src.intelligence.pricing_engine import PricingEngine
from src.intelligence.task_classifier import TaskClassifier

log = logging.getLogger(__name__)

# Keywords that signal someone needs a service
DEMAND_KEYWORDS = [
    "looking for someone to",
    "need help with",
    "anyone know an AI that can",
    "can someone write",
    "need a script",
    "need a blog post",
    "looking for an AI agent",
    "who can help me",
    "need content created",
    "need research on",
    "hire an AI",
]

BOT_USERNAME = "DemandEngineBot"


class TwitterIntake(IntakeSource):
    """Monitors Twitter for service requests and replies with quotes."""

    def __init__(self) -> None:
        self.classifier = TaskClassifier()
        self.pricer = PricingEngine()
        self.delivery = DeliveryManager()
        self.direct = DirectFulfillment()
        self.client: AsyncClient | None = None
        self._running = False
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not HAS_TWEEPY:
            log.warning("tweepy async deps not installed — Twitter intake disabled")
            return
        if not TWITTER_BEARER_TOKEN:
            log.warning("TWITTER_BEARER_TOKEN not set — Twitter intake disabled")
            return

        self.client = AsyncClient(
            bearer_token=TWITTER_BEARER_TOKEN,
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
            wait_on_rate_limit=True,
        )

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_mentions())
        log.info("Twitter intake started — monitoring mentions of @%s", BOT_USERNAME)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        log.info("Twitter intake stopped")

    async def send_message(self, client_id: str, message: str) -> None:
        """Reply to a tweet (client_id = tweet_id)."""
        if not self.client:
            return
        try:
            # Truncate to Twitter's 280 char limit
            if len(message) > 270:
                message = message[:267] + "..."
            await self.client.create_tweet(
                text=message, in_reply_to_tweet_id=int(client_id)
            )
        except Exception as e:
            log.error("Failed to reply to tweet %s: %s", client_id, e)

    async def _poll_mentions(self) -> None:
        """Poll for mentions every 60 seconds."""
        last_seen_id: str | None = None
        log.info("Starting Twitter mention polling loop")

        while self._running:
            try:
                # Get recent mentions
                me = await self.client.get_me()
                my_id = me.data.id

                kwargs = {
                    "id": my_id,
                    "max_results": 10,
                    "tweet_fields": ["author_id", "text", "created_at"],
                }
                if last_seen_id:
                    kwargs["since_id"] = last_seen_id

                mentions = await self.client.get_users_mentions(**kwargs)

                if mentions.data:
                    for tweet in reversed(mentions.data):
                        last_seen_id = str(tweet.id)
                        await self._handle_mention(tweet)

            except Exception as e:
                log.error("Twitter polling error: %s", e)

            await asyncio.sleep(60)  # Poll every 60 seconds

    async def _handle_mention(self, tweet) -> None:
        """Process a mention — classify, quote, and reply."""
        text = tweet.text
        tweet_id = str(tweet.id)
        author_id = str(tweet.author_id)

        # Remove @mention from text
        clean_text = " ".join(
            w for w in text.split() if not w.startswith("@")
        ).strip()

        if len(clean_text) < 10:
            await self.send_message(
                tweet_id,
                "Tell me more about what you need! "
                "Describe your request in detail and I'll give you a quote.",
            )
            return

        log.info("Processing Twitter mention from %s: %s", author_id, clean_text[:80])

        # Classify
        try:
            classification = await self.classifier.classify(clean_text)
        except Exception as e:
            log.error("Classification failed for tweet %s: %s", tweet_id, e)
            return

        if not classification.is_feasible:
            await self.send_message(
                tweet_id,
                "I can't handle that request, but I can help with content, "
                "research, code, data analysis, and more. Try again!",
            )
            return

        # Generate quote
        quote = self.pricer.generate_quote(classification)

        # Create job record
        async with AsyncSessionLocal() as session:
            job = Job(
                client_platform="twitter",
                client_id=author_id,
                client_username=f"tweet_{tweet_id}",
                raw_request=clean_text,
                status=JobStatus.QUOTED,
                category=classification.category,
                complexity=classification.complexity,
                client_price=quote.client_price,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        # Check free trial eligibility
        eligible = not await has_used_free_trial(author_id)

        if eligible:
            await self.send_message(
                tweet_id,
                f"I can do that! (Job #{job_id})\n\n"
                f"Category: {classification.category.value}\n"
                f"Normal price: ${quote.client_price:.0f}\n\n"
                f"Your first job is FREE! DM me or reply 'go' to start.\n\n"
                f"Or message @{BOT_USERNAME} on Telegram for the full experience.",
            )
        else:
            await self.send_message(
                tweet_id,
                f"I can do that! (Job #{job_id})\n\n"
                f"Price: ${quote.client_price:.0f} USDC\n\n"
                f"Message @{BOT_USERNAME} on Telegram to pay and get started.",
            )
