#!/usr/bin/env python3
"""Backfill XAU/USD history from Twelve Data into data/raw + data/clean.

Usage:
    python scripts/fetch_twelvedata.py                 # 1min, 5000 bars
    python scripts/fetch_twelvedata.py --interval 30min --outputsize 5000
    python scripts/fetch_twelvedata.py --all           # M15/M30/H1/H4/D1

Free-tier note: outputsize caps at 5000 and the daily request quota is limited.
1min x 5000 is only ~3.5 days; fetch each higher timeframe directly for depth.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers import twelvedata as td  # noqa: E402

ALL_INTERVALS = ["15min", "30min", "1h", "4h", "1day"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU/USD")
    ap.add_argument("--interval", default="1min")
    ap.add_argument("--outputsize", type=int, default=5000)
    ap.add_argument("--all", action="store_true",
                    help="fetch M15/M30/H1/H4/D1 instead of a single interval")
    args = ap.parse_args()

    intervals = ALL_INTERVALS if args.all else [args.interval]
    for interval in intervals:
        try:
            path = td.backfill(args.symbol, interval, args.outputsize)
            print(f"saved {args.symbol} {interval} -> {path}")
        except Exception as exc:
            print(f"FAILED {interval}: {exc}")


if __name__ == "__main__":
    main()
