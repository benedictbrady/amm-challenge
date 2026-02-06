"""Adapter to wrap EVM strategies as Python AMMStrategy objects."""

from decimal import Decimal
from typing import Optional, Tuple

from amm_competition.core.interfaces import AMMStrategy
from amm_competition.core.trade import FeeQuote, TradeInfo
from amm_competition.evm.executor import EVMStrategyExecutor, EVMExecutionResult, _WAD_DECIMAL
from amm_competition.evm.compiler import SolidityCompiler, CompilationResult
from amm_competition.evm.validator import SolidityValidator, ValidationResult


class EVMStrategyAdapter(AMMStrategy):
    """Adapts an EVM-based Solidity strategy to the Python AMMStrategy interface.

    This allows Solidity strategies to be used seamlessly with the existing
    Python simulation engine, match runner, and scoring system.
    """

    def __init__(
        self,
        bytecode: bytes,
        abi: Optional[list] = None,
        name: Optional[str] = None,
    ):
        """Initialize the adapter with compiled bytecode.

        Args:
            bytecode: Compiled contract deployment bytecode
            abi: Contract ABI (optional)
            name: Override name for the strategy
        """
        self._bytecode = bytecode
        self._abi = abi
        self._name_override = name
        self._executor = EVMStrategyExecutor(bytecode, abi)

        # Cache the name after first fetch
        self._cached_name: Optional[str] = None

        # Track execution metrics
        self.total_gas_used = 0
        self.call_count = 0

    @staticmethod
    def _clamp_fee_decimal(value: Decimal) -> Decimal:
        """Clamp strategy fee to [0, 0.1] as defense-in-depth."""
        if value < Decimal("0"):
            return Decimal("0")
        if value > Decimal("0.1"):
            return Decimal("0.1")
        return value

    def __reduce__(self):
        """Support pickling for multiprocessing.

        Returns the class and arguments needed to reconstruct this instance.
        The EVM executor will be recreated on unpickle.
        """
        return (
            self.__class__,
            (self._bytecode, self._abi, self._name_override),
        )

    def after_initialize(self, initial_x: Decimal, initial_y: Decimal) -> FeeQuote:
        """Initialize the strategy with starting reserves.

        Args:
            initial_x: Starting X reserve amount
            initial_y: Starting Y reserve amount

        Returns:
            FeeQuote with initial bid/ask fees

        Raises:
            RuntimeError: If EVM execution fails
        """
        result = self._executor.after_initialize(initial_x, initial_y)

        if not result.success:
            raise RuntimeError(f"Strategy afterInitialize() failed: {result.error}")

        self.total_gas_used += result.gas_used
        self.call_count += 1

        return FeeQuote(
            bid_fee=self._clamp_fee_decimal(result.bid_fee),
            ask_fee=self._clamp_fee_decimal(result.ask_fee),
        )

    def after_swap(self, trade: TradeInfo) -> FeeQuote:
        """Handle a trade event and return updated fees.

        Args:
            trade: Information about the just-executed trade

        Returns:
            FeeQuote with updated bid/ask fees

        Raises:
            RuntimeError: If EVM execution fails
        """
        # Use fast path that returns WAD values
        bid_wad, ask_wad = self._executor.after_swap_fast(trade)
        self.call_count += 1

        # Convert to Decimal only at the boundary
        return FeeQuote(
            bid_fee=self._clamp_fee_decimal(Decimal(bid_wad) / _WAD_DECIMAL),
            ask_fee=self._clamp_fee_decimal(Decimal(ask_wad) / _WAD_DECIMAL),
        )

    def after_swap_wad(self, trade: TradeInfo) -> Tuple[int, int]:
        """Fast path: handle a trade event and return WAD values.

        This avoids Decimal conversions for performance-critical paths.

        Args:
            trade: Information about the just-executed trade

        Returns:
            Tuple of (bid_fee_wad, ask_fee_wad) as integers
        """
        self.call_count += 1
        return self._executor.after_swap_fast(trade)

    def get_name(self) -> str:
        """Get the strategy name.

        Returns:
            Strategy name string
        """
        if self._name_override:
            return self._name_override

        if self._cached_name is None:
            self._cached_name = self._executor.get_name()

        return self._cached_name

    def reset(self) -> None:
        """Reset the strategy state for a new simulation.

        Call this between simulations to ensure fresh EVM state.
        """
        self._executor.reset()
        self.total_gas_used = 0
        self.call_count = 0

    @classmethod
    def from_source(
        cls,
        source_code: str,
        validate: bool = True,
        name: Optional[str] = None,
    ) -> "EVMStrategyAdapter":
        """Create an adapter from Solidity source code.

        This handles validation, compilation, and adapter creation in one step.

        Args:
            source_code: Solidity source code
            validate: Whether to run static analysis first (default: True)
            name: Override name for the strategy

        Returns:
            EVMStrategyAdapter ready for simulation

        Raises:
            ValueError: If validation fails
            RuntimeError: If compilation fails
        """
        if validate:
            validator = SolidityValidator()
            validation = validator.validate(source_code)
            if not validation.valid:
                raise ValueError(
                    f"Validation failed: {'; '.join(validation.errors)}"
                )

        compiler = SolidityCompiler()
        compilation = compiler.compile(source_code)

        if not compilation.success:
            raise RuntimeError(
                f"Compilation failed: {'; '.join(compilation.errors or [])}"
            )

        return cls(
            bytecode=compilation.bytecode,
            abi=compilation.abi,
            name=name,
        )


def load_solidity_strategy(source_code: str, validate: bool = True) -> EVMStrategyAdapter:
    """Convenience function to load a Solidity strategy.

    Args:
        source_code: Solidity source code
        validate: Whether to validate first

    Returns:
        EVMStrategyAdapter ready for use
    """
    return EVMStrategyAdapter.from_source(source_code, validate=validate)
