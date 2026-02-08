const SPEED_PROFILE = {
  1: { label: "0.4x", ms: 1800 },
  2: { label: "0.7x", ms: 1250 },
  3: { label: "1.0x", ms: 860 },
  4: { label: "1.5x", ms: 580 },
  5: { label: "2.2x", ms: 380 },
  6: { label: "3.2x", ms: 240 },
};

const STRATEGIES = {
  baseline30: {
    name: "Baseline 30 bps",
    code: `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {TradeInfo} from "./IAMMStrategy.sol";

contract Strategy is AMMStrategyBase {
    function afterInitialize(uint256, uint256)
        external pure override returns (uint256 bidFee, uint256 askFee)
    {
        return (bpsToWad(30), bpsToWad(30));
    }

    function afterSwap(TradeInfo calldata)
        external pure override returns (uint256 bidFee, uint256 askFee)
    {
        return (bpsToWad(30), bpsToWad(30));
    }

    function getName() external pure override returns (string memory) {
        return "Baseline-30bps";
    }
}`,
    initialize() {
      return {
        bidBps: 30,
        askBps: 30,
        lines: [11],
        explanation: "`afterInitialize` returns 30 bps on both sides. No persistent state is used.",
        stateBadge: "fixed fee: 30/30 bps",
      };
    },
    onSwap() {
      return {
        bidBps: 30,
        askBps: 30,
        lines: [17],
        explanation: "`afterSwap` ignores trade details and keeps the fee fixed at 30 bps.",
        stateBadge: "fixed fee: 30/30 bps",
      };
    },
  },

  starter50: {
    name: "Starter 50 bps",
    code: `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {IAMMStrategy, TradeInfo} from "./IAMMStrategy.sol";

contract Strategy is AMMStrategyBase {
    uint256 public constant FEE = 50 * BPS;

    function afterInitialize(uint256, uint256)
        external pure override returns (uint256, uint256)
    {
        return (FEE, FEE);
    }

    function afterSwap(TradeInfo calldata)
        external pure override returns (uint256, uint256)
    {
        return (FEE, FEE);
    }

    function getName() external pure override returns (string memory) {
        return "StarterStrategy";
    }
}`,
    initialize() {
      return {
        bidBps: 50,
        askBps: 50,
        lines: [13],
        explanation: "`afterInitialize` starts wider than baseline: 50 bps bid and 50 bps ask.",
        stateBadge: "fixed fee: 50/50 bps",
      };
    },
    onSwap() {
      return {
        bidBps: 50,
        askBps: 50,
        lines: [19],
        explanation: "`afterSwap` returns the same constant `FEE`, so each trade keeps 50 bps.",
        stateBadge: "fixed fee: 50/50 bps",
      };
    },
  },

  widenBigTrades: {
    name: "Widen After Big Trades",
    code: `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AMMStrategyBase} from "./AMMStrategyBase.sol";
import {TradeInfo} from "./IAMMStrategy.sol";

contract Strategy is AMMStrategyBase {
    uint256 public constant BASE = 30 * BPS;

    function afterInitialize(uint256, uint256)
        external override returns (uint256, uint256)
    {
        slots[0] = BASE;
        return (BASE, BASE);
    }

    function afterSwap(TradeInfo calldata trade)
        external override returns (uint256, uint256)
    {
        uint256 fee = slots[0];
        uint256 tradeRatio = wdiv(trade.amountY, trade.reserveY);

        if (tradeRatio > WAD / 20) {
            fee = clampFee(fee + 10 * BPS);
        } else if (fee > BASE) {
            fee = fee - 1 * BPS;
        }

        slots[0] = fee;
        return (fee, fee);
    }

    function getName() external pure override returns (string memory) {
        return "Widen After Big Trades";
    }
}`,
    initialize(memory) {
      memory.feeBps = 30;
      return {
        bidBps: 30,
        askBps: 30,
        lines: [12, 13],
        explanation: "`afterInitialize` stores the base fee in `slots[0]` and starts at 30 bps.",
        stateBadge: "slot[0] fee: 30 bps",
      };
    },
    onSwap(memory, ctx) {
      const base = 30;
      const current = Number.isFinite(memory.feeBps) ? memory.feeBps : base;
      let next = current;
      const tradeRatio = ctx.amountY / Math.max(ctx.reserveY, 1e-9);
      let lines = [29, 30];
      let explanation = "No fee change.";

      if (tradeRatio > 0.05) {
        next = clampBps(current + 10);
        lines = [22, 23, 29, 30];
        explanation = `Large trade branch fired: amountY / reserveY = ${formatPct(tradeRatio)} (> 5%), so fee widens by +10 bps to ${next} bps.`;
      } else if (current > base) {
        next = clampBps(current - 1);
        lines = [24, 25, 29, 30];
        explanation = `Decay branch fired: trade size ratio is ${formatPct(tradeRatio)}, so fee decays from ${current} to ${next} bps toward the 30 bps base.`;
      } else {
        lines = [24, 29, 30];
        explanation = `Trade size ratio is ${formatPct(tradeRatio)} and fee is already at base, so the code keeps 30 bps.`;
      }

      memory.feeBps = next;
      return {
        bidBps: next,
        askBps: next,
        lines,
        explanation,
        stateBadge: `slot[0] fee: ${next} bps | tradeRatio: ${formatPct(tradeRatio)}`,
      };
    },
  },
};

