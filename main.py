"""EMA 5/20 crossover bot (btc-race-24h-20261117-0603, v3).

Protocol: agent-fun/bars-replay/v1. The harness sends hello once, then one
batch per 1-minute bar, then done. This bot answers every batch with exactly
one reply echoing its seq, carrying its orders for that bar (at most 16,
here at most 1). Fills land at the NEXT bar's open, so every decision here
is made on the bar that just closed.

Strategy (Gadiel's spec: EMA 5 vs EMA 20 on 1-minute closes):
  Buy only on a FRESH cross: EMA5 crosses above EMA20 on this bar's close
  (above now, at or under on the bar before). Full size, one position max,
  long only. Never buy while already above -- a cross is an event, not a
  state.

Risk management (tuned on the dev window, kept simple and explainable):
  - Take-profit +120 bps above the entry close.
  - Stop-loss -150 bps below the entry close. It never fires on the dev
    window (no leg falls that far before the take-profit or the flatten),
    so it costs nothing there; it is live insurance for the race window.
  - Dip filter: a cross only counts after a falling hour (60-minute
    return <= -40 bps, once 60 bars of history exist). Crosses after
    an up-hour are chased rallies, and they lose on this window.
  - Max-drawdown circuit breaker: if equity falls 10% below its peak
    (well inside the 25% guardrail), flatten and stop trading for the
    rest of the window. Also never fires on dev (max DD there is 1.73%).
  - No entry in the final 60 bars: a trade opened there has no time to
    reach its take-profit and the end flatten just eats its fees.
  - max_slippage_bps 500 (5%): a 1-minute gap in dev never exceeded
    ~80 bps, so it never blocks a normal entry.

What the dev window actually does to this idea, measured in this session
before this bot was written (simulator mirroring the harness fill model):
  * The plain crossover -- buy on cross up, sell on cross back down --
    is a disaster: -$131.85, 88 trades (44 round trips), max DD 13.20%.
    The window is a slow range; EMA5/EMA20 whipsaws across itself all
    day and every round trip pays ~26-35 bps in fees, slippage and
    impact. Selling on the cross back down is the trap: it banks every
    whipsaw loss.
  * Dropping the cross-down exit and holding for the take-profit rescues
    it: only the crosses that grow into a +120 bps leg pay, the rest are
    held to the end flatten. TP 80/100/120/150 score +$7.14/+$9.25/
    +$11.95/+$10.18; TP 120 is the plateau centre. Trailing stops hurt
    (trail 90/120/150 all score worse), time stops hurt, gap filters
    (require EMA gap >= 8-10 bps at the cross) score +$10.93 on a single
    trade -- thinner, not better.
  * One more filter earns its keep: only take a cross after a falling
    hour (60-minute return <= -40 bps). The two winning legs cross at
    -89 and -140 bps; the losing leg crosses at +16 bps after an
    up-hour. The filter lifts the score to +$19.84 with the same 5
    trades and a -20 to -60 bps plateau of +$18.63 to +$19.84, so it is
    a real separation, not a tuned peak -- and it matches what v2 found
    independently: dips revert, rallies do not continue.
  * Honest limits: the edge is 2 winning legs in the first half
    (+$12.82) plus a small gain in the second half (+$0.85). It
    passes the dev gate with margin over $0 but is still only half of
    v2's +$39.86 deep-dip rider, which catches the window's real
    structure (deep dips revert +170 to +250 bps). EMA momentum does
    not continue here -- the window's only big spike is followed by
    -152 bps.

Verified on the dev window by running this file itself through the
wire protocol (hello/batch/done, the harness's own fill model, two
runs byte-identical): net PnL +$19.84 on $1,000, 5 filled orders
(3 buys, 2 sells, plus the end flatten), max drawdown 1.73%,
Sharpe +1.06. At doubled fees still +$17.41.

Deterministic: float64 EMAs seeded as the mean of the first span of
closes (like the house ema-cross baseline), integer bps comparisons
for exits, no clock, no environment, no randomness, no files, no
network. The only state is the two EMAs, the close history needed for
the cross, and the position record below.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
WINDOW_BARS = 1440  # published window length (RULES.txt: 1,440 bars)
FAST_SPAN = 5
SLOW_SPAN = 20
# Take-profit: +120 bps above the entry close.
TAKE_PROFIT_BPS = 120
# Stop-loss: -150 bps below the entry close.
STOP_LOSS_BPS = 150
# Dip filter: a cross only counts when the 60-minute close return is at
# or below -40 bps (once 60 bars of history exist). The two winning legs
# cross at -89 and -140 bps; the losing leg crosses at +16 bps, after an
# up-hour -- chasing a cross after a rally is exactly what does not work
# on this window. -20 to -60 bps all score +$18.63 to +$19.84, so -40 is
# the plateau centre, not a sharp peak. Before bar 60 there is no
# 60-minute return yet, so the filter does not apply (the first leg
# crosses at bar 22).
DIP_FILTER_BPS = 40
LOOKBACK_BARS = 60
# Circuit breaker: flatten and halt if equity drawdown reaches 10%.
BREAKER_BPS = 1000
# Do not open in the final 60 bars.
LAST_ENTRY_BAR = WINDOW_BARS - 60
# A buy must be at least 1 USDC (the harness min notional).
MIN_NOTIONAL = 1_000_000
# max_slippage_bps: 500 (5%).
MAX_SLIPPAGE_BPS = 500


def _up_at_least(now, then, bps):
    """True when now is at least `bps` above `then` (integer math)."""
    return now * 10000 >= then * (10000 + bps)


def _down_at_least(now, then, bps):
    """True when now is at least `bps` below `then` (integer math)."""
    return now * 10000 <= then * (10000 - bps)


class Ema:
    """One EMA: seeded as the mean of its first span of closes."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def step(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


