"""Custom event-loop backtester with explicit costs and honest metrics.

Design choices (PLAN.md):
- A signal is produced on the **close** of M30 bar ``t``; the trade is filled on
  the **open of bar t+1** with spread + slippage applied adversely. This avoids
  the most common look-ahead bug (filling on the signalling bar's close).
- One position at a time (MVP). Fixed stop / fixed take-profit only — no
  discretionary cutting yet (that is the deferred BIGEY layer).
- Reports the full honest metric set (expectancy in R, max drawdown, longest
  losing streak, trade count, in-sample vs out-of-sample gap) plus a ±10%
  parameter-sensitivity sweep. Flattering-only metrics are never reported alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .contracts import SignalState
from .signal import evaluate
from .risk import position_size


@dataclass
class Trade:
    direction: str
    signal_time: str
    entry_time: str
    exit_time: str
    entry: float
    stop: float
    take_profit: float
    exit_price: float
    outcome: str            # "tp" | "stop" | "eod_close" | "timeout"
    r_multiple: float       # net of costs, in R
    bars_held: int
    reasons: list[str]


@dataclass
class BacktestResult:
    config: dict
    trades: list[Trade]
    metrics: dict
    folds: list[dict] = field(default_factory=list)
    sensitivity: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# core simulation
# --------------------------------------------------------------------------- #
def _apply_entry_cost(direction: str, price: float, cfg: EngineConfig) -> float:
    """Adverse fill: pay half-spread + slippage against you."""
    adverse = cfg.spread_price + cfg.slippage_price
    return price + adverse if direction == "LONG" else price - adverse


def _r_after_costs(direction: str, entry: float, exit_price: float,
                   stop: float, cfg: EngineConfig) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    gross = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
    # exit also pays slippage adversely; commission expressed in price terms / risk
    gross -= cfg.slippage_price
    comm_r = (cfg.commission_per_trade / (risk * cfg.contract_value)
              if cfg.contract_value > 0 else 0.0)
    return gross / risk - comm_r


def run(frames: dict[str, pd.DataFrame], cfg: EngineConfig = DEFAULT_CONFIG,
        start: int | None = None, end: int | None = None,
        max_hold_bars: int = 64) -> list[Trade]:
    """Run the event loop over the primary frame; return realised trades."""
    pf = cfg.primary_frame
    m30 = frames[pf]
    n = len(m30)
    warmup = max(cfg.sr_lookback, cfg.dealing_range_lookback,
                 cfg.volume_ma_window) + cfg.pivot_confirm * 2 + 2
    start = warmup if start is None else max(start, warmup)
    end = n if end is None else min(end, n)

    trades: list[Trade] = []
    i = start
    while i < end - 1:
        sig: SignalState = evaluate(i, frames, cfg)
        if not sig.is_trade():
            i += 1
            continue

        # fill on next bar open with adverse cost
        fill_idx = i + 1
        raw_entry = float(m30["open"].iloc[fill_idx])
        entry = _apply_entry_cost(sig.direction, raw_entry, cfg)
        stop, tp = sig.stop, sig.take_profit

        # stop must still be on the correct side after the real fill
        if (sig.direction == "LONG" and not (stop < entry < tp)) or \
           (sig.direction == "SHORT" and not (tp < entry < stop)):
            i += 1
            continue

        outcome, exit_price, exit_idx = "timeout", float(m30["close"].iloc[min(fill_idx + max_hold_bars, end - 1)]), None
        for j in range(fill_idx, min(fill_idx + max_hold_bars, end)):
            hi = float(m30["high"].iloc[j])
            lo = float(m30["low"].iloc[j])
            if sig.direction == "LONG":
                hit_stop, hit_tp = lo <= stop, hi >= tp
            else:
                hit_stop, hit_tp = hi >= stop, lo <= tp
            if hit_stop and hit_tp:
                # ambiguous bar — assume stop first (conservative)
                outcome, exit_price, exit_idx = "stop", stop, j
                break
            if hit_stop:
                outcome, exit_price, exit_idx = "stop", stop, j
                break
            if hit_tp:
                outcome, exit_price, exit_idx = "tp", tp, j
                break
        if exit_idx is None:
            exit_idx = min(fill_idx + max_hold_bars, end - 1)
            exit_price = float(m30["close"].iloc[exit_idx])
            outcome = "timeout"

        r = _r_after_costs(sig.direction, entry, exit_price, stop, cfg)
        trades.append(Trade(
            direction=sig.direction,
            signal_time=str(m30.index[i]),
            entry_time=str(m30.index[fill_idx]),
            exit_time=str(m30.index[exit_idx]),
            entry=round(entry, 2), stop=round(stop, 2), take_profit=round(tp, 2),
            exit_price=round(exit_price, 2), outcome=outcome,
            r_multiple=round(r, 4), bars_held=exit_idx - fill_idx,
            reasons=sig.reasons,
        ))
        # resume after the trade closes (no overlapping positions)
        i = exit_idx + 1
    return trades


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def compute_metrics(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "note": "no trades — engine stayed FLAT"}
    r = np.array([t.r_multiple for t in trades], dtype=float)
    wins = r[r > 0]
    losses = r[r <= 0]

    # equity curve in R, max drawdown in R
    curve = np.cumsum(r)
    peak = np.maximum.accumulate(curve)
    drawdown = curve - peak
    max_dd_r = float(drawdown.min()) if n else 0.0

    # longest losing streak
    streak = worst = 0
    for x in r:
        if x <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0

    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    return {
        "trade_count": n,
        "win_rate": round(float(len(wins) / n), 4),
        "expectancy_r": round(float(r.mean()), 4),
        "total_r": round(float(r.sum()), 4),
        "avg_win_r": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else float("inf"),
        "max_drawdown_r": round(max_dd_r, 4),
        "longest_losing_streak": worst,
        "outcomes": {k: int(sum(1 for t in trades if t.outcome == k))
                     for k in ("tp", "stop", "timeout")},
        "long_count": int(sum(1 for t in trades if t.direction == "LONG")),
        "short_count": int(sum(1 for t in trades if t.direction == "SHORT")),
    }


def flat_frequency(frames, cfg: EngineConfig = DEFAULT_CONFIG,
                   sample: int = 2000) -> dict:
    """Report how often the engine is FLAT (LOGIC.md: FLAT should dominate)."""
    pf = cfg.primary_frame
    n = len(frames[pf])
    warmup = max(cfg.sr_lookback, cfg.dealing_range_lookback,
                 cfg.volume_ma_window) + cfg.pivot_confirm * 2 + 2
    idxs = range(warmup, n)
    if sample and (n - warmup) > sample:
        idxs = np.linspace(warmup, n - 1, sample).astype(int)
    dirs = [evaluate(i, frames, cfg).direction for i in idxs]
    total = len(dirs)
    flat = sum(1 for d in dirs if d == "FLAT")
    return {
        "bars_evaluated": total,
        "flat_pct": round(flat / total, 4) if total else 0.0,
        "long_pct": round(sum(1 for d in dirs if d == "LONG") / total, 4) if total else 0.0,
        "short_pct": round(sum(1 for d in dirs if d == "SHORT") / total, 4) if total else 0.0,
    }


# --------------------------------------------------------------------------- #
# walk-forward + sensitivity
# --------------------------------------------------------------------------- #
def walk_forward(frames, cfg: EngineConfig = DEFAULT_CONFIG,
                 folds: int = 4) -> list[dict]:
    """Split the timeline into contiguous folds; report metrics per fold and an
    in-sample (first 70%) vs out-of-sample (last 30%) gap."""
    pf = cfg.primary_frame
    n = len(frames[pf])
    warmup = max(cfg.sr_lookback, cfg.dealing_range_lookback,
                 cfg.volume_ma_window) + cfg.pivot_confirm * 2 + 2
    bounds = np.linspace(warmup, n, folds + 1).astype(int)
    out = []
    for k in range(folds):
        s, e = int(bounds[k]), int(bounds[k + 1])
        m = compute_metrics(run(frames, cfg, start=s, end=e))
        m["fold"] = k + 1
        m["span"] = [str(frames[pf].index[s]), str(frames[pf].index[min(e, n) - 1])]
        out.append(m)
    return out


def insample_oos(frames, cfg: EngineConfig = DEFAULT_CONFIG,
                 split: float = 0.7) -> dict:
    pf = cfg.primary_frame
    n = len(frames[pf])
    warmup = max(cfg.sr_lookback, cfg.dealing_range_lookback,
                 cfg.volume_ma_window) + cfg.pivot_confirm * 2 + 2
    cut = int(warmup + (n - warmup) * split)
    is_m = compute_metrics(run(frames, cfg, start=warmup, end=cut))
    oos_m = compute_metrics(run(frames, cfg, start=cut, end=n))
    gap = None
    if is_m.get("trade_count") and oos_m.get("trade_count"):
        gap = round(is_m["expectancy_r"] - oos_m["expectancy_r"], 4)
    return {"in_sample": is_m, "out_of_sample": oos_m, "expectancy_gap_r": gap}


def sensitivity(frames, cfg: EngineConfig = DEFAULT_CONFIG) -> list[dict]:
    """Re-run at ±10% thresholds. Collapsing results = fragile (CLAUDE.md)."""
    out = []
    for label, factor in (("baseline", 1.0), ("-10%", 0.9), ("+10%", 1.1)):
        c = cfg if factor == 1.0 else cfg.scaled(factor)
        m = compute_metrics(run(frames, c))
        out.append({"variant": label, "expectancy_r": m.get("expectancy_r"),
                    "trade_count": m.get("trade_count"),
                    "total_r": m.get("total_r")})
    return out


# --------------------------------------------------------------------------- #
# full report
# --------------------------------------------------------------------------- #
def full_report(frames, cfg: EngineConfig = DEFAULT_CONFIG,
                write_dir: str | Path | None = "reports") -> BacktestResult:
    trades = run(frames, cfg)
    metrics = compute_metrics(trades)
    metrics["flat_frequency"] = flat_frequency(frames, cfg)
    result = BacktestResult(
        config=_config_summary(cfg),
        trades=trades,
        metrics=metrics,
        folds=walk_forward(frames, cfg),
        sensitivity=sensitivity(frames, cfg),
        notes=[
            "Research output only — NOT financial advice, NO live orders.",
            "Costs modelled: spread + slippage + commission (see config).",
            "FLAT is the intended majority output; high trade counts are suspect.",
            "Walk-forward + ±10% sensitivity included; read OOS gap before trusting.",
        ],
    )
    result.metrics["insample_oos"] = insample_oos(frames, cfg)
    if write_dir:
        _write_report(result, write_dir)
    return result


def _config_summary(cfg: EngineConfig) -> dict:
    return {
        "symbol": cfg.symbol, "pip_size": cfg.pip_size,
        "risk_per_trade": cfg.risk_per_trade, "risk_cap": cfg.risk_cap,
        "min_rr": cfg.min_rr, "target_rr": cfg.target_rr,
        "volume_multiplier": cfg.volume_multiplier,
        "sr_lookback": cfg.sr_lookback, "sr_touch_count": cfg.sr_touch_count,
        "spread_price": cfg.spread_price, "slippage_price": cfg.slippage_price,
        "commission_per_trade": cfg.commission_per_trade,
        "enable_smc_layer": cfg.enable_smc_layer,
        "enable_bigey_layer": cfg.enable_bigey_layer,
    }


def _write_report(result: BacktestResult, write_dir: str | Path) -> Path:
    d = Path(write_dir)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_utc": stamp,
        "config": result.config,
        "metrics": result.metrics,
        "folds": result.folds,
        "sensitivity": result.sensitivity,
        "notes": result.notes,
        "trades": [asdict(t) for t in result.trades],
    }
    json_path = d / f"backtest_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    (d / "latest.json").write_text(json.dumps(payload, indent=2))
    (d / f"backtest_{stamp}.md").write_text(_markdown(payload))
    (d / "latest.md").write_text(_markdown(payload))
    return json_path


def _markdown(p: dict) -> str:
    m = p["metrics"]
    ff = m.get("flat_frequency", {})
    io = m.get("insample_oos", {})
    lines = [
        f"# XAU/USD backtest — {p['generated_utc']}",
        "",
        "> Research output only. NOT financial advice. No live orders placed.",
        "",
        "## Headline metrics",
        f"- Trades: **{m.get('trade_count', 0)}**  (long {m.get('long_count','-')} / short {m.get('short_count','-')})",
        f"- Expectancy: **{m.get('expectancy_r','-')} R/trade**",
        f"- Win rate: {m.get('win_rate','-')}  | Profit factor: {m.get('profit_factor','-')}",
        f"- Total: {m.get('total_r','-')} R | Max drawdown: {m.get('max_drawdown_r','-')} R",
        f"- Longest losing streak: {m.get('longest_losing_streak','-')}",
        f"- Outcomes: {m.get('outcomes','-')}",
        "",
        "## FLAT frequency (should dominate)",
        f"- FLAT {ff.get('flat_pct','-')} | LONG {ff.get('long_pct','-')} | SHORT {ff.get('short_pct','-')}",
        "",
        "## In-sample vs out-of-sample",
        f"- IS expectancy: {io.get('in_sample',{}).get('expectancy_r','-')} R "
        f"({io.get('in_sample',{}).get('trade_count','-')} trades)",
        f"- OOS expectancy: {io.get('out_of_sample',{}).get('expectancy_r','-')} R "
        f"({io.get('out_of_sample',{}).get('trade_count','-')} trades)",
        f"- Expectancy gap (IS-OOS): {io.get('expectancy_gap_r','-')} R  "
        "(large positive gap = curve-fitting)",
        "",
        "## ±10% parameter sensitivity",
    ]
    for s in p["sensitivity"]:
        lines.append(f"- {s['variant']}: {s.get('expectancy_r','-')} R over "
                     f"{s.get('trade_count','-')} trades (total {s.get('total_r','-')} R)")
    lines += ["", "## Walk-forward folds"]
    for f in p["folds"]:
        lines.append(f"- Fold {f.get('fold')}: {f.get('expectancy_r','-')} R "
                     f"over {f.get('trade_count','-')} trades")
    lines += ["", "## Notes"] + [f"- {n}" for n in p["notes"]]
    return "\n".join(lines) + "\n"
