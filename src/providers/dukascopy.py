"""Dukascopy historical-data adapter (PLAN.md provider option #4).

Why this exists: Twelve Data's free tier caps each call at 5000 bars and has a
daily request quota. Dukascopy publishes free historical **tick** data with
**no API key and no rate quota** — one LZMA-compressed ``.bi5`` file per hour,
per instrument. We decode it with the stdlib (urllib + lzma + struct), so there
is no extra dependency.

Bonus over Twelve Data: Dukascopy ticks carry real bid/ask volume, so the
volume gate (LOGIC.md §8.1) runs on genuine participation instead of the
range proxy.

Caveats (be honest, CLAUDE.md):
- This is a Dukascopy (Swiss broker) feed, not *your* execution broker. Treat it
  as a research / robustness source; spreads and fills will differ from yours.
- Deep history = many hourly files (≈24 per day). Pulling years is slow; use the
  threaded downloader and run large backfills in the background.

bi5 tick record (after LZMA decompress), big-endian, 20 bytes each:
    >IIIff = (ms_since_hour, ask_points, bid_points, ask_vol, bid_vol)
Prices are integers in instrument points; divide by ``point`` (10**digits).
"""

from __future__ import annotations

import lzma
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..data import ohlcv_from_ticks, save_parquet

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
_RECORD = struct.Struct(">IIIff")  # time(ms), ask, bid, askvol, bidvol

# decimal digits per instrument -> point divisor 10**digits.
# XAUUSD is quoted to 3 decimals on Dukascopy, so point = 1000.
INSTRUMENT_DIGITS = {"XAUUSD": 3, "EURUSD": 5, "GBPUSD": 5, "USDJPY": 3,
                     "XAGUSD": 3, "BTCUSD": 1}
DEFAULT_DIGITS = 3

_HEADERS = {"User-Agent": "Mozilla/5.0 (research) xauusd-engine/0.1"}


class DukascopyError(RuntimeError):
    pass


def _point(instrument: str) -> float:
    return 10.0 ** INSTRUMENT_DIGITS.get(instrument.upper(), DEFAULT_DIGITS)


def hour_url(instrument: str, dt: datetime) -> str:
    # NOTE: Dukascopy months are 0-indexed in the path.
    return (f"{BASE_URL}/{instrument.upper()}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _download(url: str, timeout: float = 45.0, retries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""        # no ticks this hour (weekend/holiday/closed)
            last = e
        except Exception as e:    # transient network error -> backoff + retry
            last = e
        time.sleep(min(2.0 * (attempt + 1), 8.0))
    raise DukascopyError(f"download failed: {url} ({last})")


def _decompress(raw: bytes) -> bytes:
    if not raw:
        return b""
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            return lzma.decompress(raw, format=fmt)
        except lzma.LZMAError:
            continue
    raise DukascopyError("could not LZMA-decompress bi5 payload")


def decode_hour(raw: bytes, hour_start: datetime, point: float) -> pd.DataFrame:
    """Decode one hour's raw .bi5 payload into a tick frame (bid/ask/mid/volume)."""
    data = _decompress(raw)
    n = len(data) // _RECORD.size
    if n == 0:
        return pd.DataFrame(columns=["bid", "ask", "mid", "volume"])
    times, asks, bids, mids, vols = [], [], [], [], []
    base_ms = int(hour_start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(n):
        ms, ask_i, bid_i, ask_v, bid_v = _RECORD.unpack_from(data, i * _RECORD.size)
        ask = ask_i / point
        bid = bid_i / point
        times.append(base_ms + ms)
        asks.append(ask)
        bids.append(bid)
        mids.append((ask + bid) / 2.0)
        vols.append(float(ask_v) + float(bid_v))
    idx = pd.to_datetime(np.array(times, dtype="int64"), unit="ms", utc=True)
    return pd.DataFrame({"bid": bids, "ask": asks, "mid": mids, "volume": vols},
                        index=idx)


def fetch_ticks(instrument: str, start: datetime, end: datetime,
                threads: int = 8) -> pd.DataFrame:
    """Download and decode all ticks in ``[start, end)`` (UTC)."""
    point = _point(instrument)
    hours = []
    cur = start.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    end = end.replace(tzinfo=timezone.utc)
    while cur < end:
        hours.append(cur)
        cur += timedelta(hours=1)

    def work(h: datetime) -> pd.DataFrame:
        return decode_hour(_download(hour_url(instrument, h)), h, point)

    parts: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {pool.submit(work, h): h for h in hours}
        for fut in as_completed(futures):
            df = fut.result()
            if not df.empty:
                parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["bid", "ask", "mid", "volume"])
    out = pd.concat(parts).sort_index()
    lo = pd.Timestamp(start)  # start/end are already tz-aware (UTC)
    hi = pd.Timestamp(end)
    if lo.tz is None:
        lo = lo.tz_localize("UTC")
    if hi.tz is None:
        hi = hi.tz_localize("UTC")
    return out.loc[(out.index >= lo) & (out.index < hi)]


def time_series(symbol: str = "XAUUSD", interval: str = "M30",
                start: str | datetime = "2024-01-01",
                end: str | datetime | None = None, threads: int = 8) -> pd.DataFrame:
    """Canonical OHLCV frame for ``[start, end)`` aggregated from ticks.

    ``interval`` is an engine timeframe label (M15/M30/H1/H4/D1). ``symbol``
    accepts ``XAU/USD`` or ``XAUUSD``.
    """
    instrument = symbol.replace("/", "").upper()
    start_dt = pd.Timestamp(start, tz="UTC").to_pydatetime()
    end_dt = (pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")).to_pydatetime()
    ticks = fetch_ticks(instrument, start_dt, end_dt, threads=threads)
    if ticks.empty:
        raise DukascopyError(f"no ticks for {instrument} in {start_dt:%F}..{end_dt:%F}")
    return ohlcv_from_ticks(ticks, interval, price_col="mid", volume_col="volume")


def latest_price(symbol: str = "XAUUSD") -> float | None:
    """Best-effort recent price (last decoded tick in the past ~2 hours)."""
    end = datetime.now(timezone.utc)
    try:
        ticks = fetch_ticks(symbol.replace("/", "").upper(),
                            end - timedelta(hours=2), end, threads=2)
        return float(ticks["mid"].iloc[-1]) if not ticks.empty else None
    except DukascopyError:
        return None


def backfill(symbol: str = "XAU/USD", interval: str = "M30",
             start: str = "2024-01-01", end: str | None = None,
             threads: int = 8, raw_dir: str | Path = "data/raw/dukascopy",
             clean_dir: str | Path = "data/clean/dukascopy") -> Path:
    """Fetch and persist clean OHLCV (and the raw ticks parquet)."""
    instrument = symbol.replace("/", "").upper()
    start_dt = pd.Timestamp(start, tz="UTC").to_pydatetime()
    end_dt = (pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.now(tz="UTC")).to_pydatetime()
    ticks = fetch_ticks(instrument, start_dt, end_dt, threads=threads)
    if ticks.empty:
        raise DukascopyError(f"no ticks for {instrument}")
    raw_dir, clean_dir = Path(raw_dir), Path(clean_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    save_parquet(ticks, raw_dir / f"{instrument}_ticks.parquet")
    ohlcv = ohlcv_from_ticks(ticks, interval, price_col="mid", volume_col="volume")
    label = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}.get(interval, interval)
    out = clean_dir / f"{instrument}_{label}.parquet"
    save_parquet(ohlcv, out)
    return out
