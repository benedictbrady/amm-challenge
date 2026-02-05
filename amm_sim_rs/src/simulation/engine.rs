//! Main simulation engine.

use std::collections::HashMap;

use crate::amm::CFMM;
use crate::evm::EVMStrategy;
use crate::market::{Arbitrageur, GBMPriceProcess, OrderRouter, RetailTrader};
use crate::types::config::SimulationConfig;
use crate::types::result::{LightweightSimResult, LightweightStepResult};

/// Error type for simulation.
#[derive(Debug)]
pub enum SimulationError {
    EVMError(String),
    InvalidConfig(String),
}

impl std::fmt::Display for SimulationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SimulationError::EVMError(s) => write!(f, "EVM error: {}", s),
            SimulationError::InvalidConfig(s) => write!(f, "Invalid config: {}", s),
        }
    }
}

impl std::error::Error for SimulationError {}

/// Main simulation engine for AMM competition.
///
/// Runs a simulation with the following loop per step:
/// 1. Generate new fair price via GBM
/// 2. Arbitrageur extracts profit from each AMM
/// 3. Retail orders arrive and are routed to best AMM
pub struct SimulationEngine {
    config: SimulationConfig,
}

impl SimulationEngine {
    /// Create a new simulation engine.
    pub fn new(config: SimulationConfig) -> Self {
        Self { config }
    }

    /// Run a complete simulation.
    pub fn run(
        &mut self,
        submission: EVMStrategy,
        baseline: EVMStrategy,
    ) -> Result<LightweightSimResult, SimulationError> {
        let seed = self.config.seed.unwrap_or(0);

        // Initialize price process
        let mut price_process = GBMPriceProcess::new(
            self.config.initial_price,
            self.config.gbm_mu,
            self.config.gbm_sigma,
            self.config.gbm_dt,
            Some(seed),
        );

        // Initialize retail trader with different seed
        let mut retail_trader = RetailTrader::new(
            self.config.retail_arrival_rate,
            self.config.retail_mean_size,
            self.config.retail_size_sigma,
            self.config.retail_buy_prob,
            Some(seed + 1),
        );

        let arbitrageur = Arbitrageur::new();
        let router = OrderRouter::new();

        // Create AMMs with fixed positional names to avoid HashMap collision
        // when both contracts return the same getName()
        let submission_name = "submission".to_string();
        let baseline_name = "normalizer".to_string();

        let mut amm_submission = CFMM::new(
            submission,
            self.config.initial_x,
            self.config.initial_y,
        );
        amm_submission.name = submission_name.clone();

        let mut amm_baseline = CFMM::new(
            baseline,
            self.config.initial_x,
            self.config.initial_y,
        );
        amm_baseline.name = baseline_name.clone();

        // Initialize AMMs
        amm_submission.initialize()
            .map_err(|e| SimulationError::EVMError(e.to_string()))?;
        amm_baseline.initialize()
            .map_err(|e| SimulationError::EVMError(e.to_string()))?;

        // Record initial state
        let initial_fair_price = price_process.current_price();
        let mut initial_reserves = HashMap::new();
        initial_reserves.insert(
            submission_name.clone(),
            (amm_submission.reserves().0, amm_submission.reserves().1),
        );
        initial_reserves.insert(
            baseline_name.clone(),
            (amm_baseline.reserves().0, amm_baseline.reserves().1),
        );

        // Track edge per strategy
        let mut edges: HashMap<String, f64> = HashMap::new();
        edges.insert(submission_name.clone(), 0.0);
        edges.insert(baseline_name.clone(), 0.0);

        // Run simulation steps
        let mut steps = Vec::with_capacity(self.config.n_steps as usize);

        // Store AMMs in a Vec for easier mutable access
        let mut amms = vec![amm_submission, amm_baseline];
        let names = vec![submission_name.clone(), baseline_name.clone()];

        // Track cumulative volumes
        let mut arb_volume_y: HashMap<String, f64> = HashMap::new();
        let mut retail_volume_y: HashMap<String, f64> = HashMap::new();
        // Track cumulative fees for averaging
        let mut cumulative_bid_fees: HashMap<String, f64> = HashMap::new();
        let mut cumulative_ask_fees: HashMap<String, f64> = HashMap::new();
        for name in &names {
            arb_volume_y.insert(name.clone(), 0.0);
            retail_volume_y.insert(name.clone(), 0.0);
            cumulative_bid_fees.insert(name.clone(), 0.0);
            cumulative_ask_fees.insert(name.clone(), 0.0);
        }

        for t in 0..self.config.n_steps {
            // 1. Generate new fair price
            let fair_price = price_process.step();

            // 2. Arbitrageur extracts profit from each AMM
            for amm in amms.iter_mut() {
                if let Some(arb_result) = arbitrageur.execute_arb(amm, fair_price, t as u64) {
                    *arb_volume_y.get_mut(&arb_result.amm_name).unwrap() += arb_result.amount_y;
                    let entry = edges.entry(arb_result.amm_name).or_insert(0.0);
                    // AMM edge is the negative of arbitrageur profit at true price
                    *entry += -arb_result.profit;
                }
            }

            // 3. Retail orders arrive and get routed
            let orders = retail_trader.generate_orders();
            let routed_trades = router.route_orders(&orders, &mut amms, fair_price, t as u64);
            for trade in routed_trades {
                *retail_volume_y.get_mut(&trade.amm_name).unwrap() += trade.amount_y;
                let trade_edge = if trade.amm_buys_x {
                    trade.amount_x * fair_price - trade.amount_y
                } else {
                    trade.amount_y - trade.amount_x * fair_price
                };
                let entry = edges.entry(trade.amm_name).or_insert(0.0);
                *entry += trade_edge;
            }

            // 4. Capture step result and accumulate fees
            let step = capture_step(
                t,
                fair_price,
                &amms,
                &names,
                &initial_reserves,
                initial_fair_price,
            );
            // Accumulate fees for averaging
            for name in &names {
                if let Some((bid_fee, ask_fee)) = step.fees.get(name) {
                    *cumulative_bid_fees.get_mut(name).unwrap() += bid_fee;
                    *cumulative_ask_fees.get_mut(name).unwrap() += ask_fee;
                }
            }
            steps.push(step);
        }

        // Calculate final PnL (reserves + accumulated fees)
        let final_fair_price = price_process.current_price();
        let mut pnl = HashMap::new();

        // Calculate average fees
        let n_steps = self.config.n_steps as f64;
        let mut average_fees: HashMap<String, (f64, f64)> = HashMap::new();
        for name in &names {
            let avg_bid = cumulative_bid_fees.get(name).unwrap() / n_steps;
            let avg_ask = cumulative_ask_fees.get(name).unwrap() / n_steps;
            average_fees.insert(name.clone(), (avg_bid, avg_ask));
        }

        for (amm, name) in amms.iter().zip(names.iter()) {
            let (init_x, init_y) = initial_reserves.get(name).unwrap();
            let init_value = init_x * initial_fair_price + init_y;
            let (final_x, final_y) = amm.reserves();
            let (fees_x, fees_y) = amm.accumulated_fees();
            let reserves_value = final_x * final_fair_price + final_y;
            let fees_value = fees_x * final_fair_price + fees_y;
            let final_value = reserves_value + fees_value;
            pnl.insert(name.clone(), final_value - init_value);
        }

        Ok(LightweightSimResult {
            seed,
            strategies: vec![submission_name, baseline_name],
            pnl,
            edges,
            initial_fair_price,
            initial_reserves,
            steps,
            arb_volume_y,
            retail_volume_y,
            average_fees,
        })
    }
}

