// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "./IAMMStrategy.sol";

/// @title Starter Strategy - 50 Basis Points
/// @notice A starting point with fixed 50 bps fees. Copy and modify this file.
contract Strategy is AMMStrategyBase {
    uint256 public constant FEE = 50 * BPS;

    function afterInitialize(uint256, uint256) external pure override returns (uint256, uint256) {
        return (FEE, FEE);
    }

    function afterSwap(TradeInfo calldata) external pure override returns (uint256, uint256) {
        return (FEE, FEE);
    }

    function getName() external pure override returns (string memory) {
        return "StarterStrategy";
    }
}
