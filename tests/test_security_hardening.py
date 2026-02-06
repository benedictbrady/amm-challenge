"""Security hardening regression tests."""

import multiprocessing as mp
import queue
from pathlib import Path

import pytest
import amm_sim_rs

from amm_competition.evm.compiler import SolidityCompiler
from amm_competition.evm.validator import SolidityValidator


BASE_IMPORTS = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "./IAMMStrategy.sol";
"""


def _strategy_body(body: str) -> str:
    return (
        BASE_IMPORTS
        + "\ncontract Strategy is AMMStrategyBase {\n"
        + body
        + "\n}\n"
    )


def _minimal_functions() -> str:
    return """
    function afterInitialize(uint256, uint256) external override returns (uint256 bidFee, uint256 askFee) {
        return (bpsToWad(30), bpsToWad(30));
    }

    function afterSwap(TradeInfo calldata) external override returns (uint256 bidFee, uint256 askFee) {
        return (bpsToWad(30), bpsToWad(30));
    }

    function getName() external pure override returns (string memory) {
        return "Secure";
    }
"""


def _swap_and_name_functions() -> str:
    return """
    function afterSwap(TradeInfo calldata) external override returns (uint256 bidFee, uint256 askFee) {
        return (bpsToWad(30), bpsToWad(30));
    }

    function getName() external pure override returns (string memory) {
        return "Secure";
    }
"""


def _sim_config(seed: int = 1):
    return amm_sim_rs.SimulationConfig(
        n_steps=5,
        initial_price=100.0,
        initial_x=100.0,
        initial_y=10000.0,
        gbm_mu=0.0,
        gbm_sigma=0.001,
        gbm_dt=1.0,
        retail_arrival_rate=0.8,
        retail_mean_size=20.0,
        retail_size_sigma=1.2,
        retail_buy_prob=0.5,
        seed=seed,
    )


def _deploy_worker(bytecode: bytes, abi: list, result_queue: mp.Queue) -> None:
    from amm_competition.evm.adapter import EVMStrategyAdapter

    try:
        EVMStrategyAdapter(bytecode=bytecode, abi=abi)
        result_queue.put(("ok", None))
    except Exception as e:
        result_queue.put(("err", str(e)))


def _baseline_bytecode() -> bytes:
    compiler = SolidityCompiler()
    vanilla_source = Path("contracts/src/VanillaStrategy.sol").read_text()
    result = compiler.compile(vanilla_source, contract_name="VanillaStrategy")
    assert result.success, result.errors
    return result.bytecode


def test_validator_blocks_dot_call_syntax() -> None:
    source = _strategy_body(
        """
    function afterInitialize(uint256, uint256) external override returns (uint256 bidFee, uint256 askFee) {
        (bool ok,) = address(this).call("");
        if (ok) { return (1, 1); }
        return (2, 2);
    }
"""
        + _swap_and_name_functions()
    )
    result = SolidityValidator().validate(source)
    assert not result.valid
    assert any("External calls" in err for err in result.errors)


def test_validator_blocks_memory_safe_assembly_variant() -> None:
    source = _strategy_body(
        """
    function afterInitialize(uint256, uint256) external override returns (uint256 bidFee, uint256 askFee) {
        assembly ("memory-safe") { }
        return (bpsToWad(30), bpsToWad(30));
    }
"""
        + _swap_and_name_functions()
    )
    result = SolidityValidator().validate(source)
    assert not result.valid
    assert any("assembly" in err.lower() for err in result.errors)


def test_validator_rejects_path_traversal_import() -> None:
    source = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "AMMStrategyBase.sol/../README.md";
import {IAMMStrategy, TradeInfo} from "./IAMMStrategy.sol";
contract Strategy is AMMStrategyBase {
    function afterInitialize(uint256, uint256) external pure returns (uint256, uint256) { return (0, 0); }
    function afterSwap(TradeInfo calldata) external pure returns (uint256, uint256) { return (0, 0); }
    function getName() external pure returns (string memory) { return "x"; }
}
"""
    result = SolidityValidator().validate(source)
    assert not result.valid
    assert any("not allowed" in err for err in result.errors)


def test_validator_accepts_parent_relative_base_imports() -> None:
    source = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {AMMStrategyBase} from "../AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "../IAMMStrategy.sol";
