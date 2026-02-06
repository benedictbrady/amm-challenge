"""Tests for AMM engine."""

import pytest
from decimal import Decimal

from amm_competition.core.amm import AMM
from amm_competition.core.interfaces import AMMStrategy
from amm_competition.core.trade import FeeQuote, TradeInfo


class ZeroFeeStrategy(AMMStrategy):
    """Minimal zero-fee strategy for testing fee-less behavior."""

    def after_initialize(self, initial_x: Decimal, initial_y: Decimal) -> FeeQuote:
        return FeeQuote.symmetric(Decimal("0"))

    def after_swap(self, trade: TradeInfo) -> FeeQuote:
        return FeeQuote.symmetric(Decimal("0"))

    def get_name(self) -> str:
        return "ZeroFee"


class TestAMM:
    @pytest.fixture
    def amm(self, vanilla_strategy):
        """Create a standard AMM for testing."""
        amm = AMM(
            strategy=vanilla_strategy,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
        )
        amm.initialize()
        return amm

    def test_initialization(self, amm):
        assert amm.reserve_x == Decimal("100")
        assert amm.reserve_y == Decimal("10000")
        assert amm.k == Decimal("1000000")
        assert amm.spot_price == Decimal("100")

    def test_spot_price(self, amm):
        # Price is Y/X
        assert amm.spot_price == Decimal("100")

    def test_constant_product(self, amm):
        initial_k = amm.k
        # Execute a trade
        amm.execute_sell_x(Decimal("5"), timestamp=0)
        # k should be preserved (fees go to reserves)
        assert amm.k >= initial_k  # >= because fees add to reserves

    def test_quote_buy_x_no_fee(self):
        """Test quote calculation with zero fees."""
        strategy = ZeroFeeStrategy()
        amm = AMM(
            strategy=strategy,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
        )
        amm.initialize()

        quote = amm.get_quote_buy_x(Decimal("10"))
        assert quote is not None

        # With no fees: new_y = k / new_x = 1000000 / 110 = 9090.909...
        # amount_y = 10000 - 9090.909... = 909.09...
        expected_y = Decimal("10000") - Decimal("1000000") / Decimal("110")
        assert abs(quote.amount_out - expected_y) < Decimal("0.01")

    def test_quote_sell_x_no_fee(self):
        """Test sell quote with zero fees."""
        strategy = ZeroFeeStrategy()
        amm = AMM(
            strategy=strategy,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
        )
        amm.initialize()

        quote = amm.get_quote_sell_x(Decimal("10"))
        assert quote is not None

        # Buying 10 X: new_x = 90, new_y = 1000000/90 = 11111.11...
        # amount_y = 11111.11 - 10000 = 1111.11
        expected_y = Decimal("1000000") / Decimal("90") - Decimal("10000")
        assert abs(quote.amount_in - expected_y) < Decimal("0.01")

    def test_quote_with_fees(self, amm):
        """Test that fees are applied correctly."""
        # 30bps = 0.003
        quote = amm.get_quote_buy_x(Decimal("10"))
        assert quote is not None
        assert quote.fee_rate == Decimal("0.003")
        assert quote.fee_amount > 0
        # Net output should be less than gross
        gross = Decimal("10000") - Decimal("1000000") / Decimal("110")
        assert quote.amount_out < gross

    def test_execute_buy_x(self, amm):
        """Test executing a buy trade.

        Uniswap V2 model: FULL input goes to reserves (fees accumulate).
        """
        initial_x = amm.reserve_x
        initial_y = amm.reserve_y
        initial_k = amm.k

        trade = amm.execute_buy_x(Decimal("10"), timestamp=5)

        assert trade is not None
        assert trade.side == "buy"
        assert trade.amount_x == Decimal("10")
        assert trade.timestamp == 5
        # Uniswap V2: FULL X added to reserves
        assert amm.reserve_x == initial_x + Decimal("10")
        assert amm.reserve_y < initial_y
        # k should INCREASE (fees accumulated)
        assert amm.k > initial_k

    def test_execute_sell_x(self, amm):
        """Test executing a sell trade.

        Uniswap V2 model: FULL Y paid by trader goes to reserves.
        """
        initial_x = amm.reserve_x
        initial_y = amm.reserve_y
        initial_k = amm.k

        trade = amm.execute_sell_x(Decimal("10"), timestamp=3)

        assert trade is not None
        assert trade.side == "sell"
        assert trade.amount_x == Decimal("10")
        assert amm.reserve_x == initial_x - Decimal("10")
        # Uniswap V2: FULL Y added to reserves
        assert amm.reserve_y > initial_y
        # k should INCREASE (fees accumulated)
        assert amm.k > initial_k

    def test_execute_sell_x_exceeds_reserves(self, amm):
        """Test that selling more than reserves fails."""
        trade = amm.execute_sell_x(Decimal("200"), timestamp=0)
        assert trade is None

    def test_strategy_callback(self, amm):
        """Test that strategy receives trade callbacks."""
        initial_fees = amm.current_fees

        amm.execute_buy_x(Decimal("10"), timestamp=0)

        # Vanilla strategy returns same fees, but callback should have been made
        assert amm.current_fees.bid_fee == initial_fees.bid_fee
        assert amm.current_fees.ask_fee == initial_fees.ask_fee

    def test_not_initialized_error(self, vanilla_strategy):
        """Test that using uninitialized AMM raises error."""
        amm = AMM(
            strategy=vanilla_strategy,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
        )
        # Don't call initialize()

        with pytest.raises(RuntimeError, match="not initialized"):
            amm.get_quote_buy_x(Decimal("10"))


class TestVanillaStrategy:
    def test_fixed_fees(self, vanilla_strategy):
        fees = vanilla_strategy.after_initialize(Decimal("100"), Decimal("10000"))

        assert fees.bid_fee == Decimal("0.003")
        assert fees.ask_fee == Decimal("0.003")

    def test_fees_unchanged_after_swap(self, vanilla_strategy):
        vanilla_strategy.after_initialize(Decimal("100"), Decimal("10000"))

        trade = TradeInfo(
            side="buy",
            amount_x=Decimal("10"),
            amount_y=Decimal("900"),
            timestamp=0,
            reserve_x=Decimal("110"),
            reserve_y=Decimal("9100"),
        )

        fees = vanilla_strategy.after_swap(trade)
        assert fees.bid_fee == Decimal("0.003")
        assert fees.ask_fee == Decimal("0.003")

    def test_name(self, vanilla_strategy):
        assert vanilla_strategy.get_name() == "Vanilla_30bps"
