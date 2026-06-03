# PLAN.md — Conversion Plan for LOGIC.md to Code

## Summary

Build a **Research MVP** first: convert only the measurable spine of `LOGIC.md` into a deterministic, no-lookahead XAU/USD signal engine, then add SMC and BIGEY layers only after backtests show they improve expectancy.

Default choices:
- Use **Python 3.11+**, `pandas`, `numpy`, and `pytest`.
- Use a **custom event-loop backtester**, not `vectorbt`, so spreads, partials, stops, and audit reasons stay explicit.
- Assume **broker OHLCV CSV data** is available under `data/raw/`, normalized to UTC parquet under `data/clean/`.
- Default XAU/USD pip size: `0.01`, configurable in one constants/config module.
- No live execution, no order placement, no forced directional calls.

## Key Implementation Changes

### 1. Scaffold the Engine

Create the repo structure described in `CLAUDE.md`:

- `src/data.py`: load broker CSVs, validate OHLCV schema, normalize timestamps to UTC, resample M1 upward into M15/M30/H1/H4/D1, and align frames by closed candle time.
- `src/features.py`: compute all codeable features from `LOGIC.md`: swings, S/R, flip zones, sessions, volume/range proxy, candle shape, liquidity sweeps, premium/discount, confluence inputs.
- `src/rules.py`, `src/signal.py`, `src/risk.py`, `src/backtest.py`: keep rules small, pure, individually testable, and expose `evaluate(bar_index, frames) -> SignalState`.

Add project config for pytest and imports. Keep `dashboard.html` separate for now; only connect it after the engine can emit stable reports.

### 2. Define Public Contracts

Implement these shared types:

- `SignalState`: `direction`, `entry`, `stop`, `take_profit`, `confidence`, `reasons`, `bar_time`.
- `Direction`: `LONG | SHORT | FLAT`.
- `EngineConfig`: constants for pivot confirmation, S/R lookback, tolerance, volume multiplier, pip size, risk, spread/slippage, session windows, confluence gate.
- `FeatureSnapshot`: one closed-bar feature bundle consumed by rules.

Rules must only read data closed at or before `bar_index`. Any rule needing future bars is invalid unless delayed until confirmation bars are closed.

### 3. Implement Research MVP Rules First

Implement only the measurable spine first:

- Market structure: confirmed pivots with `p=2`, HH/HL/LH/LL, BOS, CHoCH.
- S/R levels: H1 lookback `N=20`, touch count `k=2`, tolerance `0.10%`, levels `[S2, S1, R1, R2]`.
- Sessions: UTC-based Asia/London/NY labels, with breakout entries allowed only in London/NY windows.
- Volume gate: `volume > rolling_20_volume * 1.5`; if volume is missing/unreliable, use candle range proxy and record that in `reasons`.
- Candle confirmation: bull/bear close, strong body, rejection wick, engulfing, close-back-inside.
- Signals: `breakout_long`, `breakout_short`, `rejection_long`, `rejection_short`, `sweep_long`, `sweep_short`, `pullback_flip_long`, `pullback_flip_short`.
- No-trade filters: default `FLAT`, midpoint/no-confirmation, no level interaction, HTF veto, 4th+ motion candle, reward too small.

Do not implement OB/FVG/QM/BIGEY active management in MVP. Add them behind feature flags only after the spine has a baseline report.

### 4. Add Risk and Backtest

Implement fixed-risk sizing:

- Default `risk_per_trade = 0.01`; hard cap `0.02`.
- Position size derives from equity, stop distance, and contract value.
- Minimum RR gate: `1:1.5`.
- First MVP exits: fixed stop and target only; no discretionary early cutting.
- Backtest models spread, slippage, and commission explicitly.
- Backtest output writes reports with expectancy in R, win rate, max drawdown, longest losing streak, trade count, in-sample vs out-of-sample gap, and ±10% parameter sensitivity.

Use walk-forward validation, not a single in-sample backtest.

## Layering Roadmap

After MVP passes tests and produces a baseline report:

1. Add SMC layer behind config flags: OB, FVG, liquidity pools, premium/discount confluence, QM, SMC reversal playbook.
2. Add BIGEY hard filters: vol-time, clean-left room, 150/200 pip extension logic, wick size, wick retrace, BOPCH/BOPCL triggers.
3. Add BIGEY setup names as rule-family refinements, one family at a time.
4. Add tactical management last: partials, breakeven, active cuts, 25–30 pip partial logic.
5. Keep every layer separately measurable against the previous baseline.

## Test Plan

Create focused pytest coverage:

