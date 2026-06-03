"""Data loading, normalisation, resampling and no-look-ahead slicing.

Conventions (enforced everywhere downstream):
- Every frame is a ``pandas.DataFrame`` indexed by the candle **open time**,
  tz-aware in **UTC**, sorted ascending, with columns
  ``[open, high, low, close, volume]`` (volume may be NaN).
- A bar opened at ``T`` on a timeframe of ``D`` minutes **closes** at ``T + D``.
- Live/backtest code must only ever read bars that have closed at or before the
  decision time (CLAUDE.md hard rule #1). ``visible_frame`` is the single
  chokepoint that guarantees this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import TIMEFRAME_MINUTES

OHLCV_COLS = ["open", "high", "low", "close", "volume"]

# common column aliases seen in broker / provider CSVs
_COL_ALIASES = {
    "datetime": "timestamp", "date": "timestamp", "time": "timestamp",
    "o": "open", "h": "high", "l": "low", "c": "close",
    "vol": "volume", "tickvol": "volume", "tick_volume": "volume",
}


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"Unknown timeframe {timeframe!r}")
    return pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


def normalize_ohlcv(df: pd.DataFrame, *, timestamp_col: str | None = None,
                    assume_tz: str = "UTC") -> pd.DataFrame:
    """Coerce an arbitrary OHLCV table into the canonical frame.

    - renames common aliases (o/h/l/c/vol, datetime/date/time)
    - parses the timestamp column to a UTC tz-aware index
    - validates schema, drops duplicate timestamps, sorts ascending
    - validates high>=low and that high/low bracket open/close
    """
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    # if the caller named a timestamp column, normalise its name too
    if timestamp_col is not None:
        timestamp_col = timestamp_col.strip().lower()
        timestamp_col = _COL_ALIASES.get(timestamp_col, timestamp_col)
    df = df.rename(columns={k: v for k, v in _COL_ALIASES.items() if k in df.columns})

    if timestamp_col is not None and timestamp_col not in df.columns:
        # alias already renamed it (e.g. "datetime" -> "timestamp")
        timestamp_col = "timestamp" if "timestamp" in df.columns else timestamp_col

    if timestamp_col is None:
        if "timestamp" in df.columns:
            timestamp_col = "timestamp"
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={df.index.name or "index": "timestamp"})
            df.columns = [str(c).strip().lower() for c in df.columns]
            timestamp_col = "timestamp"
        else:
            raise ValueError("No timestamp column/index found")

    ts = pd.to_datetime(df[timestamp_col], utc=False, errors="coerce")
    if ts.isna().any():
        raise ValueError("Unparseable timestamps present")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(assume_tz)
    ts = ts.dt.tz_convert("UTC")

    missing = [c for c in ["open", "high", "low", "close"] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    if "volume" not in df.columns:
        df["volume"] = np.nan

    out = pd.DataFrame({
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    })
    out.index = pd.DatetimeIndex(ts, name="timestamp")  # already UTC tz-aware

    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["open", "high", "low", "close"])

    bad = (out["high"] < out["low"]) | \
          (out["high"] < out[["open", "close"]].max(axis=1) - 1e-9) | \
          (out["low"] > out[["open", "close"]].min(axis=1) + 1e-9)
    if bad.any():
        raise ValueError(f"{int(bad.sum())} bars violate OHLC ordering")

    return out[OHLCV_COLS]


def load_csv(path: str | Path, **kwargs) -> pd.DataFrame:
    """Load a raw broker/provider CSV into the canonical frame."""
    raw = pd.read_csv(path)
    return normalize_ohlcv(raw, **kwargs)


def load_parquet(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df = normalize_ohlcv(df)
    else:
        df.index = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    return df[OHLCV_COLS].sort_index()


def save_parquet(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample a finer canonical frame up to ``timeframe``.

    Uses left-labelled, left-closed bins so the index stays the candle **open**
    time. Empty bins (weekend gaps) are dropped — we never fabricate bars.
    """
    intraday = TIMEFRAME_MINUTES[timeframe] < 1440
    rule = f"{TIMEFRAME_MINUTES[timeframe]}min" if intraday else "1D"
    # `origin="epoch"` only affects tick-like (intraday) frequencies
    kw = {"label": "left", "closed": "left"}
    if intraday:
        kw["origin"] = "epoch"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule, **kw).agg(agg)
    out = out.dropna(subset=["open", "high", "low", "close"])
    # `volume.sum()` turns all-NaN bins into 0; restore NaN so the proxy can fire
    vol_counts = df["volume"].resample(rule, **kw).count()
    out.loc[vol_counts.reindex(out.index).fillna(0) == 0, "volume"] = np.nan
    if (df["volume"].isna().all()):
        out["volume"] = np.nan
    return out[OHLCV_COLS]


