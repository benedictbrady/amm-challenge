# AMM Fee Strategy Challenge

Design dynamic fee strategies for a constant-product AMM. Your goal: maximize **edge**.

## Submission

Upload a `.sol` file containing a contract named `Strategy` that inherits from `AMMStrategyBase`.

Local results may diverge slightly from submission scores due to different RNG seeds. Run more simulations locally (`--simulations 99`) to reduce variance and get closer to expected server results.

## The Simulation

Each simulation runs 10,000 steps. At each step:

1. **Price moves** — A fair price `p` evolves via geometric Brownian motion
2. **Arbitrageurs trade** — They push each AMM's spot price toward `p`, extracting profit
3. **Retail orders arrive** — Random buy/sell orders get routed optimally across AMMs

Your strategy competes against a **normalizer AMM** running fixed 25 bps fees. Both AMMs start with identical reserves (100 X, 10,000 Y at price 100).

### Price Process

The fair price follows GBM: `dS = μSdt + σSdW`

- Drift `μ = 0` (no directional bias)
- Volatility `σ ~ U[0.95%, 1.15%]` per step (varies across simulations)
- Time step `dt = 1/252`

### Retail Flow

Uninformed traders arrive via Poisson process:

- Arrival rate `λ ~ U[0.6, 1.0]` orders per step
- Order size `~ LogNormal(μ, σ=1.2)` with mean `~ U[18, 20]` in Y terms
- Direction: 50% buy, 50% sell

Retail flow splits optimally between AMMs based on fees—lower fees attract more volume.

## The Math

### Constant Product AMM

Reserves `(x, y)` satisfy `x * y = k`. The spot price is `y/x`. When the AMM sells Δx tokens:

```
Δy = y - k/(x - Δx)    (what trader pays)
```

Fees are taken on input: if fee is `f`, only `(1-f)` of the input affects reserves.

### Arbitrage

When spot price diverges from fair price `p`, arbitrageurs trade to close the gap. For fee `f`:

- **Spot < fair** (AMM underprices X): Buy X from AMM. Optimal size: `Δx = x - √(k(1+f)/p)`
- **Spot > fair** (AMM overprices X): Sell X to AMM. Optimal size: `Δx = √(k(1-f)/p) - x`

Higher fees mean arbitrageurs need larger mispricings to profit, so your AMM stays "stale" longer—bad for edge.

### Order Routing

Retail orders split optimally across AMMs to equalize marginal prices post-trade. For two AMMs with fee rates `f₁, f₂`, let `γᵢ = 1 - fᵢ` and `Aᵢ = √(xᵢ γᵢ yᵢ)`. The optimal Y split is:

```
Δy₁ = (r(y₂ + γ₂Y) - y₁) / (γ₁ + rγ₂)    where r = A₁/A₂
```

Lower fees → larger `γ` → more flow. But the relationship is nonlinear—small fee differences can shift large fractions of volume.

### Edge

Edge measures profitability using the fair price at trade time:

```
Edge = Σ (amount_x × fair_price - amount_y)   for sells (AMM sells X)
     + Σ (amount_y - amount_x × fair_price)   for buys  (AMM buys X)
```

- **Retail trades**: Positive edge (you profit from the spread)
- **Arbitrage trades**: Negative edge (you lose to informed flow)

Good strategies maximize retail edge while minimizing arb losses.

## Why the Normalizer?

Without competition, setting 10% fees would appear profitable—you'd capture huge spreads on the few trades that still execute. The normalizer prevents this: if your fees are too high, retail routes to the 25 bps AMM and you get nothing.

The normalizer also means there's no "free lunch"—you can't beat 25 bps just by setting 24 bps. The optimal fee depends on market conditions.

## Writing a Strategy

**Start with `contracts/src/StarterStrategy.sol`** — a simple 50 bps fixed-fee strategy. Copy it, rename `getName()`, and modify the fee logic.

```solidity
contract Strategy is AMMStrategyBase {
    function initialize(uint256 initialX, uint256 initialY)
        external override returns (uint256 bidFee, uint256 askFee);

    function onTrade(TradeInfo calldata trade)
        external override returns (uint256 bidFee, uint256 askFee);

    function getName() external pure override returns (string memory);
}
```

`initialize` is called once at simulation start. `onTrade` is called after every trade on your AMM with:

| Field | Description |
|-------|-------------|
| `isBuy` | `true` if AMM bought X (trader sold X to you) |
| `amountX` | X traded (WAD precision, 1e18 = 1 unit) |
| `amountY` | Y traded |
| `timestamp` | Step number |
| `reserveX`, `reserveY` | Post-trade reserves |

Return fees in WAD: `25 * BPS` = 25 basis points. Max fee is 10%.

You get 32 storage slots (`slots[0..31]`) and helpers like `wmul`, `wdiv`, `sqrt`.

## CLI

```bash
# Build the Rust engine
cd amm_sim_rs && pip install maturin && maturin develop --release && cd ..

# Install
pip install -e .

# Run 99 simulations (default)
amm-match run my_strategy.sol

# Quick test
amm-match run my_strategy.sol --simulations 10

# Validate without running
amm-match validate my_strategy.sol
```

Output is your average edge across simulations. The 25 bps normalizer typically scores around 250-350 edge depending on market conditions.
