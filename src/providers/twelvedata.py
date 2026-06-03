"""Twelve Data adapter (PLAN.md provider option #3).

Reference/prototype feed for XAU/USD — useful for the dashboard and a non-broker
paper-watch mode. It is **not** an execution-truth feed: aggregate composite
prices may not match a broker's fillable bid/ask. Backtests must model spread
explicitly (see ``EngineConfig.spread_price``).

Uses only the Python stdlib (urllib) so it has no extra runtime dependency.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from ..data import normalize_ohlcv, save_parquet

API_ROOT = "https://api.twelvedata.com"

# Twelve Data's natively supported intervals (we resample others locally).
SUPPORTED_INTERVALS = {"1min", "5min", "15min", "30min", "45min",
                       "1h", "2h", "4h", "1day", "1week", "1month"}


class TwelveDataError(RuntimeError):
    pass


def _load_env(env_path: str | Path = ".env") -> None:
    p = Path(env_path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def get_api_key() -> str:
    _load_env()
    key = os.environ.get("TWELVE_DATA_API_KEY") or os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise TwelveDataError(
            "No TWELVE_DATA_API_KEY in environment or .env. "
            "Set it before fetching live data.")
    return key


def _get_json(endpoint: str, params: dict, timeout: float = 15.0) -> dict:
    url = f"{API_ROOT}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "xauusd-engine/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if isinstance(body, dict) and (body.get("status") == "error" or body.get("code")):
        raise TwelveDataError(body.get("message", "Twelve Data error"))
    return body


def time_series(symbol: str = "XAU/USD", interval: str = "1min",
                outputsize: int = 5000, api_key: str | None = None) -> pd.DataFrame:
    """Fetch a canonical OHLCV frame from Twelve Data ``/time_series``."""
    if interval not in SUPPORTED_INTERVALS:
        raise TwelveDataError(f"Interval {interval!r} not natively supported; "
                              "fetch a finer one and resample locally.")
    body = _get_json("time_series", {
        "symbol": symbol, "interval": interval,
        "outputsize": min(int(outputsize), 5000),
        "timezone": "UTC", "order": "ASC",
        "apikey": api_key or get_api_key(),
    })
    values = body.get("values") or []
    if not values:
        raise TwelveDataError("No candles returned")
    df = pd.DataFrame(values)
    return normalize_ohlcv(df, timestamp_col="datetime", assume_tz="UTC")


def latest_price(symbol: str = "XAU/USD", api_key: str | None = None) -> float | None:
    try:
        body = _get_json("price", {"symbol": symbol, "apikey": api_key or get_api_key()})
        price = float(body.get("price"))
        return price
    except (TwelveDataError, TypeError, ValueError):
        return None


def backfill(symbol: str = "XAU/USD", interval: str = "1min",
             outputsize: int = 5000, raw_dir: str | Path = "data/raw/twelvedata",
             clean_dir: str | Path = "data/clean/twelvedata") -> Path:
    """Fetch history and persist raw CSV + clean parquet (PLAN.md storage flow)."""
    df = time_series(symbol, interval, outputsize)
    raw_dir, clean_dir = Path(raw_dir), Path(clean_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("/", "") + "_" + interval
    csv_path = raw_dir / f"{safe}.csv"
    df.to_csv(csv_path)
    save_parquet(df, clean_dir / f"{safe}.parquet")
    return csv_path
