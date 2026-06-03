# AGENTS.md

Project context for Codex (or any AI coding assistant) working in this repo.

## What we are building

A **decision-support signal engine** for XAU/USD that, on each closed candle,
emits one of three states: `LONG`, `SHORT`, or `FLAT` (no trade). The engine
encodes a discretionary price-action method (see `LOGIC.md`) as deterministic,
testable rules so we can backtest it instead of eyeballing charts.

This is a research/learning tool. It is NOT financial advice and is NOT
permitted to place live orders until it has passed out-of-sample validation
and the human operator explicitly enables execution.

## Hard rules (do not violate)

1. **No look-ahead.** A signal for candle *t* may only use data closed at or
   before *t*. Never use the high/low/close of the candle you are signalling on
   until it is closed. Most fake "profitable" backtests come from this bug.
2. **FLAT is a valid, frequent output.** The method spends most of its time in a
   no-trade zone. Do not force a directional call.
3. **Every rule must be measurable.** If a rule can't be expressed as a boolean
   over numeric inputs, it doesn't go in the engine — it goes in `LOGIC.md`'s
   "discretionary / not yet coded" section.
4. **Risk is capped per trade.** Position sizing always derives from a fixed
   fractional risk (default 1%, never above 2%) and the stop distance.
5. **Backtest before believe.** No rule change ships without a fresh walk-forward
   report. Curve-fitting to the last 7 sessions is not validation.

## Tech stack

- Python 3.11+
- `pandas`, `numpy` for data wrangling
- `pandas-ta` (or hand-rolled) for indicators
- `vectorbt` or a simple custom loop for backtesting
- `pytest` for rule unit tests
- `matplotlib`/`plotly` only for inspection, never in the hot path

## Repo layout

```
.
├── AGENTS.md            # this file
├── LOGIC.md             # the trading logic spec → source of truth for rules
├── data/
│   ├── raw/             # broker CSVs (M15, M30, H1, H4, D1)
│   └── clean/           # normalised OHLCV parquet
├── src/
│   ├── data.py          # load, resample, align timeframes
│   ├── features.py      # S/R, swings, sessions, volume, candle shapes
│   ├── rules.py         # the LONG/SHORT/FLAT logic from LOGIC.md
│   ├── signal.py        # combine features+rules → SignalState per bar
│   ├── risk.py          # SL/TP placement, position sizing
│   └── backtest.py      # walk-forward engine + metrics
├── tests/
│   ├── test_no_lookahead.py
│   ├── test_rules.py
│   └── test_risk.py
└── reports/             # generated backtest output
```

## Data requirements

- Source: your broker's historical OHLCV for XAUUSD. Get **real tick or M1 data**
  and resample up; do not trust pre-aggregated low-TF candles from random sites.
- Timeframes needed: M15 (entry), M30 (primary), H1 (structure), H4/D1 (bias).
- Timezone: store everything in UTC. Session logic converts to broker/London/NY
  locally. Gold's "day" and the London/NY opens are the whole game here.
- Minimum history for a believable test: 2+ years, covering trending, ranging,
  and high-volatility (NFP/CPI/FOMC) regimes.

## The signal contract

`signal.py` exposes one pure function:

```python
def evaluate(bar_index: int, frames: dict[str, pd.DataFrame]) -> SignalState
```

Returns a `SignalState` dataclass:

```python
@dataclass
class SignalState:
    direction: Literal["LONG", "SHORT", "FLAT"]
    entry: float | None
    stop: float | None
    take_profit: float | None
    confidence: float          # 0..1, from rule confluence count
    reasons: list[str]         # which rules fired, for the audit log
    bar_time: pd.Timestamp
```

`reasons` is mandatory — every signal must be explainable after the fact.

## Definition of done for any rule change

1. Rule is written in `LOGIC.md` in plain language first.
2. Rule is implemented in `rules.py` with a matching `pytest` case using a
   hand-built fixture candle series (known input → known output).
3. `test_no_lookahead.py` still passes.
4. A walk-forward backtest is run; `reports/` updated.
5. Metrics reviewed by the human before merge.

## Metrics that matter (and the ones that lie)

Report all of these, not just the flattering ones:

- **Expectancy per trade** (R multiples), not win rate alone. A 40% win rate
  can be very profitable; an 80% win rate can lose money.
- **Max drawdown** and **longest losing streak** — can you actually sit through it?
- **Out-of-sample vs in-sample** spread — a big gap means curve-fitting.
- **Trade count** — < ~100 trades and the stats are noise.
- **Sensitivity** — re-run with parameters ±10%. If results collapse, it's fragile.
- **Slippage + spread + commission** modelled explicitly. Gold spreads widen
  exactly at the session opens this method trades. Untested assumption = blown account.

## Things to be honest about (read this before trusting anything)

- The source method comes from social-media trade screenshots. Posted trades are
  self-selected wins; the losing trades and the blown accounts don't get posted.
- A clean-looking rule on a screenshot is survivorship bias until proven otherwise.
- Verify the broker's regulatory status independently before funding anything.
- "It worked on the last 7 sessions" is the single most dangerous sentence in
  trading. Seven sessions is ~2 weeks of one regime. Treat it as an anecdote.
- The goal of this repo is to find out whether the edge is *real*, with the full
  expectation that the answer might be "no." Build it so a negative result is
  just as visible as a positive one.

## How Codex should work in this repo

- When asked to add a rule, first update `LOGIC.md`, then `rules.py`, then tests.
- Never silently change risk parameters or remove the no-look-ahead guard.
- If a request would make the backtest look better by relaxing realism
  (removing spread, peeking at future bars, dropping FLAT), refuse and explain.
- Prefer small, individually-testable rule functions over one giant conditional.
- Keep `reasons` strings in sync with the rule names so the audit log is readable.