- `test_no_lookahead.py`: mutating future candles must not change a prior signal.
- `test_data.py`: CSV normalization, UTC timestamps, resampling, closed-candle alignment.
- `test_features.py`: pivots, S/R touches, flip zones, sessions, candle shapes, volume gate, sweeps.
- `test_rules.py`: hand-built fixtures for breakout, rejection, fakeout/sweep, pullback flip, and forced `FLAT`.
- `test_risk.py`: risk cap, position sizing, invalid stop distance, RR gate.
- `test_backtest.py`: spread/slippage applied, trade lifecycle correct, metrics calculated honestly.

Acceptance criteria:
- All tests pass.
- `FLAT` is frequent in fixture and sample runs.
- Every directional signal has non-empty `reasons`.
- No rule can improve by reading future/unclosed candles.
- First walk-forward report exists before any rule is treated as useful.

## Assumptions

- First implementation writes this plan into `PLAN.md`.
- Real broker CSV data will eventually be placed in `data/raw/`.
- XAU/USD pip size defaults to `0.01`, but stays configurable.
- Session windows are implemented from `LOGIC.md` defaults and converted from UTC using config.
- Dashboard integration is postponed until engine reports are stable.
- No live trading or broker API integration is included in this plan.

## Not Included Yet

This plan is intentionally a **Research MVP plan**, not a full conversion of every
idea in `LOGIC.md`. The following items are not part of the first build:

- Full SMC automation: order blocks, fair value gaps, candle equilibrium,
  Quasimodo, liquidity-pool ladders, and the full SMC reversal playbook.
- Full confluence engine from `LOGIC.md §13`; MVP confidence can be simple and
  must not be used as proof of edge until backtested.
- Fibonacci logic beyond leaving config space for later validation.
- SMT divergence, multi-asset confirmation, and XAU/XAG comparison.
- BIGEY setup library from `LOGIC.md §29`: A+, impulse, small-body breakout,
  wick-fill, defended breakout, pullback variants, S/R bounce, counter setups,
  and celery play.
- BIGEY execution refinements: wick flip, `BOPCH`/`BOPCL`, re-break entries,
  10–15 pip wick validation, 150/200 pip extension scenarios, and clean-left
  grading beyond the simple MVP room-to-target filter.
- BIGEY active trade management: 25–30 pip partials, breakeven at 6–7 or 15 pips,
  cut 50–75%, entry-candle reflip cuts, own high/low break cuts, and M5 manual
  exit logic.
- Day-level operator rules: max trades per day, max losses per day, no-chart
  windows, mental-state checks, journaling prompts, and weekend review workflow.
- News-event calendar blockout. This requires a reliable economic calendar feed
  before it can be coded honestly.
- Dashboard/report wiring. The current dashboard remains a static/sample UI until
  the engine emits stable JSON reports.
- Live trading, broker API integration, alerts, automated execution, or prop-firm
  account enforcement.

These deferred items should be added only after the MVP produces a baseline
walk-forward report. Each later layer must be measured against the previous
baseline so complexity does not get mistaken for edge.

## Live Data, Historical Data, and ML Plan

### Current project state

This repo is still a research/specification project. It currently has the rule
spec (`LOGIC.md`), this implementation plan, and a static dashboard prototype,
but no `src/`, `tests/`, `data/`, or `reports/` implementation yet. The next
practical milestone is still the deterministic MVP:

1. Build the data loader and closed-candle resampler.
2. Build measurable feature functions.
3. Build rule functions that return `LONG`, `SHORT`, or frequent `FLAT`.
4. Backtest the rules with realistic spread/slippage before considering live
   alerts or ML.

Live data should feed the same `data.py` contracts as historical data. Do not
build a separate live-only logic path.

### Recommended live data approach

Use the broker feed that will actually be used for evaluation/trading as the
primary source. Spot XAU/USD is OTC, so there is no single canonical global
price. Different brokers can have different symbols, spreads, rollovers,
session breaks, candle boundaries, and missing bars. A strategy that backtests
on one feed and runs live on another can fail for reasons unrelated to the
rules.

Minimum live feed requirements:

- Bid and ask, not only midpoint/last price.
- UTC timestamps.
- Tick or 1-minute data, then resample upward to M15/M30/H1/H4/D1 locally.
- Explicit spread capture per bar: open/median/max spread.
- Reconnect handling, heartbeat monitoring, and duplicate/out-of-order tick
  cleanup.
- Candle finalization logic that emits signals only after the candle close.
- Raw tick or M1 persistence under `data/raw/live/` before any feature
  calculation.

Preferred ingestion flow:

```text
provider stream/API
  -> raw bid/ask ticks or M1 candles
  -> UTC parquet append log
  -> local resampler
  -> closed M15/M30/H1/H4/D1 frames
  -> evaluate(bar_index, frames)
  -> alert/report only
```

Do not place live orders from the first version. The first live integration
should be a paper/live-watch mode that only logs signals, spreads, skipped
signals, and reasons.