const SIM = {
  strategyKey: "baseline30",
  step: 0,
  tradeCount: 0,
  eventSeq: 0,
  fairPrice: 100,
  prevFairPrice: 100,
  strategyMemory: {},
  strategyAmm: null,
  normalizerAmm: null,
  edge: {
    total: 0,
    retail: 0,
    arb: 0,
  },
  pendingEvents: [],
  history: [],
  currentSnapshot: null,
  lastEvent: null,
  lastBadge: "",
  isPlaying: false,
  timer: null,
};

const DOM = {};

document.addEventListener("DOMContentLoaded", init);

function init() {
  cacheDom();
  setupStrategySelect();
  wireEvents();
  updateSpeedLabel();
  resetSimulation();
}

function cacheDom() {
  DOM.strategySelect = document.getElementById("strategySelect");
  DOM.speedRange = document.getElementById("speedRange");
  DOM.speedLabel = document.getElementById("speedLabel");
  DOM.playBtn = document.getElementById("playBtn");
  DOM.stepBtn = document.getElementById("stepBtn");
  DOM.resetBtn = document.getElementById("resetBtn");
  DOM.codeView = document.getElementById("codeView");
  DOM.codeExplanation = document.getElementById("codeExplanation");
  DOM.strategyStateBadge = document.getElementById("strategyStateBadge");
  DOM.clockLabel = document.getElementById("clockLabel");
  DOM.curveChart = document.getElementById("curveChart");
  DOM.fairPriceMetric = document.getElementById("fairPriceMetric");
  DOM.strategySpotMetric = document.getElementById("strategySpotMetric");
  DOM.feesMetric = document.getElementById("feesMetric");
  DOM.edgeMetric = document.getElementById("edgeMetric");
  DOM.tradeTape = document.getElementById("tradeTape");
}

function setupStrategySelect() {
  const options = Object.entries(STRATEGIES)
    .map(([key, strategy]) => `<option value="${key}">${escapeHtml(strategy.name)}</option>`)
    .join("");
  DOM.strategySelect.innerHTML = options;
  DOM.strategySelect.value = SIM.strategyKey;
}

function wireEvents() {
  DOM.strategySelect.addEventListener("change", () => {
    SIM.strategyKey = DOM.strategySelect.value;
    resetSimulation();
  });

  DOM.playBtn.addEventListener("click", () => {
    if (SIM.isPlaying) {
      stopPlayback();
    } else {
      startPlayback();
    }
    renderButtons();
  });

  DOM.stepBtn.addEventListener("click", () => {
    stopPlayback();
    advanceOneTrade();
    renderAll();
  });

  DOM.resetBtn.addEventListener("click", () => {
    resetSimulation();
  });

  DOM.speedRange.addEventListener("input", () => {
    updateSpeedLabel();
    if (SIM.isPlaying) {
      stopPlayback();
      startPlayback();
    }
  });
}

