"""Match runner for baseline vs submission simulations using Rust engine."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import amm_sim_rs

from amm_competition.evm.adapter import EVMStrategyAdapter


@dataclass
class HyperparameterVariance:
    """Configuration for hyperparameter variance across simulations."""
    retail_mean_size_min: float
    retail_mean_size_max: float
    vary_retail_mean_size: bool

    retail_arrival_rate_min: float
    retail_arrival_rate_max: float
    vary_retail_arrival_rate: bool

    gbm_sigma_min: float
    gbm_sigma_max: float
    vary_gbm_sigma: bool


@dataclass
class LightweightStepResult:
    """Minimal step data for charting."""
    timestamp: int
    fair_price: float
    spot_prices: dict[str, float]
    pnls: dict[str, float]
    fees: dict[str, tuple[float, float]]


@dataclass
class LightweightSimResult:
    """Minimal simulation result for charting."""
    seed: int
    strategies: list[str]
    pnl: dict[str, Decimal]
    edges: dict[str, Decimal]
    initial_fair_price: float
    initial_reserves: dict[str, tuple[float, float]]
    steps: list[LightweightStepResult]
    arb_volume_y: dict[str, float]
    retail_volume_y: dict[str, float]
    average_fees: dict[str, tuple[float, float]]


@dataclass
class MatchResult:
    """Result of a head-to-head match."""
    strategy_a: str
    strategy_b: str
    wins_a: int
    wins_b: int
    draws: int
    total_pnl_a: Decimal
    total_pnl_b: Decimal
    total_edge_a: Decimal
    total_edge_b: Decimal
    simulation_results: list[LightweightSimResult] = field(default_factory=list)

    @property
    def winner(self) -> Optional[str]:
        if self.wins_a > self.wins_b:
            return self.strategy_a
        elif self.wins_b > self.wins_a:
            return self.strategy_b
        return None

    @property
    def total_games(self) -> int:
        return self.wins_a + self.wins_b + self.draws


# Re-export SimulationConfig from Rust for compatibility
SimulationConfig = amm_sim_rs.SimulationConfig


class MatchRunner:
    """Runs matches using Rust simulation engine."""

    def __init__(
        self,
        *,
        n_simulations: int,
        config: SimulationConfig,
        n_workers: int,
        variance: HyperparameterVariance,
    ):
        self.n_simulations = n_simulations
        self.base_config = config
        self.n_workers = n_workers
        self.variance = variance

    def _build_configs(self) -> list[amm_sim_rs.SimulationConfig]:
        """Build simulation configs with optional variance."""
        import numpy as np

        configs = []
        for i in range(self.n_simulations):
            rng = np.random.default_rng(seed=i)

            retail_mean_size = (
                rng.uniform(self.variance.retail_mean_size_min, self.variance.retail_mean_size_max)
                if self.variance.vary_retail_mean_size
                else self.base_config.retail_mean_size
            )
            retail_arrival_rate = (
                rng.uniform(self.variance.retail_arrival_rate_min, self.variance.retail_arrival_rate_max)
                if self.variance.vary_retail_arrival_rate
                else self.base_config.retail_arrival_rate
            )
            gbm_sigma = (
                rng.uniform(self.variance.gbm_sigma_min, self.variance.gbm_sigma_max)
                if self.variance.vary_gbm_sigma
                else self.base_config.gbm_sigma
            )

            cfg = amm_sim_rs.SimulationConfig(
                n_steps=self.base_config.n_steps,
                initial_price=self.base_config.initial_price,
                initial_x=self.base_config.initial_x,
                initial_y=self.base_config.initial_y,
                gbm_mu=self.base_config.gbm_mu,
                gbm_sigma=gbm_sigma,
                gbm_dt=self.base_config.gbm_dt,
                retail_arrival_rate=retail_arrival_rate,
                retail_mean_size=retail_mean_size,
                retail_size_sigma=self.base_config.retail_size_sigma,
                retail_buy_prob=self.base_config.retail_buy_prob,
                seed=i,
            )
            configs.append(cfg)
        return configs

    def run_match(
        self,
        strategy_a: EVMStrategyAdapter,
        strategy_b: EVMStrategyAdapter,
        store_results: bool = False,
    ) -> MatchResult:
        """Run a complete match between two strategies."""
        name_a = strategy_a.get_name()
        name_b = strategy_b.get_name()

        # Build configs
        configs = self._build_configs()

        # Run simulations in Rust
        batch_result = amm_sim_rs.run_batch(
            list(strategy_a._bytecode),
            list(strategy_b._bytecode),
            configs,
            self.n_workers,
        )

        # Process results
        wins_a = 0
        wins_b = 0
        draws = 0
        total_pnl_a = Decimal("0")
        total_pnl_b = Decimal("0")
        total_edge_a = Decimal("0")
        total_edge_b = Decimal("0")
        simulation_results = []

        for rust_result in batch_result.results:
            # Get PnL values using fixed positional keys from Rust
            pnl_a = rust_result.pnl.get("submission", 0.0)
            pnl_b = rust_result.pnl.get("normalizer", 0.0)
            edge_a = rust_result.edges.get("submission", 0.0)
            edge_b = rust_result.edges.get("normalizer", 0.0)

            total_pnl_a += Decimal(str(pnl_a))
            total_pnl_b += Decimal(str(pnl_b))
            total_edge_a += Decimal(str(edge_a))
            total_edge_b += Decimal(str(edge_b))

            if edge_a > edge_b:
                wins_a += 1
            elif edge_b > edge_a:
                wins_b += 1
            else:
                draws += 1

            if store_results:
                # Convert Rust result to Python dataclass
                steps = [
                    LightweightStepResult(
                        timestamp=s.timestamp,
                        fair_price=s.fair_price,
                        spot_prices=s.spot_prices,
                        pnls=s.pnls,
                        fees=s.fees,
                    )
                    for s in rust_result.steps
                ]

                sim_result = LightweightSimResult(
                    seed=rust_result.seed,
                    strategies=rust_result.strategies,
                    pnl={k: Decimal(str(v)) for k, v in rust_result.pnl.items()},
                    edges={
                        k: Decimal(str(v)) for k, v in rust_result.edges.items()
                    },
                    initial_fair_price=rust_result.initial_fair_price,
                    initial_reserves=rust_result.initial_reserves,
                    steps=steps,
                    arb_volume_y=rust_result.arb_volume_y,
                    retail_volume_y=rust_result.retail_volume_y,
                    average_fees=rust_result.average_fees,
                )
                simulation_results.append(sim_result)

        return MatchResult(
            strategy_a=name_a,
            strategy_b=name_b,
            wins_a=wins_a,
            wins_b=wins_b,
            draws=draws,
            total_pnl_a=total_pnl_a,
            total_pnl_b=total_pnl_b,
            total_edge_a=total_edge_a,
            total_edge_b=total_edge_b,
            simulation_results=simulation_results,
        )