### API/provider options for live and historical data

1. **MetaTrader 5 broker feed - best first choice if your broker is on MT5.**
   Use the official MetaTrader5 Python package through a logged-in MT5 terminal.
   It can read broker-specific ticks and rates for the exact XAU/USD symbol
   offered by the broker, including broker naming variants such as `XAUUSD`,
   `XAUUSDm`, or `GOLD`. MT5's Python docs say tick calls return fields such as
   time, bid, ask, last, and flags, and that data is stored in UTC.

   Use this when:
   - You already trade or demo-test on an MT5 broker.
   - You want historical and live data to match the broker chart.
   - You can run a local MT5 terminal reliably.

   Implementation notes:
   - Add `src/providers/mt5.py`.
   - Poll `symbol_info_tick()` for live watch mode or use repeated
     `copy_ticks_from()` / `copy_ticks_range()` windows.
   - Use `copy_rates_range()` only as a fallback; prefer tick/M1 capture and
     local resampling.
   - Store the broker symbol mapping in config, not hard-coded in rules.

   Reference: [MetaTrader 5 Python `copy_ticks_from`](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py)

2. **OANDA v20 - good broker API option where account/division supports it.**
   OANDA's v20 API provides pricing streams and candle endpoints. The pricing
   stream is line-delimited JSON over chunked transfer and includes heartbeats;
   candle requests support granularities and a maximum count per request. Check
   account eligibility and whether `XAU_USD` is tradable in the specific OANDA
   division before building around it.

   Use this when:
   - You have an OANDA v20 practice/live account with XAU/USD available.
   - You want a clean REST/streaming API without driving a desktop terminal.
   - You accept that OANDA candles/spreads are OANDA-specific.

   Implementation notes:
   - Add `src/providers/oanda.py`.
   - Use `/pricing/stream` for live bid/ask.
   - Use `/instruments/{instrument}/candles` for backfill and gap repair.
   - Keep `snapshot=True` on reconnect, then de-duplicate by timestamp.
   - Save both bid/ask and computed midpoint; backtests must use bid/ask or
     model spread explicitly.

   Reference: [OANDA v20 pricing and candles](https://developer.oanda.com/rest-live-v20/pricing-ep/)

3. **Twelve Data - useful independent market-data API, not execution truth.**
   Twelve Data lists `XAU/USD` as a commodity aggregate and provides real-time
   price/quote, WebSocket streaming, and time-series endpoints. It is useful for
   prototyping, dashboards, cross-checking, or a non-broker demo feed. It should
   not be treated as the final execution feed unless the backtest and live
   decision system are intentionally based on the same Twelve Data feed.

   Use this when:
   - You need quick API access to XAU/USD without a broker terminal.
   - You want a dashboard/reference feed.
   - You understand that aggregate/composite data may not match your broker's
     fillable bid/ask and spread.

   Implementation notes:
   - Add `src/providers/twelvedata.py`.
   - Use `wss://ws.twelvedata.com/v1/quotes/price` for live prices.
   - Use `/time_series?symbol=XAU/USD&interval=1min` for M1 history/backfill.
   - Treat missing bid/ask as a limitation; model spread from broker history
     before evaluating signals.

   References:
   - [Twelve Data WebSocket streaming](https://support.twelvedata.com/en/articles/5620516-how-to-stream-the-data)
   - [Twelve Data XAU/USD commodity reference](https://twelvedata.com/exchanges/commodity?group=reference)

4. **Dukascopy/JForex - useful for historical tick research and Java live work.**
   Dukascopy's JForex SDK supports strategy development with live data and
   backtesting examples. It is a good research source, but if the eventual live
   broker is not Dukascopy, treat the data as a separate venue/feed and validate
   broker differences.

   Use this when:
   - You want a long historical tick dataset for robustness checks.
   - You are comfortable with Java/JForex tooling.
   - You need a second feed to compare against the broker feed.

   Implementation notes:
   - Keep this as a research/backfill provider unless the whole execution stack
     is JForex.
   - Normalize timestamps and symbols into the same provider-neutral schema.
   - Track source in every row: `provider`, `symbol`, `timezone`, `bid`, `ask`,
     `volume_source`.

   Reference: [Dukascopy JForex API](https://www.dukascopy.com/swiss/english/forex/api/jforex-api/)

Avoid unofficial TradingView scrapers for core research. They can break without
notice, may violate terms, and make reproducibility weak.

### Historical dataset plan

For this project, "dataset" first means clean market data, not ML labels.

Primary dataset:

- Use the same broker/feed intended for live watch mode.
- Collect or download at least 2 years of tick or M1 XAU/USD data.
- Include high-volatility periods: CPI, NFP, FOMC, banking shocks, large USD
  trend regimes, and quiet summer ranges.
- Store raw files unchanged under `data/raw/{provider}/`.
- Normalize to UTC parquet under `data/clean/{provider}/`.
- Resample locally into M15, M30, H1, H4, and D1.

Secondary validation datasets:

- OANDA or MT5 broker history from a second broker.
- Dukascopy tick/M1 history for cross-feed robustness.
- Twelve Data M1 history for an independent aggregate reference.

Required schema for normalized data:

```text
timestamp_utc
symbol
provider
timeframe
open
high
low
close
volume
bid_open
bid_high
bid_low
bid_close
ask_open
ask_high
ask_low
ask_close
spread_open
spread_median
spread_max
is_complete
source_file
```

If a provider does not supply real volume, store tick volume or `NaN` and set a
`volume_source` field. The engine can then use candle range as the participation
proxy described in `LOGIC.md`.

### Do we need to train an ML model?

No ML model is needed for the MVP. The first version should be a deterministic,
auditable signal engine. That is the correct fit for the current `LOGIC.md`
because the rules are discretionary price-action rules being converted into
measurable booleans. Training a model before the rule engine and walk-forward
baseline exist would add complexity without proving edge.

Use ML only after:

1. The deterministic engine is implemented.
2. No-lookahead tests pass.
3. A realistic walk-forward backtest exists.
4. At least 100-300 trades are available after costs.
5. The human has reviewed expectancy, drawdown, losing streak, OOS gap, and
   parameter sensitivity.

If ML is added later, the safest role is not "predict every candle direction".
Better ML roles:

- Signal quality filter: estimate whether a deterministic signal should be
  accepted or skipped.
- Probability calibration: map confluence features to expected R or win
  probability.
- Regime classifier: trend/range/high-volatility/news-risk regime tagging.
- Spread/slippage model: estimate whether current costs are too wide to trade.

Avoid using ML to replace the rules until the deterministic baseline is known.
Also avoid labels that leak the future into features. Labels may use future
returns for training targets, but features for row `t` must only use information
known at or before the closed candle `t`.

### If ML is added later, where does the dataset come from?

The ML dataset should be generated from this project's own cleaned historical
data and backtest outcomes, not downloaded as a generic "XAU/USD ML dataset".

Feature rows:

- One row per closed M15/M30 decision candle.
- Inputs from `features.py`: structure state, distance to S/R, session, volume
  or range proxy, candle shape, sweep flags, HTF bias, spread state, and
  confluence counts.
- Strict timestamp split: train on older periods, validate on later periods,
  test on untouched newest periods.

Candidate labels:

- `accepted_signal_profitable`: whether a deterministic signal reached target
  before stop after costs.
- `forward_r_multiple`: realized R from the backtest exit engine.
- `max_favorable_excursion_r` and `max_adverse_excursion_r`.
- `skip_due_to_cost`: whether spread/slippage made the setup invalid.
- `regime`: trend/range/volatile label computed only from closed historical
  windows.

Dataset sources for ML:

- Best: broker tick/M1 history from MT5 or OANDA, matching the live feed.
- Good secondary: Dukascopy tick/M1 history for robustness checks.
- Useful reference/prototype: Twelve Data M1 `XAU/USD` time series.
- Optional exogenous features: scheduled news calendar, DXY/US10Y data, and
  session/holiday calendars, but only if timestamped and available before the
  decision candle.

ML validation requirements:

- Walk-forward training only; never random shuffle time-series rows.
- Purge/embargo around train/test boundaries if labels use future bars.
- Compare against the deterministic baseline, not against zero.
- Report calibration, precision/recall on accepted trades, expectancy after
  costs, drawdown, and trade count.
- Keep the model out of live watch mode until it improves OOS expectancy and
  does not simply reduce trade count to a flattering tiny sample.

### Implementation additions for data/live readiness

Add these modules after the deterministic MVP scaffold:

```text
src/providers/base.py        # provider interface and normalized schemas
src/providers/mt5.py         # MT5 broker data adapter
src/providers/oanda.py       # OANDA data adapter
src/providers/twelvedata.py  # optional reference/dashboard adapter
src/live.py                  # live watch loop, closed-candle dispatcher
src/storage.py               # parquet append/read utilities
tests/test_providers.py      # schema normalization and reconnect fixtures
tests/test_live_closed_bar.py # no signal before candle close
```

Live mode acceptance criteria:

- Runs in paper/watch mode only.
- Recovers from reconnect without duplicate candles.
- Produces identical signals from saved live raw data when replayed offline.
- Uses the same `evaluate(bar_index, frames)` path as backtests.
- Logs provider, bid/ask spread, candle completeness, signal reasons, and every
  `FLAT` reason.