function resetSimulation() {
  stopPlayback();

  const strategy = STRATEGIES[SIM.strategyKey];
  SIM.step = 0;
  SIM.tradeCount = 0;
  SIM.eventSeq = 0;
  SIM.fairPrice = 100;
  SIM.prevFairPrice = 100;
  SIM.pendingEvents = [];
  SIM.history = [];
  SIM.strategyMemory = {};
  SIM.edge = { total: 0, retail: 0, arb: 0 };

  const initResult = strategy.initialize(SIM.strategyMemory);
  SIM.strategyAmm = createAmm(strategy.name, 100, 10000, initResult.bidBps, initResult.askBps, true);
  SIM.normalizerAmm = createAmm("Normalizer 30 bps", 100, 10000, 30, 30, false);

  SIM.lastBadge = initResult.stateBadge || formatFeeBadge(SIM.strategyAmm);
  SIM.currentSnapshot = snapshotState();
  SIM.lastEvent = {
    id: 0,
    step: 0,
    flow: "system",
    isStrategyTrade: false,
    codeLines: initResult.lines || [],
    codeExplanation: initResult.explanation || "Initialized.",
    stateBadge: SIM.lastBadge,
    summary: "Simulation initialized.",
    edgeDelta: 0,
    trade: null,
    snapshot: SIM.currentSnapshot,
  };

  renderCode(strategy.code);
  highlightLines(initResult.lines || []);
  DOM.codeExplanation.textContent = SIM.lastEvent.codeExplanation;
  DOM.strategyStateBadge.textContent = SIM.lastBadge;
  renderAll();
}

function startPlayback() {
  if (SIM.isPlaying) return;
  SIM.isPlaying = true;
  const ms = SPEED_PROFILE[Number(DOM.speedRange.value)].ms;
  SIM.timer = setInterval(() => {
    advanceOneTrade();
    renderAll();
  }, ms);
}

function stopPlayback() {
  SIM.isPlaying = false;
  if (SIM.timer !== null) {
    clearInterval(SIM.timer);
    SIM.timer = null;
  }
}

function advanceOneTrade() {
  ensurePendingEvents();
  if (!SIM.pendingEvents.length) return;

  const event = SIM.pendingEvents.shift();
  SIM.tradeCount += 1;
  SIM.lastEvent = event;
  SIM.currentSnapshot = event.snapshot;

  if (event.isStrategyTrade) {
    SIM.lastBadge = event.stateBadge || SIM.lastBadge;
    DOM.strategyStateBadge.textContent = SIM.lastBadge;
    DOM.codeExplanation.textContent = event.codeExplanation;
    highlightLines(event.codeLines || []);
  } else {
    DOM.strategyStateBadge.textContent = SIM.lastBadge;
    DOM.codeExplanation.textContent = event.codeExplanation;
    highlightLines([]);
  }

  SIM.history.unshift(event);
  if (SIM.history.length > 90) {
    SIM.history.pop();
  }
}

function ensurePendingEvents() {
  let guard = 0;
  while (SIM.pendingEvents.length === 0 && guard < 8) {
    generateNextStep();
    guard += 1;
  }
}

function generateNextStep() {
  SIM.step += 1;

  const oldPrice = SIM.fairPrice;
  const sigma = randomBetween(0.00088, 0.00101);
  const shock = gaussianRandom();
  SIM.fairPrice = Math.max(1, oldPrice * Math.exp(-0.5 * sigma * sigma + sigma * shock));
  SIM.prevFairPrice = oldPrice;

  const priceMove = { from: oldPrice, to: SIM.fairPrice };

  runArbitrageForAmm(SIM.strategyAmm, priceMove);
  runArbitrageForAmm(SIM.normalizerAmm, priceMove);

  const order = generateRetailOrder(SIM.fairPrice);
  routeRetailOrder(order, priceMove);
}

function runArbitrageForAmm(amm, priceMove) {
  const arb = findArbOpportunity(amm, SIM.fairPrice);
  if (!arb || arb.amountX <= 0.00000001) {
    return;
  }

  const trade = arb.side === "sell"
    ? executeSellX(amm, arb.amountX, SIM.step)
    : executeBuyX(amm, arb.amountX, SIM.step);

  if (!trade) return;

  const profit = arb.side === "sell"
    ? trade.amountX * SIM.fairPrice - trade.amountY
    : trade.amountY - trade.amountX * SIM.fairPrice;

  enqueueTradeEvent({
    flow: "arbitrage",
    amm,
    trade,
    order: null,
    arbProfit: profit,
    priceMove,
  });
}

