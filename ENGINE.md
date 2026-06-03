# Signal Engine (Python backend)

The deterministic LONG/SHORT/FLAT engine described in `LOGIC.md` and `PLAN.md`.
This is the **research/paper** backend — it never places live orders
(CLAUDE.md hard rule). The Node dashboard (`server.js` / `dashboard.html`) is a
separate live-chart UI and is not required to run the engine.

## What is implemented (MVP spine — PLAN.md §3)

Measurable, no-look-ahead rules only:

- **Structure** (`features.py`): confirmed pivots (`p=2`), HH/HL/LH/LL, BOS/CHoCH.
- **Levels** (`features.py`): H1 S/R clusters (`N=20`, `k=2` touches, 0.10% tol)
  → `[S2, S1, R1, R2]`; dealing range + premium/discount/equilibrium zone.
- **Sessions** (`config.py`): UTC Asia / London / NY / NY-PM, kill-zone gating.
- **Volume gate** (`features.py`): `vol > 1.5× MA(20)`, with a candle-range proxy
  fallback when provider volume is missing (tagged in `reasons`).
- **Signals** (`rules.py`): `breakout_{long,short}`, `rejection_{long,short}`,
  `sweep_{long,short}`, `pullback_flip_{long,short}`.
- **FLAT filters + HTF veto** (`rules.py`): between-levels, Asia/Q1, exhaustion,
  equilibrium, against-H4-bias, opposing-level-overhead.
- **Risk** (`risk.py`): fixed-fractional sizing (1% default, **2% hard cap**),
  RR ≥ 1.5 gate, fixed stop/TP.
- **Backtest** (`backtest.py`): next-bar fills, explicit spread+slippage+commission,
  walk-forward folds, in-sample vs OOS gap, ±10% sensitivity, honest metric set.

Deferred behind flags (`enable_smc_layer`, `enable_bigey_layer`): OB/FVG/QM,
the SMC reversal playbook, and the entire BIGEY execution layer (§24–§33).

## The one public contract

```python
from src.signal import evaluate
sig = evaluate(bar_index, frames, cfg)   # -> SignalState
```

`frames` is `{"M30": df, "H1": df, "H4": df, "D1": df, "M15": df}`, each a
UTC-indexed OHLCV frame (candle **open** time). `evaluate` is pure and only reads
bars closed at/before `bar_index`.

## Quick start

```bash
pip install -r requirements.txt
pytest -q                                   # tests incl. no-look-ahead + bi5 decode

# --- Data backfill: two sources ---
# A) Dukascopy — NO API key, NO rate quota, deep history, real tick volume.
#    Serves one LZMA tick file per hour, so long ranges are slow (run in bg).
python scripts/fetch_dukascopy.py --all --start 2024-01-01     # M15..D1 from one tick pull

# B) Twelve Data — quick, but free tier caps at 5000 bars/call + daily quota,
#    and the composite XAU/USD has no volume (engine uses the range proxy).
python scripts/fetch_twelvedata.py --all    # M15/M30/H1/H4/D1   (uses TWELVE_DATA_API_KEY)

# Walk-forward backtest -> reports/latest.md + latest.json
python scripts/run_backtest.py --from-clean data/clean/dukascopy   # or .../twelvedata

# Paper / watch mode (NO orders — logs signals only)
python -m src.live --once                   # evaluate latest closed M30 bar
python -m src.live --watch 1800             # re-evaluate every 30 min
```

Paper outputs: `reports/paper_signals.jsonl` (every evaluation + reasons),
`reports/paper_trades.jsonl` (closed paper trades), `reports/paper_book.json`
(open paper positions).

## Layout

```
src/
  config.py      EngineConfig — every tunable + session windows
  contracts.py   SignalState, FeatureSnapshot, Level
  data.py        load/normalise/resample + visible_frame() no-look-ahead slice
  features.py    all measurable features -> FeatureSnapshot
  rules.py       the signals + FLAT filters + HTF veto
  signal.py      evaluate(bar_index, frames) -> SignalState
  risk.py        fixed-fractional sizing + RR gate
  backtest.py    event loop, costs, walk-forward, metrics, report writer
  live.py        paper/watch loop (same evaluate path, no orders)
  providers/     dukascopy.py (no-key tick feed, real volume),
                 twelvedata.py (reference feed), base.py (interface)
scripts/         fetch_dukascopy.py, fetch_twelvedata.py, run_backtest.py
tests/           no-lookahead, data, features, rules, risk, backtest
```

## Honesty notes (read before trusting any number)

- On a random-walk sanity check the engine prints **negative** expectancy and
  ~94% FLAT — that is correct: there is no edge in noise, and the engine does not
  manufacture one.
- Twelve Data XAU/USD is a **composite** feed with no volume and not a broker's
  fillable bid/ask. It is fine for prototyping/dashboards; validate on the actual
  execution broker's feed before believing results (PLAN.md "Live Data").
- No rule is "real" until a walk-forward report with a small IS↔OOS gap and
  stable ±10% sensitivity says so.
