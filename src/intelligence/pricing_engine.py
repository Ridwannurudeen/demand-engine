from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import DEFAULT_MARGIN_PERCENT, MAX_JOB_PRICE, MIN_JOB_PRICE
from src.intelligence.task_classifier import Classification

log = logging.getLogger(__name__)

# Base rates per category (USD) — what the market typically pays
CATEGORY_BASE_RATES: dict[str, float] = {
    "content_creation": 30.0,
    "research": 40.0,
    "trading": 50.0,
    "code": 60.0,
    "design": 45.0,
    "data_analysis": 50.0,
    "social_media": 25.0,
    "other": 35.0,
}


@dataclass
class Quote:
    client_price: float  # what we charge
    agent_budget: float  # max we'll pay the ACP agent
    margin: float  # our take
    protocol_fee: float  # ACP's 20% of agent side
    breakdown: str  # human-readable explanation


class PricingEngine:
    def generate_quote(self, classification: Classification) -> Quote:
        base = CATEGORY_BASE_RATES.get(classification.category, 35.0)

        # Scale by complexity and estimated time
        complexity_mult = 0.5 + classification.complexity * 1.5  # 0.5x – 2.0x
        time_mult = max(0.5, min(classification.estimated_hours, 8.0)) / 2.0

        client_price = round(base * complexity_mult * time_mult, 2)
        client_price = max(MIN_JOB_PRICE, min(MAX_JOB_PRICE, client_price))

        margin_frac = DEFAULT_MARGIN_PERCENT / 100.0
        agent_budget = round(client_price * (1 - margin_frac), 2)
        margin = round(client_price - agent_budget, 2)
        protocol_fee = round(agent_budget * 0.20, 2)  # ACP takes 20% from provider

        breakdown = (
            f"Service: {classification.summary}\n"
            f"Category: {classification.category}\n"
            f"Complexity: {classification.complexity:.0%}\n"
            f"Est. time: {classification.estimated_hours:.1f}h\n"
            f"Price: ${client_price:.2f} USDC"
        )

        log.info("Quote generated: $%.2f (agent $%.2f, margin $%.2f)",
                 client_price, agent_budget, margin)

        return Quote(
            client_price=client_price,
            agent_budget=agent_budget,
            margin=margin,
            protocol_fee=protocol_fee,
            breakdown=breakdown,
        )
