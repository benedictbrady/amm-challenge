//! TradeInfo struct and ABI encoding for EVM calls.

use crate::types::wad::{Wad, MAX_FEE};

/// Information about an executed trade, passed to EVM strategies.
#[derive(Debug, Clone, Copy)]
pub struct TradeInfo {
    /// true if AMM bought X (trader sold X)
    pub is_buy: bool,
    /// Amount of X traded (WAD precision)
    pub amount_x: Wad,
    /// Amount of Y traded (WAD precision)
    pub amount_y: Wad,
    /// Simulation step number
    pub timestamp: u64,
    /// Post-trade X reserves (WAD precision)
    pub reserve_x: Wad,
    /// Post-trade Y reserves (WAD precision)
    pub reserve_y: Wad,
}

impl TradeInfo {
    /// Create a new TradeInfo.
    pub fn new(
        is_buy: bool,
        amount_x: Wad,
        amount_y: Wad,
        timestamp: u64,
        reserve_x: Wad,
        reserve_y: Wad,
    ) -> Self {
        Self {
            is_buy,
            amount_x,
            amount_y,
            timestamp,
            reserve_x,
            reserve_y,
        }
    }

    /// Encode as ABI calldata for afterSwap function.
    ///
    /// Layout (196 bytes total):
    /// - bytes 0-3: function selector (0xc2babb57)
    /// - bytes 4-35: isBuy (bool as uint256)
    /// - bytes 36-67: amountX (uint256)
    /// - bytes 68-99: amountY (uint256)
    /// - bytes 100-131: timestamp (uint256)
    /// - bytes 132-163: reserveX (uint256)
    /// - bytes 164-195: reserveY (uint256)
    #[inline]
    pub fn encode_calldata(&self, buffer: &mut [u8; 196]) {
        // Function selector for afterSwap(TradeInfo)
        buffer[0..4].copy_from_slice(&[0xc2, 0xba, 0xbb, 0x57]);

        // isBuy (bool as uint256, value at byte 35)
        buffer[4..36].fill(0);
        if self.is_buy {
            buffer[35] = 1;
        }

        // amountX
        Self::encode_u256(&mut buffer[36..68], self.amount_x.raw() as u128);

        // amountY
        Self::encode_u256(&mut buffer[68..100], self.amount_y.raw() as u128);

        // timestamp
        Self::encode_u256(&mut buffer[100..132], self.timestamp as u128);

        // reserveX
        Self::encode_u256(&mut buffer[132..164], self.reserve_x.raw() as u128);

        // reserveY
        Self::encode_u256(&mut buffer[164..196], self.reserve_y.raw() as u128);
    }

    /// Encode a u128 as big-endian 32 bytes.
    #[inline]
    fn encode_u256(buffer: &mut [u8], value: u128) {
        buffer.fill(0);
        let bytes = value.to_be_bytes();
        buffer[16..32].copy_from_slice(&bytes);
    }
}

/// Function selector for afterInitialize(uint256,uint256)
pub const SELECTOR_AFTER_INITIALIZE: [u8; 4] = [0x83, 0x7a, 0xef, 0x47];

/// Function selector for afterSwap(TradeInfo)
pub const SELECTOR_AFTER_SWAP: [u8; 4] = [0xc2, 0xba, 0xbb, 0x57];

/// Function selector for getName()
pub const SELECTOR_GET_NAME: [u8; 4] = [0x17, 0xd7, 0xde, 0x7c];

/// Encode afterInitialize(uint256, uint256) calldata.
#[inline]
pub fn encode_after_initialize(initial_x: Wad, initial_y: Wad) -> [u8; 68] {
    let mut buffer = [0u8; 68];
    buffer[0..4].copy_from_slice(&SELECTOR_AFTER_INITIALIZE);

    // initialX
    let x_bytes = (initial_x.raw() as u128).to_be_bytes();
    buffer[20..36].copy_from_slice(&x_bytes);

    // initialY
    let y_bytes = (initial_y.raw() as u128).to_be_bytes();
    buffer[52..68].copy_from_slice(&y_bytes);

    buffer
}

/// Decode (uint256, uint256) return value as (bid_fee, ask_fee) in WAD.
#[inline]
pub fn decode_fee_pair(data: &[u8]) -> Option<(Wad, Wad)> {
    if data.len() < 64 {
        return None;
    }

    let bid_fee = decode_u256(&data[0..32])?;
    let ask_fee = decode_u256(&data[32..64])?;

    let max_fee_u128 = MAX_FEE as u128;
    if bid_fee > max_fee_u128 || ask_fee > max_fee_u128 {
        return None;
    }

    let bid_i128 = i128::try_from(bid_fee).ok()?;
    let ask_i128 = i128::try_from(ask_fee).ok()?;
    Some((Wad::new(bid_i128), Wad::new(ask_i128)))
}

/// Decode big-endian 32 bytes as u128 (upper 16 bytes must be zero).
#[inline]
fn decode_u256(data: &[u8]) -> Option<u128> {
    if data.len() != 32 {
        return None;
    }
    // Check upper 16 bytes are zero
    if data[0..16].iter().any(|&b| b != 0) {
        return None;
    }
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&data[16..32]);
    Some(u128::from_be_bytes(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::wad::{WAD, MAX_FEE};

    #[test]
    fn test_encode_trade_info() {
        let trade = TradeInfo {
            is_buy: true,
            amount_x: Wad::new(WAD), // 1.0
            amount_y: Wad::new(WAD * 2), // 2.0
            timestamp: 100,
            reserve_x: Wad::new(WAD * 1000),
            reserve_y: Wad::new(WAD * 1000),
        };

        let mut buffer = [0u8; 196];
        trade.encode_calldata(&mut buffer);

        // Check selector
        assert_eq!(&buffer[0..4], &[0xc2, 0xba, 0xbb, 0x57]);

        // Check is_buy
        assert_eq!(buffer[35], 1);

        // Decode and verify
        let decoded_x = decode_u256(&buffer[36..68]).unwrap();
        assert_eq!(decoded_x as i128, WAD);
    }

    #[test]
    fn test_encode_after_initialize() {
        let calldata = encode_after_initialize(
            Wad::new(WAD * 1000),
            Wad::new(WAD * 1000),
        );

        assert_eq!(&calldata[0..4], &SELECTOR_AFTER_INITIALIZE);
        assert_eq!(calldata.len(), 68);
    }

    #[test]
    fn test_decode_fee_pair_rejects_out_of_range_fee() {
        let mut data = [0u8; 64];

        // Set bid_fee = MAX_FEE + 1 in low 16 bytes.
        let bad = (MAX_FEE as u128) + 1;
        data[16..32].copy_from_slice(&bad.to_be_bytes());

        // Set ask_fee = 30 bps.
        let ok = (30u128) * 100_000_000_000_000u128;
        data[48..64].copy_from_slice(&ok.to_be_bytes());

        assert!(decode_fee_pair(&data).is_none());
    }
}
