//! EVM strategy wrapper using revm.

use revm::{
    primitives::{
        Address, Bytes, ExecutionResult, Output, U256,
        AccountInfo, Bytecode, TxKind,
    },
    Evm, InMemoryDB,
};
use thiserror::Error;

use crate::types::trade_info::{encode_after_initialize, decode_fee_pair, TradeInfo, SELECTOR_GET_NAME};
use crate::types::wad::Wad;

/// Errors that can occur during EVM execution.
#[derive(Error, Debug)]
pub enum EVMError {
    #[error("Deployment failed: {0}")]
    DeploymentFailed(String),

    #[error("Execution failed: {0}")]
    ExecutionFailed(String),

    #[error("Invalid return data: {0}")]
    InvalidReturnData(String),

    #[error("Out of gas")]
    OutOfGas,
}

/// Gas limits for strategy execution.
const GAS_LIMIT_INIT: u64 = 250_000;
const GAS_LIMIT_TRADE: u64 = 250_000;
const GAS_LIMIT_NAME: u64 = 50_000;

/// Fixed addresses for simulation.
const STRATEGY_ADDRESS: Address = Address::new([
    0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
]);

const CALLER_ADDRESS: Address = Address::new([
    0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02,
]);

/// EVM strategy executor.
///
/// Wraps a Solidity AMM strategy and executes it using revm.
pub struct EVMStrategy {
    /// Strategy name (cached after first call)
    name: String,
    /// Compiled bytecode (for reset)
    bytecode: Vec<u8>,
    /// In-memory database for EVM state
    db: InMemoryDB,
    /// Pre-allocated calldata buffer for after_swap (196 bytes)
    trade_calldata: [u8; 196],
}

impl EVMStrategy {
    /// Create a new EVM strategy from compiled bytecode.
    pub fn new(bytecode: Vec<u8>, default_name: String) -> Result<Self, EVMError> {
        let mut strategy = Self {
            name: default_name,
            bytecode: bytecode.clone(),
            db: InMemoryDB::default(),
            trade_calldata: [0u8; 196],
        };

        strategy.deploy()?;
        strategy.fetch_name()?;

        Ok(strategy)
    }

    /// Deploy the contract to the EVM.
    fn deploy(&mut self) -> Result<(), EVMError> {
        // Reset database
        self.db = InMemoryDB::default();

        // Give caller some balance
        let caller_info = AccountInfo {
            balance: U256::from(1_000_000_000_000_000_000_000u128),
            nonce: 0,
            code_hash: Default::default(),
            code: None,
        };
        self.db.insert_account_info(CALLER_ADDRESS, caller_info);

        // First, run the deployment transaction
        let deployed_code = {
            let mut evm = Evm::builder()
                .with_db(&mut self.db)
                .modify_tx_env(|tx| {
                    tx.caller = CALLER_ADDRESS;
                    tx.transact_to = TxKind::Create;
                    tx.data = Bytes::copy_from_slice(&self.bytecode);
                    tx.value = U256::ZERO;
                    tx.gas_limit = 10_000_000;
                })
                .build();

            let result = evm.transact_commit()
                .map_err(|e| EVMError::DeploymentFailed(format!("{:?}", e)))?;

            match result {
                ExecutionResult::Success { output, .. } => {
                    match output {
                        Output::Create(code, _) => Ok(code),
                        Output::Call(_) => {
                            Err(EVMError::DeploymentFailed("Expected Create output".into()))
                        }
                    }
                }
                ExecutionResult::Revert { output, .. } => {
                    Err(EVMError::DeploymentFailed(format!("Reverted: {:?}", output)))
                }
                ExecutionResult::Halt { reason, .. } => {
                    Err(EVMError::DeploymentFailed(format!("Halted: {:?}", reason)))
                }
            }
        }?;

        // Now insert the code at our fixed address
        let bytecode = Bytecode::new_raw(deployed_code);
        let account_info = AccountInfo {
            balance: U256::ZERO,
            nonce: 1,
            code_hash: bytecode.hash_slow(),
            code: Some(bytecode),
        };
        self.db.insert_account_info(STRATEGY_ADDRESS, account_info);

        Ok(())
    }

