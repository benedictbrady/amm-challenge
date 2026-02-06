"""Constant product AMM engine with dynamic fees."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from amm_competition.core.interfaces import AMMStrategy
from amm_competition.core.trade import FeeQuote, TradeInfo, TradeSide


@dataclass
class Quote:
    """A quote for a potential trade."""
    side: TradeSide
    amount_in: Decimal
    amount_out: Decimal
    fee_rate: Decimal
    fee_amount: Decimal
    effective_price: Decimal  # Price including fees


@dataclass
class AMM:
    """Constant product AMM with strategy-determined fees.

    Implements x * y = k invariant with configurable fee strategies.
    Uses Uniswap V2 fee model:
    - Swap validation uses fee-adjusted input: (x + γ·Δx)(y - Δy) ≥ k
    - But FULL input (including fee) goes to reserves
    - Result: k grows over time as fees accumulate (LP compensation)
    """
    strategy: AMMStrategy
    reserve_x: Decimal
    reserve_y: Decimal
    name: str = ""
    current_fees: FeeQuote = field(init=False)
    _initialized: bool = field(default=False, init=False)
    # Performance optimization: batch after_swap calls
    _pending_trade: Optional[TradeInfo] = field(default=None, init=False)
    _trade_count: int = field(default=0, init=False)
    # Only call after_swap every N trades (0 = every trade, higher = less frequent)
    fee_update_interval: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.strategy.get_name()

    def initialize(self) -> None:
        """Initialize the AMM and get starting fees from strategy."""
        self.current_fees = self.strategy.after_initialize(self.reserve_x, self.reserve_y)
        self._initialized = True
        self._trade_count = 0
        self._pending_trade = None

    def set_fee_update_interval(self, interval: int) -> None:
        """Set how often to update fees (0 = every trade, N = every Nth trade)."""
        self.fee_update_interval = interval

    def flush(self) -> None:
        """Force a fee update if there's a pending trade."""
        if self._pending_trade is not None:
            self.current_fees = self.strategy.after_swap(self._pending_trade)
            self._pending_trade = None

    def _maybe_update_fees(self, trade_info: TradeInfo) -> None:
        """Update fees, respecting the update interval for performance."""
        self._trade_count += 1
        self._pending_trade = trade_info

        if self.fee_update_interval == 0:
            # Update every trade (original behavior)
            self.current_fees = self.strategy.after_swap(trade_info)
            self._pending_trade = None
        elif self._trade_count % self.fee_update_interval == 0:
            # Update every Nth trade
            self.current_fees = self.strategy.after_swap(trade_info)
            self._pending_trade = None
        # Otherwise, defer the update

    @property
    def k(self) -> Decimal:
        """The constant product invariant."""
        return self.reserve_x * self.reserve_y

    def _fast_quote_buy_x(self, amount_x_f: float) -> tuple[float, float]:
        """Fast quote for AMM buying X. Returns (y_out, fee_amount) as floats."""
        if amount_x_f <= 0:
            return 0.0, 0.0
        fee_f = float(self.current_fees.bid_fee)
        gamma = 1.0 - fee_f
        net_x = amount_x_f * gamma
        rx, ry = float(self.reserve_x), float(self.reserve_y)
        k = rx * ry
        new_rx = rx + net_x
        new_ry = k / new_rx
        y_out = ry - new_ry
        return (y_out, amount_x_f * fee_f) if y_out > 0 else (0.0, 0.0)

    def _fast_quote_sell_x(self, amount_x_f: float) -> tuple[float, float]:
        """Fast quote for AMM selling X. Returns (total_y_in, fee_amount) as floats."""
        rx = float(self.reserve_x)
        if amount_x_f <= 0 or amount_x_f >= rx:
            return 0.0, 0.0
        ry = float(self.reserve_y)
        k = rx * ry
        fee_f = float(self.current_fees.ask_fee)
        gamma = 1.0 - fee_f
        new_rx = rx - amount_x_f
        new_ry = k / new_rx
        net_y = new_ry - ry
        if net_y <= 0:
            return 0.0, 0.0
        total_y = net_y / gamma
        return total_y, total_y - net_y

    def _fast_quote_x_for_y(self, amount_y_f: float) -> tuple[float, float]:
        """Fast quote for Y input to X output. Returns (x_out, fee_amount) as floats."""
        if amount_y_f <= 0:
            return 0.0, 0.0
        rx, ry = float(self.reserve_x), float(self.reserve_y)
        k = rx * ry
        fee_f = float(self.current_fees.ask_fee)
        gamma = 1.0 - fee_f
        net_y = amount_y_f * gamma
        new_ry = ry + net_y
        new_rx = k / new_ry
        x_out = rx - new_rx
        return (x_out, amount_y_f * fee_f) if x_out > 0 else (0.0, 0.0)

    @property
    def spot_price(self) -> Decimal:
        """Current spot price (Y per X) before fees."""
        if self.reserve_x == 0:
            return Decimal("0")
        return self.reserve_y / self.reserve_x

    def get_quote_buy_x(self, amount_x: Decimal) -> Optional[Quote]:
        """Get quote for AMM buying X (trader selling X).

        Uses Uniswap v2 fee-on-input model: γ = 1 - f
        Only γ * amount_x goes to reserves, fee is taken from input.

        Args:
            amount_x: Amount of X the trader wants to sell

        Returns:
            Quote with amount of Y trader receives, or None if invalid
        """
        if not self._initialized:
            raise RuntimeError("AMM not initialized. Call initialize() first.")

        if amount_x <= 0:
            return None

        # Uniswap v2 fee-on-input: γ = 1 - f
        fee_rate = self.current_fees.bid_fee
        gamma = Decimal("1") - fee_rate
        fee_amount = amount_x * fee_rate
        net_amount_x = amount_x * gamma  # Only this goes to reserves

        # Calculate Y output using constant product formula
        # (reserve_x + net_x) * (reserve_y - amount_y) = k
        new_reserve_x = self.reserve_x + net_amount_x
        new_reserve_y = self.k / new_reserve_x
        amount_y = self.reserve_y - new_reserve_y

        if amount_y <= 0:
            return None

        effective_price = amount_y / amount_x

        return Quote(
            side=TradeSide.BUY,
            amount_in=amount_x,
            amount_out=amount_y,
            fee_rate=fee_rate,
            fee_amount=fee_amount,
            effective_price=effective_price,
        )

    def get_quote_sell_x(self, amount_x: Decimal) -> Optional[Quote]:
        """Get quote for AMM selling X (trader buying X).

        Uses Uniswap v2 fee-on-input model: γ = 1 - f
        Trader pays Y, only γ * Y goes to reserves.
        We solve for total Y such that γ * Y added to reserves gives amount_x out.

        Args:
            amount_x: Amount of X the trader wants to buy

        Returns:
            Quote with amount of Y trader must pay, or None if invalid
        """
        if not self._initialized:
            raise RuntimeError("AMM not initialized. Call initialize() first.")

        if amount_x <= 0 or amount_x >= self.reserve_x:
            return None

        # Calculate net Y needed in reserves using constant product formula
        # (reserve_x - amount_x) * (reserve_y + net_y) = k
        new_reserve_x = self.reserve_x - amount_x
        new_reserve_y = self.k / new_reserve_x
        net_amount_y = new_reserve_y - self.reserve_y

        if net_amount_y <= 0:
            return None

        # Uniswap v2 fee-on-input: trader pays Y, only γ*Y goes to reserves
        # net_y = γ * total_y => total_y = net_y / γ = net_y / (1 - f)
        fee_rate = self.current_fees.ask_fee
        gamma = Decimal("1") - fee_rate
        total_amount_y = net_amount_y / gamma
        fee_amount = total_amount_y - net_amount_y

        effective_price = total_amount_y / amount_x

        return Quote(
            side=TradeSide.SELL,
            amount_in=total_amount_y,
            amount_out=amount_x,
            fee_rate=fee_rate,
            fee_amount=fee_amount,
            effective_price=effective_price,
        )

    def get_amount_x_for_y_input(self, amount_y: Decimal) -> Optional[Quote]:
        """Get quote for trader paying Y to receive X.

        Uses Uniswap v2 fee-on-input model: γ = 1 - f
        Trader pays Y, only γ * Y goes to reserves.

        Args:
            amount_y: Amount of Y the trader is paying

        Returns:
            Quote with amount of X trader receives, or None if invalid
        """
        if not self._initialized:
            raise RuntimeError("AMM not initialized. Call initialize() first.")

        if amount_y <= 0:
            return None

        # Uniswap v2 fee-on-input: γ = 1 - f, net_y = γ * amount_y
        fee_rate = self.current_fees.ask_fee
        gamma = Decimal("1") - fee_rate
        fee_amount = amount_y * fee_rate
        net_amount_y = amount_y * gamma

        # Calculate X output
        new_reserve_y = self.reserve_y + net_amount_y
        new_reserve_x = self.k / new_reserve_y
        amount_x = self.reserve_x - new_reserve_x

        if amount_x <= 0:
            return None

        effective_price = amount_y / amount_x

        return Quote(
            side=TradeSide.SELL,
            amount_in=amount_y,
            amount_out=amount_x,
            fee_rate=fee_rate,
            fee_amount=fee_amount,
            effective_price=effective_price,
        )

    def execute_buy_x(self, amount_x: Decimal, timestamp: int) -> Optional[TradeInfo]:
        """Execute trade where AMM buys X (trader sells X for Y)."""
        # Use fast float math for quote
        y_out, _ = self._fast_quote_buy_x(float(amount_x))
        if y_out <= 0:
            return None

        amount_y = Decimal(str(y_out))

        # Update reserves
        self.reserve_x += amount_x
        self.reserve_y -= amount_y

        trade_info = TradeInfo(
            side="buy",
            amount_x=amount_x,
            amount_y=amount_y,
            timestamp=timestamp,
            reserve_x=self.reserve_x,
            reserve_y=self.reserve_y,
        )

        self._maybe_update_fees(trade_info)
        return trade_info

    def execute_sell_x(self, amount_x: Decimal, timestamp: int) -> Optional[TradeInfo]:
        """Execute trade where AMM sells X (trader buys X with Y)."""
        # Use fast float math
        total_y, _ = self._fast_quote_sell_x(float(amount_x))
        if total_y <= 0:
            return None

        amount_y = Decimal(str(total_y))

        self.reserve_x -= amount_x
        self.reserve_y += amount_y

        trade_info = TradeInfo(
            side="sell",
            amount_x=amount_x,
            amount_y=amount_y,
            timestamp=timestamp,
            reserve_x=self.reserve_x,
            reserve_y=self.reserve_y,
        )

        self._maybe_update_fees(trade_info)
        return trade_info

    def execute_buy_x_with_y(self, amount_y: Decimal, timestamp: int) -> Optional[TradeInfo]:
        """Execute trade where trader pays Y to receive X."""
        # Use fast float math
        x_out, _ = self._fast_quote_x_for_y(float(amount_y))
        if x_out <= 0:
            return None

        amount_x = Decimal(str(x_out))

        self.reserve_x -= amount_x
        self.reserve_y += amount_y

        trade_info = TradeInfo(
            side="sell",
            amount_x=amount_x,
            amount_y=amount_y,
            timestamp=timestamp,
            reserve_x=self.reserve_x,
            reserve_y=self.reserve_y,
        )

        self._maybe_update_fees(trade_info)
        return trade_info