contract Strategy is AMMStrategyBase {
    function afterInitialize(uint256, uint256) external pure returns (uint256, uint256) { return (0, 0); }
    function afterSwap(TradeInfo calldata) external pure returns (uint256, uint256) { return (0, 0); }
    function getName() external pure returns (string memory) { return "x"; }
}
"""
    result = SolidityValidator().validate(source)
    assert result.valid


def test_validator_rejects_reserved_name_redeclaration() -> None:
    source = (
        BASE_IMPORTS
        + """
contract AMMStrategyBase {}
contract Strategy is AMMStrategyBase {
    function afterInitialize(uint256, uint256) external pure returns (uint256, uint256) { return (0, 0); }
    function afterSwap(TradeInfo calldata) external pure returns (uint256, uint256) { return (0, 0); }
    function getName() external pure returns (string memory) { return "x"; }
}
"""
    )
    result = SolidityValidator().validate(source)
    assert not result.valid
    assert any("Redefining reserved identifier" in err for err in result.errors)


def test_validator_rejects_commented_inheritance_spoof() -> None:
    source = (
        BASE_IMPORTS
        + """
// contract Strategy is AMMStrategyBase
contract Strategy is IAMMStrategy {
    function afterInitialize(uint256, uint256) external pure returns (uint256, uint256) { return (0, 0); }
    function afterSwap(TradeInfo calldata) external pure returns (uint256, uint256) { return (0, 0); }
    function getName() external pure returns (string memory) { return "x"; }
}
"""
    )
    result = SolidityValidator().validate(source)
    assert not result.valid
    assert any("inherit from AMMStrategyBase" in err for err in result.errors)


def test_compiler_rejects_forbidden_runtime_opcodes() -> None:
    source = _strategy_body(
        """
    function afterInitialize(uint256, uint256) external override returns (uint256 bidFee, uint256 askFee) {
        (bool ok,) = address(this).call("");
        if (ok) { return (1, 1); }
        return (2, 2);
    }
"""
        + _swap_and_name_functions()
    )
    result = SolidityCompiler().compile(source)
    assert not result.success
    assert any("forbidden opcodes" in err.lower() for err in (result.errors or []))


def test_compiler_rejects_forbidden_creation_opcodes() -> None:
    source = _strategy_body(
        """
    constructor() {
        IAMMStrategy(address(0x0000000000000000000000000000000000000004)).getName();
    }
"""
        + _minimal_functions()
    )
    result = SolidityCompiler().compile(source)
    assert not result.success
    assert any("creation bytecode contains forbidden opcodes" in err.lower() for err in (result.errors or []))


def test_compiler_rejects_storage_outside_slots() -> None:
    source = _strategy_body(
        """
    uint256 private hacked;
"""
        + _minimal_functions()
    )
    result = SolidityCompiler().compile(source)
    assert not result.success
    assert any("storage outside" in err.lower() for err in (result.errors or []))


def test_rust_engine_rejects_out_of_range_fee_returns() -> None:
    huge_fee = "170141183460469231731687303715884105728"  # 2^127
    source = _strategy_body(
        f"""
    function afterInitialize(uint256, uint256) external pure override returns (uint256 bidFee, uint256 askFee) {{
        return ({huge_fee}, {huge_fee});
    }}

    function afterSwap(TradeInfo calldata) external pure override returns (uint256 bidFee, uint256 askFee) {{
        return ({huge_fee}, {huge_fee});
    }}

    function getName() external pure override returns (string memory) {{
        return "overflow";
    }}
"""
    )
    compiler = SolidityCompiler()
    submission = compiler.compile(source)
    assert submission.success, submission.errors

    with pytest.raises(Exception):
        amm_sim_rs.run_single(
            list(submission.bytecode),
            list(_baseline_bytecode()),
            _sim_config(),
        )


def test_python_executor_deploy_is_bounded_for_infinite_constructor() -> None:
    source = _strategy_body(
        """
    constructor() {
        while (true) { }
    }
"""
        + _minimal_functions()
    )
    compilation = SolidityCompiler().compile(source)
    assert compilation.success, compilation.errors

    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_deploy_worker,
        args=(compilation.bytecode, compilation.abi, result_queue),
        daemon=True,
    )
    process.start()
    process.join(8)

    if process.is_alive():
        process.terminate()
        process.join(2)
        pytest.fail("EVM deployment hung on infinite constructor")

    try:
        status, _ = result_queue.get(timeout=2)
    except queue.Empty as e:
        raise AssertionError("Deployment worker returned no result") from e

    assert status == "err"