    /// Fetch the strategy name from the contract.
    fn fetch_name(&mut self) -> Result<(), EVMError> {
        let result = self.call(&SELECTOR_GET_NAME, GAS_LIMIT_NAME)?;

        // Decode string return value
        // String is encoded as: offset (32 bytes) + length (32 bytes) + data
        if result.len() >= 64 {
            let offset = u256_to_usize(&result[0..32]).unwrap_or(32);
            if offset + 32 <= result.len() {
                let length = u256_to_usize(&result[offset..offset + 32]).unwrap_or(0);
                if offset + 32 + length <= result.len() {
                    if let Ok(name) = String::from_utf8(result[offset + 32..offset + 32 + length].to_vec()) {
                        self.name = name;
                    }
                }
            }
        }

        Ok(())
    }

    /// Get the strategy name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Initialize the strategy with starting reserves.
    ///
    /// Returns (bid_fee, ask_fee) in WAD.
    pub fn after_initialize(&mut self, initial_x: Wad, initial_y: Wad) -> Result<(Wad, Wad), EVMError> {
        let calldata = encode_after_initialize(initial_x, initial_y);
        let result = self.call(&calldata, GAS_LIMIT_INIT)?;

        decode_fee_pair(&result)
            .ok_or_else(|| EVMError::InvalidReturnData("Failed to decode fee pair".into()))
    }

    /// Handle a trade event and return updated fees.
    ///
    /// Returns (bid_fee, ask_fee) in WAD.
    #[inline]
    pub fn after_swap(&mut self, trade: &TradeInfo) -> Result<(Wad, Wad), EVMError> {
        // Encode trade info into pre-allocated buffer
        trade.encode_calldata(&mut self.trade_calldata);

        // Copy calldata to avoid borrow conflict
        let calldata = self.trade_calldata;
        let result = self.call(&calldata, GAS_LIMIT_TRADE)?;

        decode_fee_pair(&result)
            .ok_or_else(|| EVMError::InvalidReturnData("Failed to decode fee pair".into()))
    }

    /// Reset the strategy for a new simulation.
    pub fn reset(&mut self) -> Result<(), EVMError> {
        self.deploy()
    }

    /// Make a call to the contract.
    fn call(&mut self, calldata: &[u8], gas_limit: u64) -> Result<Vec<u8>, EVMError> {
        let mut evm = Evm::builder()
            .with_db(&mut self.db)
            .modify_tx_env(|tx| {
                tx.caller = CALLER_ADDRESS;
                tx.transact_to = TxKind::Call(STRATEGY_ADDRESS);
                tx.data = Bytes::copy_from_slice(calldata);
                tx.value = U256::ZERO;
                tx.gas_limit = gas_limit;
            })
            .build();

        let result = evm.transact_commit()
            .map_err(|e| EVMError::ExecutionFailed(format!("{:?}", e)))?;

        match result {
            ExecutionResult::Success { output, .. } => {
                match output {
                    Output::Call(data) => Ok(data.to_vec()),
                    Output::Create(_, _) => {
                        Err(EVMError::ExecutionFailed("Unexpected Create output".into()))
                    }
                }
            }
            ExecutionResult::Revert { output, .. } => {
                Err(EVMError::ExecutionFailed(format!("Reverted: {:?}", output)))
            }
            ExecutionResult::Halt { reason, .. } => {
                if matches!(reason, revm::primitives::HaltReason::OutOfGas(_)) {
                    Err(EVMError::OutOfGas)
                } else {
                    Err(EVMError::ExecutionFailed(format!("Halted: {:?}", reason)))
                }
            }
        }
    }
}

/// Convert 32-byte big-endian slice to usize.
fn u256_to_usize(data: &[u8]) -> Option<usize> {
    if data.len() != 32 {
        return None;
    }
    // Check upper bytes are zero
    if data[0..24].iter().any(|&b| b != 0) {
        return None;
    }
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&data[24..32]);
    Some(u64::from_be_bytes(bytes) as usize)
}

impl Clone for EVMStrategy {
    fn clone(&self) -> Self {
        // Create a fresh strategy from bytecode
        Self::new(self.bytecode.clone(), self.name.clone())
            .expect("Failed to clone EVMStrategy")
    }
}

#[cfg(test)]
mod tests {
    // Note: Full tests require EVM bytecode, which is complex to embed.
    // The Python integration tests will verify correctness.
}
