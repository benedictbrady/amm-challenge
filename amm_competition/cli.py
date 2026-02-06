"""Command-line interface for running AMM simulations."""

import argparse
import sys
from pathlib import Path

from amm_competition.competition.match import MatchRunner, HyperparameterVariance
from amm_competition.evm.adapter import EVMStrategyAdapter
from amm_competition.evm.baseline import load_vanilla_strategy
from amm_competition.evm.compiler import SolidityCompiler
from amm_competition.evm.validator import SolidityValidator
import amm_sim_rs

from amm_competition.competition.config import (
    BASELINE_SETTINGS,
    BASELINE_VARIANCE,
    baseline_nominal_retail_rate,
    baseline_nominal_retail_size,
    baseline_nominal_sigma,
    resolve_n_workers,
)


def run_match_command(args: argparse.Namespace) -> int:
    """Run simulations for a strategy and report its score."""
    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Error: Strategy file not found: {strategy_path}")
        return 1

    # Read Solidity source
    source_code = strategy_path.read_text()

    # Validate
    print("Validating strategy...")
    validator = SolidityValidator()
    validation = validator.validate(source_code)
    if not validation.valid:
        print("Validation failed:")
        for error in validation.errors:
            print(f"  - {error}")
        return 1

    # Compile
    print("Compiling strategy...")
    compiler = SolidityCompiler()
    compilation = compiler.compile(source_code)
    if not compilation.success:
        print("Compilation failed:")
        for error in (compilation.errors or []):
            print(f"  - {error}")
        return 1

    # Create strategy adapter
    user_strategy = EVMStrategyAdapter(
        bytecode=compilation.bytecode,
        abi=compilation.abi,
    )
    strategy_name = user_strategy.get_name()
    print(f"Strategy: {strategy_name}")

    # Load default 30bps strategy (used as the other AMM in simulation)
    default_strategy = load_vanilla_strategy()

    # Configure simulation
    n_steps = args.steps if args.steps is not None else BASELINE_SETTINGS.n_steps
    initial_price = (
        args.initial_price if args.initial_price is not None else BASELINE_SETTINGS.initial_price
    )
    initial_x = args.initial_x if args.initial_x is not None else BASELINE_SETTINGS.initial_x
    initial_y = args.initial_y if args.initial_y is not None else BASELINE_SETTINGS.initial_y
    gbm_sigma = args.volatility if args.volatility is not None else baseline_nominal_sigma()
    retail_rate = (
        args.retail_rate if args.retail_rate is not None else baseline_nominal_retail_rate()
    )
    retail_size = (
        args.retail_size if args.retail_size is not None else baseline_nominal_retail_size()
    )
    retail_size_sigma = (
        args.retail_size_sigma
        if args.retail_size_sigma is not None
        else BASELINE_SETTINGS.retail_size_sigma
    )

    config = amm_sim_rs.SimulationConfig(
        n_steps=n_steps,
        initial_price=initial_price,
        initial_x=initial_x,
        initial_y=initial_y,
        gbm_mu=BASELINE_SETTINGS.gbm_mu,
        gbm_sigma=gbm_sigma,
        gbm_dt=BASELINE_SETTINGS.gbm_dt,
        retail_arrival_rate=retail_rate,
        retail_mean_size=retail_size,
        retail_size_sigma=retail_size_sigma,
        retail_buy_prob=BASELINE_SETTINGS.retail_buy_prob,
        seed=None,
    )

    # Run simulations
    n_simulations = (
        args.simulations if args.simulations is not None else BASELINE_SETTINGS.n_simulations
    )
    print(f"\nRunning {n_simulations} simulations...")
    variance = HyperparameterVariance(
        retail_mean_size_min=retail_size if args.retail_size is not None else BASELINE_VARIANCE.retail_mean_size_min,
        retail_mean_size_max=retail_size if args.retail_size is not None else BASELINE_VARIANCE.retail_mean_size_max,
        vary_retail_mean_size=False if args.retail_size is not None else BASELINE_VARIANCE.vary_retail_mean_size,
        retail_arrival_rate_min=retail_rate if args.retail_rate is not None else BASELINE_VARIANCE.retail_arrival_rate_min,
        retail_arrival_rate_max=retail_rate if args.retail_rate is not None else BASELINE_VARIANCE.retail_arrival_rate_max,
        vary_retail_arrival_rate=False if args.retail_rate is not None else BASELINE_VARIANCE.vary_retail_arrival_rate,
        gbm_sigma_min=gbm_sigma if args.volatility is not None else BASELINE_VARIANCE.gbm_sigma_min,
        gbm_sigma_max=gbm_sigma if args.volatility is not None else BASELINE_VARIANCE.gbm_sigma_max,
        vary_gbm_sigma=False if args.volatility is not None else BASELINE_VARIANCE.vary_gbm_sigma,
    )

    runner = MatchRunner(
        n_simulations=n_simulations,
        config=config,
        n_workers=resolve_n_workers(),
        variance=variance,
    )
    result = runner.run_match(user_strategy, default_strategy)

    # Display score (only the user's strategy Edge)
    avg_edge = result.total_edge_a / n_simulations
    print(f"\n{strategy_name} Edge: {avg_edge:.2f}")

    return 0