function routeRetailOrder(order, priceMove) {
  if (order.side === "buy") {
    const splits = splitBuyTwoAmms(SIM.strategyAmm, SIM.normalizerAmm, order.sizeY);
    for (const [amm, yAmount] of splits) {
      if (yAmount <= 0.0001) continue;
      const trade = executeBuyXWithY(amm, yAmount, SIM.step);
      if (!trade) continue;
      enqueueTradeEvent({
        flow: "retail",
        amm,
        trade,
        order,
        arbProfit: 0,
        priceMove,
      });
    }
    return;
  }

  const totalX = order.sizeY / SIM.fairPrice;
  const splits = splitSellTwoAmms(SIM.strategyAmm, SIM.normalizerAmm, totalX);
  for (const [amm, xAmount] of splits) {
    if (xAmount <= 0.0001) continue;
    const trade = executeBuyX(amm, xAmount, SIM.step);
    if (!trade) continue;
    enqueueTradeEvent({
      flow: "retail",
      amm,
      trade,
      order,
      arbProfit: 0,
      priceMove,
    });
  }
}

function enqueueTradeEvent({ flow, amm, trade, order, arbProfit, priceMove }) {
  const isStrategyTrade = amm.isStrategy;

  let edgeDelta = 0;
  if (isStrategyTrade) {
    if (flow === "arbitrage") {
      edgeDelta = -arbProfit;
      SIM.edge.arb += edgeDelta;
    } else {
      edgeDelta = trade.side === "buy"
        ? trade.amountX * SIM.fairPrice - trade.amountY
        : trade.amountY - trade.amountX * SIM.fairPrice;
      SIM.edge.retail += edgeDelta;
    }
    SIM.edge.total += edgeDelta;
  }

  let codeLines = [];
  let codeExplanation = "Trade hit the normalizer AMM, so your strategy `afterSwap` was not called.";
  let stateBadge = SIM.lastBadge;
  let feeChange = null;

  if (isStrategyTrade) {
    const strategy = STRATEGIES[SIM.strategyKey];
    const beforeBid = amm.bidFeeBps;
    const beforeAsk = amm.askFeeBps;

    const callback = strategy.onSwap(SIM.strategyMemory, {
      isBuy: trade.side === "buy",
      amountX: trade.amountX,
      amountY: trade.amountY,
      timestamp: trade.timestamp,
      reserveX: trade.reserveX,
      reserveY: trade.reserveY,
      flowType: flow,
      orderSide: order ? order.side : null,
      fairPrice: SIM.fairPrice,
      edgeDelta,
    });

    amm.bidFeeBps = clampBps(callback.bidBps);
    amm.askFeeBps = clampBps(callback.askBps);

    feeChange = {
      beforeBid,
      beforeAsk,
      afterBid: amm.bidFeeBps,
      afterAsk: amm.askFeeBps,
    };

    codeLines = callback.lines || [];
    codeExplanation = callback.explanation || "Strategy updated fees.";
    stateBadge = callback.stateBadge || formatFeeBadge(amm);
    SIM.lastBadge = stateBadge;
  }

  const event = {
    id: ++SIM.eventSeq,
    step: SIM.step,
    flow,
    ammName: amm.name,
    isStrategyTrade,
    trade,
    order,
    arbProfit,
    fairPrice: SIM.fairPrice,
    priceMove,
    edgeDelta,
    feeChange,
    codeLines,
    codeExplanation,
    stateBadge,
    summary: describeTrade({ flow, amm, trade, order, edgeDelta }),
    snapshot: snapshotState(),
  };

  SIM.pendingEvents.push(event);
}

function describeTrade({ flow, amm, trade, order, edgeDelta }) {
  const move = trade.side === "buy" ? "AMM bought X" : "AMM sold X";
  const base = `${amm.name}: ${move} | X=${formatNum(trade.amountX, 4)} | Y=${formatNum(trade.amountY, 2)}`;

  if (flow === "arbitrage") {
    return `${base} | arbitrage against fair price ${formatNum(SIM.fairPrice, 2)}`;
  }

  const orderLabel = order ? `${order.side} ${formatNum(order.sizeY, 2)} Y` : "retail";
  return `${base} | routed from retail ${orderLabel}`;
}

function createAmm(name, reserveX, reserveY, bidFeeBps, askFeeBps, isStrategy) {
  return {
    name,
    reserveX,
    reserveY,
    bidFeeBps,
    askFeeBps,
    feesX: 0,
    feesY: 0,
    isStrategy,
  };
}

function ammK(amm) {
  return amm.reserveX * amm.reserveY;
}

