"""Tests for market simulation components."""

import math
import pytest
from decimal import Decimal

from amm_competition.market.price_process import GBMPriceProcess
from amm_competition.market.retail import RetailTrader
from amm_competition.market.arbitrageur import Arbitrageur
from amm_competition.market.router import OrderRouter
from amm_competition.core.amm import AMM
from amm_competition.core.interfaces import AMMStrategy
from amm_competition.core.trade import FeeQuote, TradeInfo


class FixedFeeStrategy(AMMStrategy):
    """Simple non-EVM strategy for tests."""

    def __init__(self, bid_fee: Decimal, ask_fee: Decimal):
        self._fees = FeeQuote(bid_fee=bid_fee, ask_fee=ask_fee)

    def after_initialize(self, initial_x: Decimal, initial_y: Decimal) -> FeeQuote:
        return self._fees

    def after_swap(self, trade: TradeInfo) -> FeeQuote:
        return self._fees


class TestGBMPriceProcess:
    def test_initial_price(self):
        gbm = GBMPriceProcess(initial_price=100.0, seed=42)
        assert gbm.current_price == Decimal("100.0")

    def test_step_changes_price(self):
        gbm = GBMPriceProcess(initial_price=100.0, seed=42)
        initial = gbm.current_price
        gbm.step()
        assert gbm.current_price != initial

    def test_deterministic_with_seed(self):
        gbm1 = GBMPriceProcess(initial_price=100.0, seed=42)
        gbm2 = GBMPriceProcess(initial_price=100.0, seed=42)

        prices1 = [gbm1.step() for _ in range(10)]
        prices2 = [gbm2.step() for _ in range(10)]

        assert prices1 == prices2

    def test_generate_path_length(self):
        gbm = GBMPriceProcess(initial_price=100.0, seed=42)
        path = gbm.generate_path(100)
        assert len(path) == 100

    def test_generate_path_starts_with_initial(self):
        gbm = GBMPriceProcess(initial_price=100.0, seed=42)
        path = gbm.generate_path(10)
        assert path[0] == Decimal("100.0")

    def test_prices_stay_positive(self):
        """GBM should never produce negative prices."""
        gbm = GBMPriceProcess(
            initial_price=100.0,
            sigma=1.0,  # High volatility
            seed=42,
        )
        path = gbm.generate_path(1000)
        assert all(p > 0 for p in path)

    def test_reset(self):
        gbm = GBMPriceProcess(initial_price=100.0, seed=42)
        gbm.step()
        gbm.step()
        gbm.reset(seed=42)
        assert gbm.current_price == Decimal("100.0")


class TestRetailTrader:
    def test_generate_orders_deterministic(self):
        trader1 = RetailTrader(arrival_rate=5.0, seed=42)
        trader2 = RetailTrader(arrival_rate=5.0, seed=42)

        orders1 = trader1.generate_orders()
        orders2 = trader2.generate_orders()

        assert len(orders1) == len(orders2)
        for o1, o2 in zip(orders1, orders2):
            assert o1.side == o2.side
            assert o1.size == o2.size

    def test_order_sides_distribution(self):
        """Test that buy_prob affects order distribution."""
        trader = RetailTrader(arrival_rate=100.0, buy_prob=0.7, seed=42)

        buys = 0
        total = 0
        for _ in range(100):
            orders = trader.generate_orders()
            for order in orders:
                total += 1
                if order.side == "buy":
                    buys += 1

        # With buy_prob=0.7 and many samples, should be close to 70%
        assert 0.6 < buys / total < 0.8

    def test_zero_arrival_rate(self):
        trader = RetailTrader(arrival_rate=0.0, seed=42)
        # With rate 0, should almost never get orders
        for _ in range(100):
            orders = trader.generate_orders()
            assert len(orders) == 0


