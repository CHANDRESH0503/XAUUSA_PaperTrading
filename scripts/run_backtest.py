#!/usr/bin/env python3
"""Run a walk-forward backtest and write a report to reports/.

Loads clean parquet frames (preferred) or resamples from a base CSV/parquet.

Usage:
    # from a single fine-grained base file (e.g. 1min) resampled into all TFs:
    python scripts/run_backtest.py --base data/clean/twelvedata/XAUUSD_1min.parquet

    # from separate per-timeframe clean files:
    python scripts/run_backtest.py --from-clean data/clean/twelvedata
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DEFAULT_CONFIG  # noqa: E402
from src.data import load_parquet, load_csv, build_frames  # noqa: E402
from src.backtest import full_report  # noqa: E402

TF_FILE = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}


def _load_one(path: Path):
    return load_parquet(path) if path.suffix == ".parquet" else load_csv(path)


def frames_from_base(base_path: Path) -> dict:
    base = _load_one(base_path)
    needed = [DEFAULT_CONFIG.primary_frame, *DEFAULT_CONFIG.htf_frames, *DEFAULT_CONFIG.ltf_frames]
    # detect base timeframe label from filename, else assume M1
    base_tf = "M1"
    return build_frames(base, needed, base_timeframe=base_tf)


def frames_from_clean(clean_dir: Path, symbol_prefix: str = "XAUUSD") -> dict:
    frames = {}
    for tf, label in TF_FILE.items():
        for ext in (".parquet", ".csv"):
            p = clean_dir / f"{symbol_prefix}_{label}{ext}"
            if p.exists():
                frames[tf] = _load_one(p)
                break
    missing = [tf for tf in (DEFAULT_CONFIG.primary_frame, *DEFAULT_CONFIG.htf_frames)
               if tf not in frames]
    if missing:
        raise SystemExit(f"Missing clean files for timeframes: {missing}")
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, help="single fine-grained base file to resample")
    ap.add_argument("--from-clean", type=Path, help="dir of per-timeframe clean files")
    ap.add_argument("--symbol-prefix", default="XAUUSD")
    args = ap.parse_args()

    if args.base:
        frames = frames_from_base(args.base)
    elif args.from_clean:
        frames = frames_from_clean(args.from_clean, args.symbol_prefix)
    else:
        raise SystemExit("Provide --base or --from-clean")

    result = full_report(frames, DEFAULT_CONFIG, write_dir="reports")
    m = result.metrics
    print("Backtest complete. Report written to reports/latest.md")
    print(f"  trades={m.get('trade_count')} expectancy={m.get('expectancy_r')} R "
          f"max_dd={m.get('max_drawdown_r')} R")
    print(f"  flat_freq={m.get('flat_frequency', {}).get('flat_pct')}")


if __name__ == "__main__":
    main()
