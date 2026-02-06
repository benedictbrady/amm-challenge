"""Runtime security regression tests for executor/adapter behavior."""

from decimal import Decimal

import pytest

from amm_competition.core.trade import TradeInfo
from amm_competition.evm.adapter import EVMStrategyAdapter
from amm_competition.evm.executor import EVMExecutionResult, EVMStrategyExecutor


def _sample_trade() -> TradeInfo:
    return TradeInfo(
        side="buy",
        amount_x=Decimal("1"),
        amount_y=Decimal("100"),
        timestamp=1,
        reserve_x=Decimal("99"),
        reserve_y=Decimal("10100"),
    )


def test_after_swap_fast_rejects_short_return_data(vanilla_bytecode_and_abi) -> None:
    bytecode, abi = vanilla_bytecode_and_abi
    executor = EVMStrategyExecutor(bytecode=bytecode, abi=abi)

    class ShortReturnEVM:
        def message_call(self, **kwargs):
            return b"\x00" * 63

    executor.evm = ShortReturnEVM()

    with pytest.raises(RuntimeError, match="afterSwap failed: Invalid return data length"):
        executor.after_swap_fast(_sample_trade())


def test_after_swap_fast_surfaces_evm_errors(vanilla_bytecode_and_abi) -> None:
    bytecode, abi = vanilla_bytecode_and_abi
    executor = EVMStrategyExecutor(bytecode=bytecode, abi=abi)

    class ExplodingEVM:
        def message_call(self, **kwargs):
            raise RuntimeError("boom")

    executor.evm = ExplodingEVM()

    with pytest.raises(RuntimeError, match="afterSwap failed: boom"):
        executor.after_swap_fast(_sample_trade())


def test_adapter_clamps_out_of_range_initialize_fees(vanilla_bytecode_and_abi) -> None:
    bytecode, abi = vanilla_bytecode_and_abi
    adapter = EVMStrategyAdapter(bytecode=bytecode, abi=abi)

    class FakeExecutor:
        def after_initialize(self, initial_x, initial_y):
            return EVMExecutionResult(
                bid_fee=Decimal("-1"),
                ask_fee=Decimal("999"),
                gas_used=123,
                success=True,
            )

    adapter._executor = FakeExecutor()
    quote = adapter.after_initialize(Decimal("100"), Decimal("10000"))
    assert quote.bid_fee == Decimal("0")
    assert quote.ask_fee == Decimal("0.1")


def test_adapter_clamps_out_of_range_swap_fees(vanilla_bytecode_and_abi) -> None:
    bytecode, abi = vanilla_bytecode_and_abi
    adapter = EVMStrategyAdapter(bytecode=bytecode, abi=abi)

    class FakeExecutor:
        def after_swap_fast(self, trade):
            return (-1, 2 * 10**17)  # -1 WAD, 20% WAD

    adapter._executor = FakeExecutor()
    quote = adapter.after_swap(_sample_trade())
    assert quote.bid_fee == Decimal("0")
    assert quote.ask_fee == Decimal("0.1")
