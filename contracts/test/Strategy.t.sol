// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import {VanillaStrategy} from "../src/VanillaStrategy.sol";
import {AMMStrategyBase} from "../src/AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "../src/IAMMStrategy.sol";

contract StrategyTest is Test {
    VanillaStrategy public vanilla;

    function setUp() public {
        vanilla = new VanillaStrategy();
    }

    function test_VanillaAfterInitialize() public {
        uint256 initialX = 100e18;
        uint256 initialY = 10000e18;

        (uint256 bidFee, uint256 askFee) = vanilla.afterInitialize(initialX, initialY);

        // 30 bps = 30 * 1e14 = 30e14
        assertEq(bidFee, 30e14, "Bid fee should be 30 bps");
        assertEq(askFee, 30e14, "Ask fee should be 30 bps");
    }

    function test_VanillaOnTrade() public {
        TradeInfo memory trade = TradeInfo({
            isBuy: true,
            amountX: 1e18,
            amountY: 100e18,
            timestamp: 1,
            reserveX: 101e18,
            reserveY: 9900e18
        });

        (uint256 bidFee, uint256 askFee) = vanilla.afterSwap(trade);

        assertEq(bidFee, 30e14, "Bid fee should be 30 bps");
        assertEq(askFee, 30e14, "Ask fee should be 30 bps");
    }

    function test_VanillaGetName() public view {
        string memory name = vanilla.getName();
        assertEq(name, "Vanilla_30bps");
    }

    function test_Constants() public view {
        assertEq(vanilla.WAD(), 1e18, "WAD should be 1e18");
        assertEq(vanilla.MAX_FEE(), 1e17, "MAX_FEE should be 10%");
        assertEq(vanilla.BPS(), 1e14, "BPS should be 1e14");
    }

    function test_SlotsInitializedToZero() public view {
        for (uint256 i = 0; i < 32; i++) {
            assertEq(vanilla.slots(i), 0, "Slots should be initialized to zero");
        }
    }
}

/// @notice Test contract to verify helper functions work correctly
contract HelperFunctionsTest is AMMStrategyBase {
    function afterInitialize(uint256, uint256) external pure override returns (uint256, uint256) {
        return (0, 0);
    }

    function afterSwap(TradeInfo calldata) external pure override returns (uint256, uint256) {
        return (0, 0);
    }

    function getName() external pure override returns (string memory) {
        return "HelperTest";
    }

    // Expose internal functions for testing (prefixed "expose" to avoid Forge fuzz discovery)
    function exposeWmul(uint256 x, uint256 y) external pure returns (uint256) {
        return wmul(x, y);
    }

    function exposeWdiv(uint256 x, uint256 y) external pure returns (uint256) {
        return wdiv(x, y);
    }

    function exposeClamp(uint256 value, uint256 minVal, uint256 maxVal) external pure returns (uint256) {
        return clamp(value, minVal, maxVal);
    }

    function exposeBpsToWad(uint256 bps) external pure returns (uint256) {
        return bpsToWad(bps);
    }

    function exposeWadToBps(uint256 wadValue) external pure returns (uint256) {
        return wadToBps(wadValue);
    }

    function exposeClampFee(uint256 fee) external pure returns (uint256) {
        return clampFee(fee);
    }

    function exposeAbsDiff(uint256 a, uint256 b) external pure returns (uint256) {
        return absDiff(a, b);
    }

    function exposeSqrt(uint256 x) external pure returns (uint256) {
        return sqrt(x);
    }

    function exposeReadSlot(uint256 index) external view returns (uint256) {
        return readSlot(index);
    }

    function exposeWriteSlot(uint256 index, uint256 value) external {
        writeSlot(index, value);
    }
}

contract HelperTest is Test {
    HelperFunctionsTest public helper;

    function setUp() public {
        helper = new HelperFunctionsTest();
    }

    function test_Wmul() public view {
        // 2 WAD * 3 WAD = 6 WAD
        uint256 result = helper.exposeWmul(2e18, 3e18);
        assertEq(result, 6e18);

        // 0.5 WAD * 0.5 WAD = 0.25 WAD
        result = helper.exposeWmul(5e17, 5e17);
        assertEq(result, 25e16);
    }

    function test_Wdiv() public view {
        // 6 WAD / 2 WAD = 3 WAD
        uint256 result = helper.exposeWdiv(6e18, 2e18);
        assertEq(result, 3e18);

        // 1 WAD / 4 WAD = 0.25 WAD
        result = helper.exposeWdiv(1e18, 4e18);
        assertEq(result, 25e16);
    }

    function test_Clamp() public view {
        // Value in range
        assertEq(helper.exposeClamp(50, 0, 100), 50);
        // Value below min
        assertEq(helper.exposeClamp(0, 10, 100), 10);
        // Value above max
        assertEq(helper.exposeClamp(150, 0, 100), 100);
    }

    function test_BpsToWad() public view {
        // 25 bps = 25 * 1e14 = 25e14
        assertEq(helper.exposeBpsToWad(25), 25e14);
        // 100 bps (1%) = 100 * 1e14 = 1e16
        assertEq(helper.exposeBpsToWad(100), 1e16);
        // 10000 bps (100%) = 10000 * 1e14 = 1e18
        assertEq(helper.exposeBpsToWad(10000), 1e18);
    }

    function test_WadToBps() public view {
        assertEq(helper.exposeWadToBps(25e14), 25);
        assertEq(helper.exposeWadToBps(1e16), 100);
        assertEq(helper.exposeWadToBps(1e18), 10000);
    }

    function test_ClampFee() public view {
        // Valid fee
        assertEq(helper.exposeClampFee(25e14), 25e14);
        // Above max (10% = 1e17)
        assertEq(helper.exposeClampFee(2e17), 1e17);
        // Zero is valid
        assertEq(helper.exposeClampFee(0), 0);
    }

    function test_AbsDiff() public view {
        assertEq(helper.exposeAbsDiff(10, 7), 3);
        assertEq(helper.exposeAbsDiff(7, 10), 3);
        assertEq(helper.exposeAbsDiff(5, 5), 0);
    }

    function test_Sqrt() public view {
        assertEq(helper.exposeSqrt(0), 0);
        assertEq(helper.exposeSqrt(1), 1);
        assertEq(helper.exposeSqrt(4), 2);
        assertEq(helper.exposeSqrt(9), 3);
        assertEq(helper.exposeSqrt(100), 10);
        // Non-perfect square rounds down
        assertEq(helper.exposeSqrt(10), 3);
    }

    function test_SlotReadWrite() public {
        // Initial value is zero
        assertEq(helper.exposeReadSlot(0), 0);

        // Write and read back
        helper.exposeWriteSlot(5, 12345);
        assertEq(helper.exposeReadSlot(5), 12345);

        // Write to last slot
        helper.exposeWriteSlot(31, 99999);
        assertEq(helper.exposeReadSlot(31), 99999);
    }

    function test_SlotOutOfBounds() public {
        vm.expectRevert("Slot index out of bounds");
        helper.exposeReadSlot(32);

        vm.expectRevert("Slot index out of bounds");
        helper.exposeWriteSlot(32, 100);
    }
}
