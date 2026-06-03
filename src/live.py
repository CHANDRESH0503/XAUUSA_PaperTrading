"""Paper / live-watch loop.

Watch mode only — logs signals and tracks a paper book; never places orders.
Uses the exact same ``evaluate()`` path as the backtester.

Data source: Dukascopy clean parquets + fresh live ticks (no API key needed).
If a SIGNAL_WEBHOOK_URL is set in .env the signal JSON is also POSTed there
(works with any HTTP webhook: Telegram bot, Slack, Make.com, etc.).

Run:
    python -m src.live --once          # one evaluation of the latest closed M30 bar
    python -m src.live --watch 1800    # re-evaluate every 30 min (M30 cadence)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .contracts import SignalState
from .data import load_parquet, ohlcv_from_ticks, closed_bars_only
from .providers import dukascopy as dk
from .signal import evaluate

TF_LABEL = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}
CLEAN_DIR = Path("data/clean/dukascopy")
REPORT_DIR = Path("reports")
SIGNAL_LOG  = REPORT_DIR / "paper_signals.jsonl"
TRADE_LOG   = REPORT_DIR / "paper_trades.jsonl"
BOOK_PATH   = REPORT_DIR / "paper_book.json"
LIVE_LOOKBACK_HOURS = 6


def fetch_frames(cfg: EngineConfig = DEFAULT_CONFIG) -> dict[str, pd.DataFrame]:
    """Load clean Dukascopy parquets and extend with the last few hours of live ticks."""
    needed = [cfg.primary_frame, *cfg.htf_frames, *cfg.ltf_frames]
    now = pd.Timestamp.now(tz="UTC")

    # 1. Load from parquet
    frames: dict[str, pd.DataFrame] = {}
    for tf in needed:
        label = TF_LABEL.get(tf)
        if label:
            p = CLEAN_DIR / f"XAUUSD_{label}.parquet"
            if p.exists():
                frames[tf] = load_parquet(p)

    # 2. Extend entry-timeframes with fresh ticks (best-effort).
    #    Lookback covers at least LIVE_LOOKBACK_HOURS but also bridges any gap
    #    between the parquet's last bar and now (overnight, weekends, etc.).
    try:
        end = datetime.now(timezone.utc)
        latest_bar = max(
            (df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
             for df in frames.values() if not df.empty),
            default=None,
        )
        if latest_bar is not None:
            gap_h = (end - latest_bar).total_seconds() / 3600
            lookback = max(LIVE_LOOKBACK_HOURS, int(gap_h) + 2)
        else:
            lookback = LIVE_LOOKBACK_HOURS
        start = end - timedelta(hours=lookback)
        ticks = dk.fetch_ticks("XAUUSD", start, end, threads=4)
        if not ticks.empty:
            for tf in ("M15", "M30", "H1"):
                if tf not in frames:
                    continue
                fresh = ohlcv_from_ticks(ticks, tf, price_col="mid", volume_col="volume")
                if not fresh.empty:
                    combined = pd.concat([frames[tf], fresh])
                    frames[tf] = (combined
                                  .loc[~combined.index.duplicated(keep="last")]
                                  .sort_index())
    except Exception as exc:
        print(f"[live] live tick fetch failed ({exc}); using parquet data only")

    # 3. Trim to closed bars
    return {tf: closed_bars_only(df, tf, now) for tf, df in frames.items()}


def evaluate_latest(cfg: EngineConfig = DEFAULT_CONFIG,
                    frames: dict | None = None) -> tuple[SignalState, dict]:
    """Evaluate the most recent closed primary-frame bar."""
    frames = frames or fetch_frames(cfg)
    pf = cfg.primary_frame
    if pf not in frames or len(frames[pf]) < 2:
        raise RuntimeError(f"Not enough {pf} data to evaluate")
    idx = len(frames[pf]) - 1
    sig = evaluate(idx, frames, cfg)
    meta = {
        "provider": "dukascopy",
        "symbol": cfg.symbol,
        "evaluated_utc": datetime.now(timezone.utc).isoformat(),
        "primary_bar_time": str(frames[pf].index[idx]),
        "last_price": float(frames[pf]["close"].iloc[idx]),
        "bars": {tf: len(frames.get(tf, [])) for tf in frames},
    }
    return sig, meta


def _notify(record: dict) -> None:
    """POST the signal JSON to SIGNAL_WEBHOOK_URL if configured (optional)."""
    url = os.environ.get("SIGNAL_WEBHOOK_URL", "").strip()
    if not url:
        return
    sig = record.get("signal", {})
    direction = sig.get("direction", "FLAT")
    # Skip FLAT notifications unless a position is involved
    if direction == "FLAT" and not record.get("paper_action"):
        return
    payload = json.dumps(record, default=str).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as exc:
        print(f"[live] webhook notify failed: {exc}")


# --------------------------------------------------------------------------- #
# paper book — tracks open paper trades, no real orders
# --------------------------------------------------------------------------- #
@dataclass
class PaperTrade:
    direction: str
    signal_time: str
    entry: float
    stop: float
    take_profit: float
    opened_utc: str
    reasons: list[str]
    status: str = "open"          # open | tp | stop
    exit_price: float | None = None
    closed_utc: str | None = None


def _load_book() -> list[dict]:
    if BOOK_PATH.exists():
        return json.loads(BOOK_PATH.read_text())
    return []


def _save_book(book: list[dict]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    BOOK_PATH.write_text(json.dumps(book, indent=2))


def _append_jsonl(path: Path, obj: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj) + "\n")


def _update_open_trades(book: list[dict], frames: dict, cfg: EngineConfig) -> None:
    """Check open paper trades against closed bars after entry; close on stop/tp."""
    pf = cfg.primary_frame
    m30 = frames[pf]
    for t in book:
        if t["status"] != "open":
            continue
        opened = pd.Timestamp(t["signal_time"])
        after = m30.loc[m30.index > opened]
        for ts, bar in after.iterrows():
            if t["direction"] == "LONG":
                hit_stop, hit_tp = bar["low"] <= t["stop"], bar["high"] >= t["take_profit"]
            else:
                hit_stop, hit_tp = bar["high"] >= t["stop"], bar["low"] <= t["take_profit"]
            if hit_stop:          # conservative: stop wins ambiguous bars
                t.update(status="stop", exit_price=t["stop"],
                         closed_utc=datetime.now(timezone.utc).isoformat())
                _append_jsonl(TRADE_LOG, t)
                break
            if hit_tp:
                t.update(status="tp", exit_price=t["take_profit"],
                         closed_utc=datetime.now(timezone.utc).isoformat())
                _append_jsonl(TRADE_LOG, t)
                break


def run_once(cfg: EngineConfig = DEFAULT_CONFIG) -> dict:
    """One paper cycle: fetch, manage open trades, evaluate, log, maybe open."""
    frames = fetch_frames(cfg)
    sig, meta = evaluate_latest(cfg, frames)
    book = _load_book()
    _update_open_trades(book, frames, cfg)

    record = {**meta, "signal": sig.to_dict()}
    _append_jsonl(SIGNAL_LOG, record)

    has_open = any(t["status"] == "open" for t in book)
    already = any(t["signal_time"] == meta["primary_bar_time"] for t in book)
    if sig.is_trade() and not has_open and not already:
        pt = PaperTrade(
            direction=sig.direction, signal_time=meta["primary_bar_time"],
            entry=sig.entry, stop=sig.stop, take_profit=sig.take_profit,
            opened_utc=meta["evaluated_utc"], reasons=sig.reasons,
        )
        book.append(asdict(pt))
        record["paper_action"] = "opened"
    _save_book(book)
    _notify(record)

    open_n = sum(1 for t in book if t["status"] == "open")
    action = record.get("paper_action", "")
    print(f"[{meta['primary_bar_time']}] {sig.direction:5s}  "
          f"conf={sig.confidence:.2f}  price={meta['last_price']:.2f}  "
          f"reasons={sig.reasons}  open={open_n}"
          + (f"  → {action}" if action else ""))
    return record


def watch(interval_seconds: int = 1800, cfg: EngineConfig = DEFAULT_CONFIG,
          max_cycles: int | None = None) -> None:
    print(f"Paper-watch started ({cfg.symbol}, every {interval_seconds}s). "
          "WATCH MODE ONLY — no orders are placed.")
    cycles = 0
    while True:
        try:
            run_once(cfg)
        except Exception as exc:  # keep the watcher alive across transient errors
            print(f"cycle error: {exc}")
        cycles += 1
        if max_cycles and cycles >= max_cycles:
            break
        time.sleep(interval_seconds)


def main() -> None:
    ap = argparse.ArgumentParser(description="XAU/USD paper-watch (no live orders)")
    ap.add_argument("--once", action="store_true", help="evaluate latest closed bar once")
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="re-evaluate on an interval (e.g. 1800 for M30)")
    args = ap.parse_args()
    if args.watch:
        watch(args.watch)
    else:
        run_once()


if __name__ == "__main__":
    main()