def validate_command(args: argparse.Namespace) -> int:
    """Validate a Solidity strategy file without running it."""
    strategy_path = Path(args.strategy)
    if not strategy_path.exists():
        print(f"Error: Strategy file not found: {strategy_path}")
        return 1

    source_code = strategy_path.read_text()

    # Validate
    print("Validating strategy...")
    validator = SolidityValidator()
    validation = validator.validate(source_code)
    if not validation.valid:
        print("Validation failed:")
        for error in validation.errors:
            print(f"  - {error}")
        return 1

    if validation.warnings:
        print("Warnings:")
        for warning in validation.warnings:
            print(f"  - {warning}")

    # Compile
    print("Compiling strategy...")
    compiler = SolidityCompiler()
    compilation = compiler.compile(source_code)
    if not compilation.success:
        print("Compilation failed:")
        for error in (compilation.errors or []):
            print(f"  - {error}")
        return 1

    # Test deployment
    try:
        from decimal import Decimal
        strategy = EVMStrategyAdapter(
            bytecode=compilation.bytecode,
            abi=compilation.abi,
        )
        strategy.after_initialize(Decimal("100"), Decimal("10000"))
        print(f"Strategy '{strategy.get_name()}' validated successfully!")
        return 0
    except Exception as e:
        print(f"EVM execution failed: {e}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AMM Design Competition - Simulate and score your strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  amm-match run my_strategy.sol
  amm-match run my_strategy.sol --simulations 99 --steps 1000
  amm-match validate my_strategy.sol
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run simulations and get your strategy's Edge score")
    run_parser.add_argument("strategy", help="Path to Solidity strategy file (.sol)")
    run_parser.add_argument(
        "--simulations",
        type=int,
        default=None,
        help="Number of simulations per match (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Steps per simulation (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--initial-price",
        type=float,
        default=None,
        help="Initial price (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--initial-x",
        type=float,
        default=None,
        help="Initial X reserves (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--initial-y",
        type=float,
        default=None,
        help="Initial Y reserves (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--volatility",
        type=float,
        default=None,
        help="Annualized volatility (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--retail-rate",
        type=float,
        default=None,
        help="Retail arrival rate per step (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--retail-size",
        type=float,
        default=None,
        help="Mean retail trade size in Y (defaults to shared baseline config)",
    )
    run_parser.add_argument(
        "--retail-size-sigma",
        type=float,
        default=None,
        help="Lognormal sigma for retail sizes (defaults to shared baseline config)",
    )
    run_parser.set_defaults(func=run_match_command)

    # Validate command
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a Solidity strategy without running"
    )
    validate_parser.add_argument("strategy", help="Path to Solidity strategy file (.sol)")
    validate_parser.set_defaults(func=validate_command)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
