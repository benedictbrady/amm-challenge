// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "./IAMMStrategy.sol";

/// @title Vanilla AMM Strategy
/// @notice Default strategy with fixed 30 basis point fees
/// @dev This runs as the second AMM in simulations to normalize scoring
contract VanillaStrategy is AMMStrategyBase {
    /// @notice Fixed fee in WAD (30 bps = 0.30% = 30e14)
    uint256 public constant FEE = 30 * BPS;

    /// @inheritdoc IAMMStrategy
    function afterInitialize(uint256, uint256) external pure override returns (uint256 bidFee, uint256 askFee) {
        return (FEE, FEE);
    }

    /// @inheritdoc IAMMStrategy
    function afterSwap(TradeInfo calldata) external pure override returns (uint256 bidFee, uint256 askFee) {
        return (FEE, FEE);
    }

    /// @inheritdoc IAMMStrategy
    function getName() external pure override returns (string memory) {
        return "Vanilla_30bps";
    }
}
