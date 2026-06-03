#!/usr/bin/env python3
"""Backfill 2+ years of XAU/USD history from Dukascopy in 6-month chunks.

Why chunked: 2.4 years of ticks is ~200M rows. Loading them all at once needs
~8 GB RAM. Each 6-month window is ~2 GB, well inside available memory.

The script:
1. Downloads ticks for each 6-month window.
2. Immediately resamples to clean OHLCV (M15/M30/H1/H4/D1).
3. Keeps only the OHLCV frames in memory; ticks are discarded after each chunk.
4. At the end, merges all chunks + the existing data and writes final parquets.

Usage:
    python scripts/backfill_history.py --start 2024-01-01 --threads 16
    python scripts/backfill_history.py --start 2023-01-01 --threads 16   # 3 years
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.providers import dukascopy as dk
from src.data import ohlcv_from_ticks, save_parquet, load_parquet

TF_LABEL = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}
SYMBOL = "XAUUSD"


def _date_chunks(start: pd.Timestamp, end: pd.Timestamp,
                 months: int = 6) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    chunks = []
    cur = start
    while cur < end:
        nxt = (cur + pd.DateOffset(months=months)).floor("D")
        chunks.append((cur, min(nxt, end)))
        cur = nxt
    return chunks


def fetch_chunk(start: pd.Timestamp, end: pd.Timestamp,
                threads: int) -> dict[str, pd.DataFrame] | None:
    """Download one date window and return {tf: ohlcv_frame}. Returns None on failure."""
    print(f"  [{start:%Y-%m-%d} → {end:%Y-%m-%d}] fetching ticks …", flush=True)
    ticks = dk.fetch_ticks(
        SYMBOL,
        start.to_pydatetime(),
        end.to_pydatetime(),
        threads=threads,
    )
    if ticks.empty:
        print(f"  [{start:%Y-%m-%d}] no ticks (weekend-only range?) — skipping")
        return None
    print(f"  [{start:%Y-%m-%d}] {len(ticks):,} ticks  resampling …", flush=True)
    return {
        tf: ohlcv_from_ticks(ticks, tf, price_col="mid", volume_col="volume")
        for tf in TF_LABEL
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01",
                    help="history start date (UTC, YYYY-MM-DD)")
    ap.add_argument("--end", default=None,
                    help="end date (default: today UTC)")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--chunk-months", type=int, default=6,
                    help="window size per download batch (default 6 months)")
    args = ap.parse_args()

    clean_dir = Path("data/clean/dukascopy")
    clean_dir.mkdir(parents=True, exist_ok=True)

    start = pd.Timestamp(args.start, tz="UTC")
    end = (pd.Timestamp(args.end, tz="UTC") if args.end
           else pd.Timestamp.now(tz="UTC").floor("D"))
    chunks = _date_chunks(start, end, months=args.chunk_months)

    print(f"Fetching {SYMBOL}  {start:%Y-%m-%d} → {end:%Y-%m-%d}")
    print(f"  {len(chunks)} chunk(s) of ≤{args.chunk_months} months each  "
          f"({args.threads} threads/chunk)\n")

    # Collect per-TF OHLCV frames across all chunks
    all_frames: dict[str, list[pd.DataFrame]] = {tf: [] for tf in TF_LABEL}
    failed: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    for i, (cs, ce) in enumerate(chunks, 1):
        print(f"Chunk {i}/{len(chunks)}", flush=True)
        result = fetch_chunk(cs, ce, args.threads)
        if result is None:
            failed.append((cs, ce))
            continue
        for tf, frame in result.items():
            all_frames[tf].append(frame)
        print(f"  done  {i}/{len(chunks)} chunks complete\n", flush=True)

    if failed:
        print(f"WARNING: {len(failed)} chunk(s) returned no ticks:")
        for s, e in failed:
            print(f"  {s:%Y-%m-%d} → {e:%Y-%m-%d}")
        print()

    print("Merging new history with any existing clean files …")
    for tf, label in TF_LABEL.items():
        parts = list(all_frames[tf])        # new historical chunks
        existing_path = clean_dir / f"{SYMBOL}_{label}.parquet"
        if existing_path.exists():
            try:
                parts.append(load_parquet(existing_path))
            except Exception as e:
                print(f"  WARNING: could not load existing {existing_path}: {e}")

        if not parts:
            print(f"  {tf}: no data — skipping")
            continue

        merged = (pd.concat(parts)
                    .sort_index()
                    .loc[~pd.concat(parts).sort_index().index.duplicated(keep="last")])
        save_parquet(merged, existing_path)
        span = f"{merged.index[0]:%Y-%m-%d} → {merged.index[-1]:%Y-%m-%d}"
        print(f"  {tf:4s}  {len(merged):>7,} bars  {span}")

    print("\nDone. Run the backtest:")
    print("  python3 scripts/run_backtest.py --from-clean data/clean/dukascopy")


if __name__ == "__main__":
    main()