function executeBuyX(amm, amountXIn, timestamp) {
  if (amountXIn <= 0) return null;

  const feeRate = amm.bidFeeBps / 10000;
  const gamma = 1 - feeRate;
  if (gamma <= 0) return null;

  const beforeX = amm.reserveX;
  const beforeY = amm.reserveY;
  const k = ammK(amm);

  const feeX = amountXIn * feeRate;
  const netX = amountXIn * gamma;
  const newX = beforeX + netX;
  const newY = k / newX;
  const amountYOut = beforeY - newY;

  if (!Number.isFinite(amountYOut) || amountYOut <= 0) return null;

  amm.reserveX = newX;
  amm.reserveY = newY;
  amm.feesX += feeX;

  return {
    side: "buy",
    amountX: amountXIn,
    amountY: amountYOut,
    timestamp,
    reserveX: amm.reserveX,
    reserveY: amm.reserveY,
    beforeX,
    beforeY,
    feeBpsUsed: amm.bidFeeBps,
    spotBefore: beforeY / beforeX,
    spotAfter: amm.reserveY / amm.reserveX,
  };
}

function executeSellX(amm, amountXOut, timestamp) {
  if (amountXOut <= 0 || amountXOut >= amm.reserveX) return null;

  const feeRate = amm.askFeeBps / 10000;
  const gamma = 1 - feeRate;
  if (gamma <= 0) return null;

  const beforeX = amm.reserveX;
  const beforeY = amm.reserveY;
  const k = ammK(amm);

  const newX = beforeX - amountXOut;
  const newYWithoutFee = k / newX;
  const netYIn = newYWithoutFee - beforeY;
  if (netYIn <= 0) return null;

  const amountYIn = netYIn / gamma;
  const feeY = amountYIn - netYIn;

  amm.reserveX = newX;
  amm.reserveY = beforeY + netYIn;
  amm.feesY += feeY;

  return {
    side: "sell",
    amountX: amountXOut,
    amountY: amountYIn,
    timestamp,
    reserveX: amm.reserveX,
    reserveY: amm.reserveY,
    beforeX,
    beforeY,
    feeBpsUsed: amm.askFeeBps,
    spotBefore: beforeY / beforeX,
    spotAfter: amm.reserveY / amm.reserveX,
  };
}

function executeBuyXWithY(amm, amountYIn, timestamp) {
  if (amountYIn <= 0) return null;

  const feeRate = amm.askFeeBps / 10000;
  const gamma = 1 - feeRate;
  if (gamma <= 0) return null;

  const beforeX = amm.reserveX;
  const beforeY = amm.reserveY;
  const k = ammK(amm);

  const feeY = amountYIn * feeRate;
  const netY = amountYIn * gamma;
  const newY = beforeY + netY;
  const newX = k / newY;
  const amountXOut = beforeX - newX;

  if (!Number.isFinite(amountXOut) || amountXOut <= 0) return null;

  amm.reserveX = newX;
  amm.reserveY = newY;
  amm.feesY += feeY;

  return {
    side: "sell",
    amountX: amountXOut,
    amountY: amountYIn,
    timestamp,
    reserveX: amm.reserveX,
    reserveY: amm.reserveY,
    beforeX,
    beforeY,
    feeBpsUsed: amm.askFeeBps,
    spotBefore: beforeY / beforeX,
    spotAfter: amm.reserveY / amm.reserveX,
  };
}

function findArbOpportunity(amm, fairPrice) {
  const x = amm.reserveX;
  const y = amm.reserveY;
  const spot = y / x;
  const k = x * y;

  if (spot < fairPrice) {
    const gamma = 1 - amm.askFeeBps / 10000;
    if (gamma <= 0) return null;
    const newX = Math.sqrt(k / (gamma * fairPrice));
    let amountX = x - newX;
    if (!Number.isFinite(amountX) || amountX <= 0) return null;
    amountX = Math.min(amountX, x * 0.99);
    return {
      side: "sell",
      amountX,
      spot,
    };
  }

  if (spot > fairPrice) {
    const gamma = 1 - amm.bidFeeBps / 10000;
    if (gamma <= 0) return null;
    const xVirtual = Math.sqrt((k * gamma) / fairPrice);
    const netX = xVirtual - x;
    const amountX = netX / gamma;
    if (!Number.isFinite(amountX) || amountX <= 0) return null;
    return {
      side: "buy",
      amountX,
      spot,
    };
  }

  return null;
}

