from __future__ import annotations

import pytest

from src.intelligence.pricing_engine import PricingEngine, Quote
from src.intelligence.task_classifier import Classification


@pytest.fixture
def pricer():
    return PricingEngine()


def _make_classification(**overrides) -> Classification:
    defaults = {
        "category": "content_creation",
        "complexity": 0.5,
        "estimated_hours": 2.0,
        "feasibility_score": 0.8,
        "summary": "Test task",
        "rejection_reason": None,
    }
    defaults.update(overrides)
    return Classification(**defaults)


class TestPricingEngine:
    def test_basic_quote(self, pricer):
        c = _make_classification()
        quote = pricer.generate_quote(c)

        assert isinstance(quote, Quote)
        assert quote.client_price >= 5.0
        assert quote.client_price <= 500.0
        assert quote.agent_budget < quote.client_price
        assert quote.margin > 0
        assert quote.protocol_fee > 0

    def test_high_complexity_costs_more(self, pricer):
        low = pricer.generate_quote(_make_classification(complexity=0.1))
        high = pricer.generate_quote(_make_classification(complexity=0.9))
        assert high.client_price > low.client_price

    def test_longer_time_costs_more(self, pricer):
        short = pricer.generate_quote(_make_classification(estimated_hours=0.5))
        long = pricer.generate_quote(_make_classification(estimated_hours=6.0))
        assert long.client_price > short.client_price

    def test_price_within_bounds(self, pricer):
        # Extreme low
        q_low = pricer.generate_quote(
            _make_classification(complexity=0.0, estimated_hours=0.1)
        )
        assert q_low.client_price >= 5.0

        # Extreme high
        q_high = pricer.generate_quote(
            _make_classification(
                category="code", complexity=1.0, estimated_hours=10.0
            )
        )
        assert q_high.client_price <= 500.0

    def test_margin_is_correct_percentage(self, pricer):
        quote = pricer.generate_quote(_make_classification())
        expected_agent = round(quote.client_price * 0.4, 2)
        assert quote.agent_budget == expected_agent

    def test_breakdown_contains_info(self, pricer):
        quote = pricer.generate_quote(_make_classification())
        assert "content_creation" in quote.breakdown
        assert "$" in quote.breakdown


class TestClassificationFeasibility:
    def test_feasible(self):
        c = _make_classification(feasibility_score=0.8)
        assert c.is_feasible

    def test_not_feasible_low_score(self):
        c = _make_classification(feasibility_score=0.1)
        assert not c.is_feasible

    def test_not_feasible_rejected(self):
        c = _make_classification(
            feasibility_score=0.9, rejection_reason="Illegal request"
        )
        assert not c.is_feasible