fn capture_step(
    timestamp: u32,
    fair_price: f64,
    amms: &[CFMM],
    names: &[String],
    initial_reserves: &HashMap<String, (f64, f64)>,
    initial_fair_price: f64,
) -> LightweightStepResult {
    let mut spot_prices = HashMap::new();
    let mut pnls = HashMap::new();
    let mut fees = HashMap::new();

    for (amm, name) in amms.iter().zip(names.iter()) {
        spot_prices.insert(name.clone(), amm.spot_price());

        let fee_quote = amm.fees();
        fees.insert(
            name.clone(),
            (fee_quote.bid_fee.to_f64(), fee_quote.ask_fee.to_f64()),
        );

        // Calculate running PnL (reserves + accumulated fees)
        let (init_x, init_y) = initial_reserves.get(name).unwrap();
        let init_value = init_x * initial_fair_price + init_y;
        let (curr_x, curr_y) = amm.reserves();
        let (fees_x, fees_y) = amm.accumulated_fees();
        let reserves_value = curr_x * fair_price + curr_y;
        let fees_value = fees_x * fair_price + fees_y;
        let curr_value = reserves_value + fees_value;
        pnls.insert(name.clone(), curr_value - init_value);
    }

    LightweightStepResult {
        timestamp,
        fair_price,
        spot_prices,
        pnls,
        fees,
    }
}

#[cfg(test)]
mod tests {
    // Full tests require EVM bytecode - see integration tests
}