function splitBuyTwoAmms(amm1, amm2, totalY) {
  if (totalY <= 0) return [[amm1, 0], [amm2, 0]];

  const x1 = amm1.reserveX;
  const y1 = amm1.reserveY;
  const x2 = amm2.reserveX;
  const y2 = amm2.reserveY;
  const gamma1 = 1 - amm1.askFeeBps / 10000;
  const gamma2 = 1 - amm2.askFeeBps / 10000;

  const A1 = Math.sqrt(Math.max(x1 * gamma1 * y1, 0));
  const A2 = Math.sqrt(Math.max(x2 * gamma2 * y2, 0));
  if (!Number.isFinite(A1) || !Number.isFinite(A2) || A2 <= 0) {
    return [[amm1, totalY / 2], [amm2, totalY / 2]];
  }

  const r = A1 / A2;
  const numerator = r * (y2 + gamma2 * totalY) - y1;
  const denominator = gamma1 + r * gamma2;
  let y1Amount = denominator === 0 ? totalY / 2 : numerator / denominator;
  y1Amount = clamp(y1Amount, 0, totalY);

  return [[amm1, y1Amount], [amm2, totalY - y1Amount]];
}

function splitSellTwoAmms(amm1, amm2, totalX) {
  if (totalX <= 0) return [[amm1, 0], [amm2, 0]];

  const x1 = amm1.reserveX;
  const y1 = amm1.reserveY;
  const x2 = amm2.reserveX;
  const y2 = amm2.reserveY;
  const gamma1 = 1 - amm1.bidFeeBps / 10000;
  const gamma2 = 1 - amm2.bidFeeBps / 10000;

  const B1 = Math.sqrt(Math.max(y1 * gamma1 * x1, 0));
  const B2 = Math.sqrt(Math.max(y2 * gamma2 * x2, 0));
  if (!Number.isFinite(B1) || !Number.isFinite(B2) || B2 <= 0) {
    return [[amm1, totalX / 2], [amm2, totalX / 2]];
  }

  const r = B1 / B2;
  const numerator = r * (x2 + gamma2 * totalX) - x1;
  const denominator = gamma1 + r * gamma2;
  let x1Amount = denominator === 0 ? totalX / 2 : numerator / denominator;
  x1Amount = clamp(x1Amount, 0, totalX);

  return [[amm1, x1Amount], [amm2, totalX - x1Amount]];
}

function generateRetailOrder() {
  const side = Math.random() < 0.5 ? "buy" : "sell";
  const sigma = 0.8;
  const mu = Math.log(20) - 0.5 * sigma * sigma;
  const sample = Math.exp(mu + sigma * gaussianRandom());
  const sizeY = clamp(sample, 4, 90);
  return { side, sizeY };
}

function snapshotState() {
  return {
    step: SIM.step,
    fairPrice: SIM.fairPrice,
    strategy: {
      x: SIM.strategyAmm.reserveX,
      y: SIM.strategyAmm.reserveY,
      bid: SIM.strategyAmm.bidFeeBps,
      ask: SIM.strategyAmm.askFeeBps,
      k: ammK(SIM.strategyAmm),
    },
    normalizer: {
      x: SIM.normalizerAmm.reserveX,
      y: SIM.normalizerAmm.reserveY,
      bid: SIM.normalizerAmm.bidFeeBps,
      ask: SIM.normalizerAmm.askFeeBps,
      k: ammK(SIM.normalizerAmm),
    },
    edge: {
      total: SIM.edge.total,
      retail: SIM.edge.retail,
      arb: SIM.edge.arb,
    },
  };
}

function renderAll() {
  const snapshot = SIM.currentSnapshot || snapshotState();
  renderButtons();
  renderClock(snapshot);
  renderMetrics(snapshot);
  renderChart(snapshot, SIM.lastEvent);
  renderTradeTape();
}

function renderButtons() {
  DOM.playBtn.textContent = SIM.isPlaying ? "Pause" : "Play";
}

function renderClock(snapshot) {
  DOM.clockLabel.textContent = `Step ${snapshot.step} | Trade ${SIM.tradeCount}`;
}

function renderMetrics(snapshot) {
  const strategySpot = snapshot.strategy.y / snapshot.strategy.x;

  DOM.fairPriceMetric.textContent = `${formatNum(snapshot.fairPrice, 4)} Y/X`;
  DOM.strategySpotMetric.textContent = `${formatNum(strategySpot, 4)} Y/X`;
  DOM.feesMetric.textContent = `bid ${formatNum(snapshot.strategy.bid, 0)} bps | ask ${formatNum(snapshot.strategy.ask, 0)} bps`;

  const total = snapshot.edge.total;
  const retail = snapshot.edge.retail;
  const arb = snapshot.edge.arb;
  DOM.edgeMetric.textContent = `${formatSigned(total)} (retail ${formatSigned(retail)}, arb ${formatSigned(arb)})`;
}

