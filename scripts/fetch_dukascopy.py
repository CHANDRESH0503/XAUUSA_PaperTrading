#!/usr/bin/env python3
"""Backfill XAU/USD history from Dukascopy (no API key, no rate quota).

Dukascopy serves one LZMA tick file per hour, so deep pulls take a while — run
large ranges in the background. Free, unlimited, and includes real tick volume.

Usage:
    # one timeframe over a date range
    python scripts/fetch_dukascopy.py --interval M30 --start 2024-01-01 --end 2026-06-01

    # all engine timeframes from one tick pull (efficient: ticks fetched once)
    python scripts/fetch_dukascopy.py --all --start 2024-06-01

Output: data/raw/dukascopy/XAUUSD_ticks.parquet (+ per-timeframe clean parquet).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.providers import dukascopy as dk  # noqa: E402
from src.data import ohlcv_from_ticks, save_parquet  # noqa: E402

TF_LABEL = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU/USD")
    ap.add_argument("--interval", default="M30")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--all", action="store_true",
                    help="aggregate one tick pull into M15/M30/H1/H4/D1")
    args = ap.parse_args()

    instrument = args.symbol.replace("/", "").upper()
    start_dt = pd.Timestamp(args.start, tz="UTC").to_pydatetime()
    end_dt = (pd.Timestamp(args.end, tz="UTC") if args.end
              else pd.Timestamp.now(tz="UTC")).to_pydatetime()

    print(f"Fetching {instrument} ticks {start_dt:%F} -> {end_dt:%F} "
          f"({args.threads} threads). This can take a while for long ranges...")
    ticks = dk.fetch_ticks(instrument, start_dt, end_dt, threads=args.threads)
    if ticks.empty:
        raise SystemExit("No ticks returned (check date range / weekend-only span).")

    raw_dir = Path("data/raw/dukascopy")
    clean_dir = Path("data/clean/dukascopy")
    save_parquet(ticks, raw_dir / f"{instrument}_ticks.parquet")
    print(f"  ticks: {len(ticks):,}  {ticks.index[0]} -> {ticks.index[-1]}")

    intervals = list(TF_LABEL) if args.all else [args.interval]
    for tf in intervals:
        ohlcv = ohlcv_from_ticks(ticks, tf, price_col="mid", volume_col="volume")
        out = clean_dir / f"{instrument}_{TF_LABEL[tf]}.parquet"
        save_parquet(ohlcv, out)
        print(f"  {tf:4} -> {out}  ({len(ohlcv):,} bars)")


if __name__ == "__main__":
    main()