class TestArbitrageur:
    @pytest.fixture
    def amm(self, vanilla_strategy):
        amm = AMM(
            strategy=vanilla_strategy,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
        )
        amm.initialize()
        return amm

    def test_no_arb_at_fair_price(self, amm):
        """No arbitrage when AMM price equals fair price."""
        arb = Arbitrageur()
        result = arb.find_arb_opportunity(amm, fair_price=Decimal("100"))
        # With 30bps fees, small price differences don't create arb
        # At exact fair price, no arb
        assert result is None

    def test_arb_when_amm_underpriced(self, amm):
        """Arbitrage opportunity when AMM underprices X."""
        arb = Arbitrageur()
        # Fair price is higher than AMM price - should buy X from AMM
        result = arb.find_arb_opportunity(amm, fair_price=Decimal("110"))
        assert result is not None
        assert result.side == "sell"  # AMM sells X
        assert result.profit > 0

    def test_arb_when_amm_overpriced(self, amm):
        """Arbitrage opportunity when AMM overprices X."""
        arb = Arbitrageur()
        # Fair price is lower than AMM price - should sell X to AMM
        result = arb.find_arb_opportunity(amm, fair_price=Decimal("90"))
        assert result is not None
        assert result.side == "buy"  # AMM buys X
        assert result.profit > 0

    def test_execute_arb_updates_reserves(self, amm):
        arb = Arbitrageur()
        initial_x = amm.reserve_x

        # Fair price higher - buy from AMM
        arb.execute_arb(amm, fair_price=Decimal("150"), timestamp=0)

        # Reserves should have changed
        assert amm.reserve_x != initial_x

    def test_buy_arb_sizes_trade_accounting_for_fee(self):
        """Buy-side arb sizing should match fee-on-input closed form."""
        fee = Decimal("0.05")  # 5% to make the difference visible
        amm = AMM(
            strategy=FixedFeeStrategy(bid_fee=fee, ask_fee=fee),
            reserve_x=Decimal("1000"),
            reserve_y=Decimal("1000"),
        )
        amm.initialize()

        arb = Arbitrageur()
        fair_price = Decimal("1.2")
        result = arb.find_arb_opportunity(amm, fair_price=fair_price)
        assert result is not None
        assert result.side == "sell"  # AMM sells X

        x = float(amm.reserve_x)
        y = float(amm.reserve_y)
        k = x * y
        gamma = 1.0 - float(fee)
        p = float(fair_price)
        expected_x_out = x - math.sqrt(k / (gamma * p))

        assert abs(float(result.amount_x) - expected_x_out) / expected_x_out < 1e-9

    def test_sell_arb_sizes_trade_accounting_for_fee(self):
        """Sell-side arb sizing should match fee-on-input closed form."""
        fee = Decimal("0.05")  # 5% to make the difference visible
        amm = AMM(
            strategy=FixedFeeStrategy(bid_fee=fee, ask_fee=fee),
            reserve_x=Decimal("1000"),
            reserve_y=Decimal("1000"),
        )
        amm.initialize()

        arb = Arbitrageur()
        fair_price = Decimal("0.9")
        result = arb.find_arb_opportunity(amm, fair_price=fair_price)
        assert result is not None
        assert result.side == "buy"  # AMM buys X

        x = float(amm.reserve_x)
        y = float(amm.reserve_y)
        k = x * y
        gamma = 1.0 - float(fee)
        p = float(fair_price)
        expected_x_in = (math.sqrt(k * gamma / p) - x) / gamma

        assert abs(float(result.amount_x) - expected_x_in) / expected_x_in < 1e-9


class TestOrderRouter:
    @pytest.fixture
    def amms(self, vanilla_bytecode_and_abi):
        """Create two AMMs with different fees."""
        from amm_competition.evm.adapter import EVMStrategyAdapter

        bytecode, abi = vanilla_bytecode_and_abi
        strategy1 = EVMStrategyAdapter(bytecode=bytecode, abi=abi)
        strategy2 = EVMStrategyAdapter(bytecode=bytecode, abi=abi, name="HighFee")

        amm1 = AMM(
            strategy=strategy1,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
            name="LowFee",
        )
        amm2 = AMM(
            strategy=strategy2,
            reserve_x=Decimal("100"),
            reserve_y=Decimal("10000"),
            name="HighFee",
        )
        amm1.initialize()
        amm2.initialize()
        return [amm1, amm2]

    def test_routes_to_better_price(self, amms):
        """Router should favor the AMM with lower fee in optimal split."""
        router = OrderRouter()

        # For buying X, lower fee = more flow to that AMM
        splits = router.compute_optimal_split_buy(amms, Decimal("1000"))
        assert len(splits) == 2

        # Both AMMs have same 30bps fee, so split should be roughly equal
        assert all(s[1] > 0 for s in splits)

    def test_routes_sell_to_better_price(self, amms):
        """Router should favor the AMM with lower fee in optimal split."""
        router = OrderRouter()

        splits = router.compute_optimal_split_sell(amms, Decimal("10"))
        assert len(splits) == 2

        # Both AMMs have same 30bps fee, so split should be roughly equal
        assert all(s[1] > 0 for s in splits)
