"""AMM strategy interface that users implement."""

from abc import ABC, abstractmethod
from decimal import Decimal

from amm_competition.core.trade import FeeQuote, TradeInfo


class AMMStrategy(ABC):
    """Abstract base class for AMM fee strategies.

    Users implement this interface to create custom fee algorithms.
    Strategies only receive trade information - no external prices,
    no other AMM states, no time-based callbacks.

    This mirrors real smart contract constraints where contracts
    only execute when called via transactions.
    """

    @abstractmethod
    def after_initialize(self, initial_x: Decimal, initial_y: Decimal) -> FeeQuote:
        """Called once at the start of simulation with initial reserves.

        Args:
            initial_x: Starting X reserve amount
            initial_y: Starting Y reserve amount

        Returns:
            FeeQuote with initial bid/ask fees for the first trade
        """
        pass

    @abstractmethod
    def after_swap(self, trade: TradeInfo) -> FeeQuote:
        """Called after each trade against this AMM.

        This is the only callback strategies receive. Use it to update
        internal state and return fees for the next trade.

        Args:
            trade: Information about the just-executed trade

        Returns:
            FeeQuote with updated bid/ask fees for the next trade
        """
        pass

    def get_name(self) -> str:
        """Return the strategy name for display purposes."""
        return self.__class__.__name__
