"""EVM strategy executor using pyrevm."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from pyrevm import EVM

from amm_competition.core.trade import TradeInfo


@dataclass
class EVMExecutionResult:
    """Result of an EVM strategy execution."""

    bid_fee: Decimal
    ask_fee: Decimal
    gas_used: int
    success: bool
    error: Optional[str] = None


# Pre-computed constants for fast path
_WAD = 10**18
_WAD_DECIMAL = Decimal(_WAD)


class EVMStrategyExecutor:
    """Executes Solidity AMM strategies using pyrevm.

    Provides gas-limited execution and tracks storage usage.
    """

    # Gas limits
    GAS_LIMIT_DEPLOY = 10_000_000
    GAS_LIMIT_INIT = 250_000
    GAS_LIMIT_TRADE = 250_000

    # Maximum storage slots (enforced by contract, but we track for reporting)
    MAX_STORAGE_SLOTS = 32

    # WAD precision (1e18)
    WAD = 10**18

    # Contract addresses
    STRATEGY_ADDRESS = "0x1000000000000000000000000000000000000001"
    CALLER_ADDRESS = "0x2000000000000000000000000000000000000002"

    # Function selectors (first 4 bytes of keccak256 of function signature)
    # afterInitialize(uint256,uint256) -> 0x837aef47
    # afterSwap((bool,uint256,uint256,uint256,uint256,uint256)) -> 0xc2babb57
    # getName() -> 0x17d7de7c
    SELECTOR_AFTER_INITIALIZE = bytes.fromhex("837aef47")
    SELECTOR_AFTER_SWAP = bytes.fromhex("c2babb57")
    SELECTOR_GET_NAME = bytes.fromhex("17d7de7c")

    def __init__(self, bytecode: bytes, abi: Optional[list] = None):
        """Initialize the executor with compiled bytecode.

        Args:
            bytecode: Compiled contract bytecode (deployment bytecode)
            abi: Contract ABI for encoding/decoding (optional, we use manual encoding)
        """
        self.bytecode = bytecode
        self.abi = abi
        self.evm: Optional[EVM] = None
        self.deployed_address: Optional[str] = None

        # Pre-allocated calldata buffer for after_swap (reused across calls)
        self._trade_calldata = bytearray(196)
        self._trade_calldata[0:4] = self.SELECTOR_AFTER_SWAP

        self._deploy()

    def _deploy(self) -> None:
        """Deploy the strategy contract to the EVM."""
        # Create a fresh EVM instance
        self.evm = EVM()

        # Deploy the contract
        # pyrevm deploy returns the deployed address
        self.deployed_address = self.evm.deploy(
            deployer=self.CALLER_ADDRESS,
            code=self.bytecode,
            value=0,
            gas=self.GAS_LIMIT_DEPLOY,
        )

    def _encode_uint256(self, value: int) -> bytes:
        """Encode a uint256 value as 32 bytes."""
        return value.to_bytes(32, byteorder="big")

    def _encode_bool(self, value: bool) -> bytes:
        """Encode a bool as 32 bytes."""
        return self._encode_uint256(1 if value else 0)

    def _decode_uint256(self, data: bytes, offset: int = 0) -> int:
        """Decode a uint256 from bytes."""
        return int.from_bytes(data[offset : offset + 32], byteorder="big")

    def _decimal_to_wad(self, value: Decimal) -> int:
        """Convert a Decimal fee to WAD representation."""
        # Fees are expressed as decimals (e.g., 0.003 = 30 bps)
        # WAD representation: 0.003 * 1e18 = 30e14
        return int(value * self.WAD)

    def _wad_to_decimal(self, value: int) -> Decimal:
        """Convert a WAD value to Decimal."""
        return Decimal(value) / Decimal(self.WAD)

    def after_initialize(self, initial_x: Decimal, initial_y: Decimal) -> EVMExecutionResult:
        """Call the strategy's afterInitialize function.

        Args:
            initial_x: Starting X reserve amount
            initial_y: Starting Y reserve amount

        Returns:
            EVMExecutionResult with bid/ask fees and gas usage
        """
        # Convert to WAD
        x_wad = self._decimal_to_wad(initial_x)
        y_wad = self._decimal_to_wad(initial_y)

        # Encode calldata: selector + initialX + initialY
        calldata = (
            self.SELECTOR_AFTER_INITIALIZE + self._encode_uint256(x_wad) + self._encode_uint256(y_wad)
        )

        try:
            # Execute with gas limit
            result = self.evm.message_call(
                caller=self.CALLER_ADDRESS,
                to=self.deployed_address,
                calldata=calldata,
                value=0,
                gas=self.GAS_LIMIT_INIT,
            )

            # Decode return data: (uint256 bidFee, uint256 askFee)
            if len(result) < 64:
                return EVMExecutionResult(
                    bid_fee=Decimal(0),
                    ask_fee=Decimal(0),
                    gas_used=self.GAS_LIMIT_INIT,
                    success=False,
                    error=f"Invalid return data length: {len(result)}",
                )

            bid_fee_wad = self._decode_uint256(result, 0)
            ask_fee_wad = self._decode_uint256(result, 32)

            # Calculate gas used (pyrevm doesn't directly report this,
            # so we estimate based on successful execution)
            gas_used = self.GAS_LIMIT_INIT // 2  # Rough estimate

            return EVMExecutionResult(
                bid_fee=self._wad_to_decimal(bid_fee_wad),
                ask_fee=self._wad_to_decimal(ask_fee_wad),
                gas_used=gas_used,
                success=True,
            )

        except Exception as e:
            return EVMExecutionResult(
                bid_fee=Decimal(0),
                ask_fee=Decimal(0),
                gas_used=self.GAS_LIMIT_INIT,
                success=False,
                error=str(e),
            )

    def after_swap_fast(self, trade: TradeInfo) -> Tuple[int, int]:
        """Fast path: call afterSwap and return raw WAD values.

        Returns:
            Tuple of (bid_fee_wad, ask_fee_wad) as integers.
            Raises RuntimeError on EVM errors or malformed returns.
        """
        # Reuse pre-allocated calldata buffer
        calldata = self._trade_calldata

        # Clear and set bool isBuy (offset 4-35, with value at byte 35)
        calldata[4:36] = b'\x00' * 32
        if trade.side == "buy":
            calldata[35] = 1

        # Convert Decimals to WAD and encode
        wad = _WAD

        # amountX at offset 36
        val = int(trade.amount_x * wad)
        calldata[36:68] = val.to_bytes(32, 'big')

        # amountY at offset 68
        val = int(trade.amount_y * wad)
        calldata[68:100] = val.to_bytes(32, 'big')

        # timestamp at offset 100
        calldata[100:132] = trade.timestamp.to_bytes(32, 'big')

        # reserveX at offset 132
        val = int(trade.reserve_x * wad)
        calldata[132:164] = val.to_bytes(32, 'big')

        # reserveY at offset 164
        val = int(trade.reserve_y * wad)
        calldata[164:196] = val.to_bytes(32, 'big')

        try:
            result = self.evm.message_call(
                caller=self.CALLER_ADDRESS,
                to=self.deployed_address,
                calldata=bytes(calldata),
                value=0,
                gas=self.GAS_LIMIT_TRADE,
            )

            if len(result) < 64:
                raise RuntimeError(f"Invalid return data length: {len(result)}")

            # Decode return values directly as integers
            bid_fee_wad = int.from_bytes(result[0:32], 'big')
            ask_fee_wad = int.from_bytes(result[32:64], 'big')
            return (bid_fee_wad, ask_fee_wad)

        except Exception as e:
            raise RuntimeError(f"afterSwap failed: {e}") from e

    def after_swap(self, trade: TradeInfo) -> EVMExecutionResult:
        """Call the strategy's afterSwap function."""
        try:
            # Use fast path and convert to Decimal
            bid_wad, ask_wad = self.after_swap_fast(trade)
            return EVMExecutionResult(
                bid_fee=Decimal(bid_wad) / _WAD_DECIMAL,
                ask_fee=Decimal(ask_wad) / _WAD_DECIMAL,
                gas_used=self.GAS_LIMIT_TRADE // 2,
                success=True,
            )
        except Exception as e:
            return EVMExecutionResult(
                bid_fee=Decimal(0),
                ask_fee=Decimal(0),
                gas_used=self.GAS_LIMIT_TRADE,
                success=False,
                error=str(e),
            )

    def get_name(self) -> str:
        """Call the strategy's getName function.

        Returns:
            Strategy name string
        """
        try:
            result = self.evm.message_call(
                caller=self.CALLER_ADDRESS,
                to=self.deployed_address,
                calldata=self.SELECTOR_GET_NAME,
                value=0,
                gas=50_000,  # getName should be cheap
            )

            # Decode string return value
            # String is encoded as: offset (32 bytes) + length (32 bytes) + data
            if len(result) < 64:
                return "Unknown"

            offset = self._decode_uint256(result, 0)
            length = self._decode_uint256(result, offset)
            string_data = result[offset + 32 : offset + 32 + length]

            return string_data.decode("utf-8")

        except Exception:
            return "Unknown"

    def reset(self) -> None:
        """Reset the EVM state by redeploying the contract.

        Call this between simulations to ensure fresh state.
        """
        self._deploy()