function renderTradeTape() {
  if (SIM.history.length === 0) {
    DOM.tradeTape.innerHTML = "<li class='trade-row'>No trades yet. Press Step or Play.</li>";
    return;
  }

  const rows = SIM.history.slice(0, 14).map((event) => {
    const flowClass = event.flow === "arbitrage" ? "arb" : "retail";
    const flowLabel = event.flow === "arbitrage" ? "Arb" : "Retail";

    let edgeLine = "";
    if (event.isStrategyTrade) {
      const edgeClass = event.edgeDelta >= 0 ? "good" : "bad";
      edgeLine = `<div class="trade-edge ${edgeClass}">strategy edge delta: ${formatSigned(event.edgeDelta)}</div>`;
    } else {
      edgeLine = "<div class='trade-edge'>normalizer trade (strategy callback skipped)</div>";
    }

    return `
      <li class="trade-row">
        <div class="trade-top">
          <span class="trade-pill ${flowClass}">${flowLabel}</span>
          <span>t${event.step} | ${escapeHtml(event.ammName)}</span>
        </div>
        <p class="trade-text">${escapeHtml(event.summary)}</p>
        ${edgeLine}
      </li>
    `;
  });

  DOM.tradeTape.innerHTML = rows.join("");
}

function renderChart(snapshot, event) {
  const width = 760;
  const height = 440;
  const margin = { left: 72, right: 24, top: 26, bottom: 58 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const xVals = [snapshot.strategy.x, snapshot.normalizer.x, 100];
  const xMin = Math.max(45, Math.min(...xVals) * 0.72);
  const xMax = Math.max(130, Math.max(...xVals) * 1.36);

  const ySamples = [];
  for (const amm of [snapshot.strategy, snapshot.normalizer]) {
    ySamples.push(amm.k / xMin, amm.k / xMax, amm.y);
  }
  const yMin = Math.max(2800, Math.min(...ySamples) * 0.88);
  const yMax = Math.max(12000, Math.max(...ySamples) * 1.12);

  const xToPx = (x) => margin.left + ((x - xMin) / (xMax - xMin)) * innerW;
  const yToPx = (y) => margin.top + (1 - (y - yMin) / (yMax - yMin)) * innerH;

  let grid = "";
  for (let i = 0; i <= 6; i += 1) {
    const gx = margin.left + (innerW * i) / 6;
    grid += `<line x1="${gx.toFixed(2)}" y1="${margin.top}" x2="${gx.toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="#ddd1c4" stroke-width="1" />`;
  }
  for (let j = 0; j <= 6; j += 1) {
    const gy = margin.top + (innerH * j) / 6;
    grid += `<line x1="${margin.left}" y1="${gy.toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${gy.toFixed(2)}" stroke="#ddd1c4" stroke-width="1" />`;
  }

  const strategyPath = buildCurvePath(snapshot.strategy.k, xMin, xMax, xToPx, yToPx);
  const normalizerPath = buildCurvePath(snapshot.normalizer.k, xMin, xMax, xToPx, yToPx);

  const sx = xToPx(snapshot.strategy.x);
  const sy = yToPx(snapshot.strategy.y);
  const nx = xToPx(snapshot.normalizer.x);
  const ny = yToPx(snapshot.normalizer.y);

  const targetX = Math.sqrt(snapshot.strategy.k / snapshot.fairPrice);
  const targetY = snapshot.strategy.k / targetX;
  const tx = xToPx(targetX);
  const ty = yToPx(targetY);

  let arrow = "";
  if (event && event.trade) {
    const bx = xToPx(event.trade.beforeX);
    const by = yToPx(event.trade.beforeY);
    const ax = xToPx(event.trade.reserveX);
    const ay = yToPx(event.trade.reserveY);
    const stroke = event.isStrategyTrade ? "#8b4a23" : "#b6957e";
    arrow = `<line x1="${bx.toFixed(2)}" y1="${by.toFixed(2)}" x2="${ax.toFixed(2)}" y2="${ay.toFixed(2)}" stroke="${stroke}" stroke-width="2.3" marker-end="url(#arrowHead)" />`;
  }

  DOM.curveChart.innerHTML = `
    <defs>
      <marker id="arrowHead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#9a6d50" />
      </marker>
    </defs>

    <rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>
    ${grid}

    <line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="#c3b3a5" stroke-width="2" />
    <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="#c3b3a5" stroke-width="2" />

    <path d="${normalizerPath}" fill="none" stroke="#d2b8a4" stroke-width="3" stroke-dasharray="8 6" />
    <path d="${strategyPath}" fill="none" stroke="#7f421d" stroke-width="4" />

    ${arrow}

    <circle cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" r="8" fill="#8a4a22" fill-opacity="0.8" />
    <circle cx="${sx.toFixed(2)}" cy="${sy.toFixed(2)}" r="17" fill="none" stroke="#c9a58d" stroke-width="1" />
    <circle cx="${nx.toFixed(2)}" cy="${ny.toFixed(2)}" r="6" fill="#e8d3c2" stroke="#bf9f8a" stroke-width="2" />

    <circle cx="${tx.toFixed(2)}" cy="${ty.toFixed(2)}" r="4" fill="#6f7e96" fill-opacity="0.7" />

    <text x="${(width - 145).toFixed(2)}" y="58" fill="#b48f76" font-size="58" font-family="Cormorant Garamond" font-style="italic">x . y = k</text>
    <text x="${(width - 118).toFixed(2)}" y="86" fill="#c3a791" font-size="26" font-family="Cormorant Garamond" font-style="italic">dy / dx</text>

    <text x="${(width / 2 - 48).toFixed(2)}" y="${(height - 18).toFixed(2)}" fill="#aa8d74" font-size="38" font-family="Cormorant Garamond">Reserve X</text>
    <text x="35" y="${(height / 2 + 40).toFixed(2)}" fill="#aa8d74" font-size="38" font-family="Cormorant Garamond" transform="rotate(-90 35 ${height / 2 + 40})">Reserve Y</text>

    <text x="${(margin.left + 16).toFixed(2)}" y="${(margin.top + 20).toFixed(2)}" fill="#8b4a23" font-size="16" font-family="Space Mono">strategy</text>
    <text x="${(margin.left + 16).toFixed(2)}" y="${(margin.top + 40).toFixed(2)}" fill="#b6957e" font-size="16" font-family="Space Mono">normalizer</text>
  `;
}

function buildCurvePath(k, xMin, xMax, xToPx, yToPx) {
  const points = [];
  const samples = 140;
  for (let i = 0; i <= samples; i += 1) {
    const x = xMin + ((xMax - xMin) * i) / samples;
    const y = k / x;
    if (!Number.isFinite(y)) continue;
    points.push(`${i === 0 ? "M" : "L"}${xToPx(x).toFixed(2)} ${yToPx(y).toFixed(2)}`);
  }
  return points.join(" ");
}

function renderCode(code) {
  const lines = code.replace(/\t/g, "    ").split("\n");
  DOM.codeView.innerHTML = lines
    .map((line, index) => {
      const lineNumber = index + 1;
      const safeText = escapeHtml(line) || "&nbsp;";
      return `
        <div class="code-line" data-line="${lineNumber}">
          <span class="line-no">${String(lineNumber).padStart(2, "0")}</span>
          <span class="line-text">${safeText}</span>
        </div>
      `;
    })
    .join("");
}

function highlightLines(lines) {
  const allRows = DOM.codeView.querySelectorAll(".code-line");
  allRows.forEach((row) => row.classList.remove("active"));

  if (!lines || lines.length === 0) return;

  let firstActive = null;
  for (const line of lines) {
    const row = DOM.codeView.querySelector(`.code-line[data-line='${line}']`);
    if (!row) continue;
    row.classList.add("active");
    if (!firstActive) firstActive = row;
  }

  if (firstActive) {
    firstActive.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function updateSpeedLabel() {
  const profile = SPEED_PROFILE[Number(DOM.speedRange.value)];
  DOM.speedLabel.textContent = profile.label;
}

function formatFeeBadge(amm) {
  return `fees: bid ${formatNum(amm.bidFeeBps, 0)} bps | ask ${formatNum(amm.askFeeBps, 0)} bps`;
}

function clampBps(value) {
  return Math.max(0, Math.min(1000, Math.round(value)));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

function gaussianRandom() {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function formatNum(value, digits = 2) {
  if (!Number.isFinite(value)) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSigned(value) {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${formatNum(value, 3)}`;
}

function formatPct(value) {
  return `${formatNum(value * 100, 2)}%`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
