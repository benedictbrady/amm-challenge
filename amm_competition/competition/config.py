"""Shared configuration for baseline simulations and variance."""

from dataclasses import dataclass
import multiprocessing
import os

import amm_sim_rs

from amm_competition.competition.match import HyperparameterVariance


@dataclass(frozen=True)
class BaselineSimulationSettings:
    n_simulations: int
    n_steps: int
    initial_price: float
    initial_x: float
    initial_y: float
    gbm_mu: float
    gbm_dt: float
    retail_buy_prob: float
    retail_size_sigma: float


BASELINE_SETTINGS = BaselineSimulationSettings(
    n_simulations=99,
    n_steps=10000,
    initial_price=100.0,
    initial_x=100.0,
    initial_y=10000.0,
    gbm_mu=0.0,
    gbm_dt=1 / 252,
    retail_buy_prob=0.5,
    retail_size_sigma=1.2,
)


BASELINE_VARIANCE = HyperparameterVariance(
    retail_mean_size_min=19.0,
    retail_mean_size_max=21.0,
    vary_retail_mean_size=True,
    retail_arrival_rate_min=0.6,
    retail_arrival_rate_max=1.0,
    vary_retail_arrival_rate=True,
    gbm_sigma_min=0.0140,
    gbm_sigma_max=0.0160,
    vary_gbm_sigma=True,
)

def _midpoint(min_val: float, max_val: float) -> float:
    return (min_val + max_val) / 2


def baseline_nominal_sigma() -> float:
    return _midpoint(BASELINE_VARIANCE.gbm_sigma_min, BASELINE_VARIANCE.gbm_sigma_max)


def baseline_nominal_retail_rate() -> float:
    return _midpoint(
        BASELINE_VARIANCE.retail_arrival_rate_min,
        BASELINE_VARIANCE.retail_arrival_rate_max,
    )


def baseline_nominal_retail_size() -> float:
    return _midpoint(
        BASELINE_VARIANCE.retail_mean_size_min,
        BASELINE_VARIANCE.retail_mean_size_max,
    )


def resolve_n_workers() -> int:
    """Resolve worker count from environment or CPU count."""
    return int(os.environ.get("N_WORKERS", str(min(8, multiprocessing.cpu_count()))))


def build_base_config(*, seed: int | None) -> amm_sim_rs.SimulationConfig:
    """Build the canonical base SimulationConfig with explicit values."""
    return amm_sim_rs.SimulationConfig(
        n_steps=BASELINE_SETTINGS.n_steps,
        initial_price=BASELINE_SETTINGS.initial_price,
        initial_x=BASELINE_SETTINGS.initial_x,
        initial_y=BASELINE_SETTINGS.initial_y,
        gbm_mu=BASELINE_SETTINGS.gbm_mu,
        gbm_sigma=baseline_nominal_sigma(),
        gbm_dt=BASELINE_SETTINGS.gbm_dt,
        retail_arrival_rate=baseline_nominal_retail_rate(),
        retail_mean_size=baseline_nominal_retail_size(),
        retail_size_sigma=BASELINE_SETTINGS.retail_size_sigma,
        retail_buy_prob=BASELINE_SETTINGS.retail_buy_prob,
        seed=seed,
    )


def build_config(
    *,
    seed: int | None,
    gbm_sigma: float,
    retail_arrival_rate: float,
    retail_mean_size: float,
    retail_size_sigma: float | None = None,
) -> amm_sim_rs.SimulationConfig:
    """Build a SimulationConfig with explicit fields and variable parameters."""
    return amm_sim_rs.SimulationConfig(
        n_steps=BASELINE_SETTINGS.n_steps,
        initial_price=BASELINE_SETTINGS.initial_price,
        initial_x=BASELINE_SETTINGS.initial_x,
        initial_y=BASELINE_SETTINGS.initial_y,
        gbm_mu=BASELINE_SETTINGS.gbm_mu,
        gbm_sigma=gbm_sigma,
        gbm_dt=BASELINE_SETTINGS.gbm_dt,
        retail_arrival_rate=retail_arrival_rate,
        retail_mean_size=retail_mean_size,
        retail_size_sigma=(
            BASELINE_SETTINGS.retail_size_sigma
            if retail_size_sigma is None
            else retail_size_sigma
        ),
        retail_buy_prob=BASELINE_SETTINGS.retail_buy_prob,
        seed=seed,
    )