class Crossover:
    """The whole bot state: two EMAs, the cross, the position, the peak."""

    def __init__(self):
        self.fast = Ema(FAST_SPAN)
        self.slow = Ema(SLOW_SPAN)
        self.closes = []
        self.prev_gap = None  # fast - slow after the previous bar
        self.bar_no = -1
        self.held = False
        self.entry_close = 0
        self.peak_equity = 0
        self.halted = False

    def note_fill(self, state, close):
        """Reconcile with the real account after each batch.

        A buy/sell the market rejected leaves the flag wrong. A position
        with no flag is re-adopted (its exit is re-evaluated fresh); a
        flag with no position is forgotten.
        """
        pos = state.positions.get(SYMBOL)
        have = pos is not None and pos.qty > 0
        if have and not self.held:
            self.held = True
            self.entry_close = close
        elif self.held and not have:
            self.held = False

    def decide(self, state, batch):
        if state.warming_up:
            return []
        if not any(b.get("symbol") == SYMBOL for b in batch.get("bars", [])):
            return []  # gap minute: no bar, the stale close must not count
        close = state.close(SYMBOL)
        if close is None:
            return []
        # Circuit breaker runs on equity, before any new decision.
        if state.equity > self.peak_equity:
            self.peak_equity = state.equity
        if self.peak_equity > 0:
            dd_bps = (self.peak_equity - state.equity) * 10000 // self.peak_equity
            if dd_bps >= BREAKER_BPS:
                self.halted = True
        self.bar_no += 1
        self.closes.append(close)
        self.fast.step(close)
        self.slow.step(close)
        self.note_fill(state, close)
        if self.halted:
            if self.held:
                held = state.positions.get(SYMBOL)
                self.held = False
                if held is not None and held.qty > 0:
                    return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                             "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []
        if self.fast.value is None or self.slow.value is None:
            return []
        gap = self.fast.value - self.slow.value
        prev_gap = self.prev_gap
        self.prev_gap = gap

        if not self.held:
            if self.bar_no > LAST_ENTRY_BAR or prev_gap is None:
                return []
            # Fresh cross up only: above now, at or under on the bar before,
            # and only after a falling hour (dip filter, once history exists).
            if gap > 0 and prev_gap <= 0:
                if len(self.closes) > LOOKBACK_BARS and \
                        not _down_at_least(close, self.closes[-LOOKBACK_BARS - 1],
                                           DIP_FILTER_BPS):
                    return []
                amount = min(state.cash, state.weight_room(SYMBOL))
                if amount < MIN_NOTIONAL:
                    return []
                self.held = True
                self.entry_close = close
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": amount,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Holding: take-profit or stop-loss only. The cross back down is
        # deliberately NOT an exit (measured -$131.85 on dev).
        if _up_at_least(close, self.entry_close, TAKE_PROFIT_BPS) or \
                _down_at_least(close, self.entry_close, STOP_LOSS_BPS):
            self.held = False
            held = state.positions.get(SYMBOL)
            if held is None or held.qty <= 0:
                return []
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []


_RIDER = Crossover()


def on_batch(state, batch):
    return _RIDER.decide(state, batch)


makefun_bars.run(on_batch)