def ohlcv_from_ticks(ticks: pd.DataFrame, timeframe: str,
                     price_col: str = "mid", volume_col: str = "volume") -> pd.DataFrame:
    """Aggregate a tick frame into a canonical OHLCV frame at ``timeframe``.

    ``ticks`` must be indexed by a UTC tz-aware timestamp and contain a price
    column (default ``mid``) and an optional volume column. Empty bins are
    dropped (no fabricated bars). Tick count is summed into ``volume`` when no
    explicit volume is supplied — that is genuine participation data.
    """
    if ticks.empty:
        return pd.DataFrame(columns=OHLCV_COLS)
    idx = ticks.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    px = pd.to_numeric(ticks[price_col], errors="coerce")
    df = pd.DataFrame({"price": px.to_numpy()}, index=idx).dropna()
    if volume_col in ticks.columns:
        df["volume"] = pd.to_numeric(ticks[volume_col], errors="coerce").to_numpy()[: len(df)]
    else:
        df["volume"] = 1.0  # one unit per tick -> tick volume

    intraday = TIMEFRAME_MINUTES[timeframe] < 1440
    rule = f"{TIMEFRAME_MINUTES[timeframe]}min" if intraday else "1D"
    kw = {"label": "left", "closed": "left"}
    if intraday:
        kw["origin"] = "epoch"
    res = df["price"].resample(rule, **kw)
    out = pd.DataFrame({
        "open": res.first(), "high": res.max(),
        "low": res.min(), "close": res.last(),
        "volume": df["volume"].resample(rule, **kw).sum(),
    }).dropna(subset=["open", "high", "low", "close"])
    out.index.name = "timestamp"
    return out[OHLCV_COLS]


def build_frames(base: pd.DataFrame, timeframes: Iterable[str],
                 base_timeframe: str = "M1") -> dict[str, pd.DataFrame]:
    """Build a frame dict from one base frame by resampling upward.

    If a requested timeframe equals ``base_timeframe`` the base is returned as-is.
    """
    base = base.sort_index()
    frames: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        if tf == base_timeframe:
            frames[tf] = base.copy()
        elif TIMEFRAME_MINUTES[tf] < TIMEFRAME_MINUTES[base_timeframe]:
            raise ValueError(f"Cannot build {tf} from coarser base {base_timeframe}")
        else:
            frames[tf] = resample(base, tf)
    return frames


def visible_frame(frame: pd.DataFrame, asof_close: pd.Timestamp,
                  timeframe: str) -> pd.DataFrame:
    """Return only the bars of ``frame`` that have **closed** at/before ``asof_close``.

    This is the no-look-ahead chokepoint. A bar opened at ``T`` closes at
    ``T + duration``; it is visible iff ``T + duration <= asof_close``.
    """
    if frame.empty:
        return frame
    dur = timeframe_delta(timeframe)
    close_times = frame.index + dur
    return frame.loc[close_times <= asof_close]


def closed_bars_only(frame: pd.DataFrame, timeframe: str,
                     now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop the still-forming last bar(s): keep only bars closed by ``now``."""
    now = now or pd.Timestamp.now(tz="UTC")
    return visible_frame(frame, now, timeframe)
