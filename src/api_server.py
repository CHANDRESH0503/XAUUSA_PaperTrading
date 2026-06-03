#!/usr/bin/env python3
"""Python signal API server for the XAU/USD dashboard.

Binds to 127.0.0.1:4174 (localhost only — never expose to the internet).
Spawned by server.js on startup. Provides the real engine signal and live
Dukascopy candles to the Node.js frontend.

Endpoints:
  GET /health              → {"ok": true, "bars": {...}}
  GET /signal              → SignalState JSON for the latest closed M30 bar
  GET /candles?tf=M30&n=240 → recent OHLCV bars (parquet + fresh ticks)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── path setup: must happen before any project imports ───────────────────────
# When invoked as `python3 src/api_server.py` Python adds src/ to sys.path[0],
# which shadows stdlib modules (e.g. `signal`, `struct`). Remove src/ and add
# the project root so relative package imports work correctly.
_SRC_DIR = str(Path(__file__).resolve().parent)
_ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import pandas as pd

from src.config import DEFAULT_CONFIG as CFG, TIMEFRAME_MINUTES
from src.data import load_parquet, ohlcv_from_ticks, closed_bars_only
from src.providers import dukascopy as dk
from src.signal import evaluate

# ── constants ────────────────────────────────────────────────────────────────
PORT = 4175
HOST = "127.0.0.1"
CLEAN_DIR = Path("data/clean/dukascopy")
SYMBOL = "XAUUSD"
TF_LABEL = {"M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1day"}
# How many hours of fresh ticks to fetch per refresh cycle
LIVE_LOOKBACK_HOURS = 6
# Refresh the signal cache every N seconds (aligns with M30 close)
REFRESH_SECONDS = 60


# ── shared state (protected by _lock) ────────────────────────────────────────
_lock = threading.Lock()
_frames: dict[str, pd.DataFrame] = {}
_signal_cache: dict | None = None
_last_refresh: float = 0.0
_last_price: float | None = None


def _load_base_frames() -> dict[str, pd.DataFrame]:
    """Load clean parquet files into memory."""
    out = {}
    for tf, label in TF_LABEL.items():
        p = CLEAN_DIR / f"{SYMBOL}_{label}.parquet"
        if p.exists():
            out[tf] = load_parquet(p)
    return out


def _extend_with_live(base: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Fetch recent Dukascopy ticks and append to each timeframe.

    Lookback is dynamic: covers at least LIVE_LOOKBACK_HOURS but also bridges
    any gap between the parquet's last bar and now (e.g. overnight when the
    parquet was last updated yesterday afternoon).
    """
    end = datetime.now(timezone.utc)

    # How many hours since the newest bar in any base frame?
    latest_bar: datetime | None = None
    for df in base.values():
        if not df.empty:
            t = df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
            if latest_bar is None or t > latest_bar:
                latest_bar = t

    if latest_bar is not None:
        gap_hours = (end - latest_bar).total_seconds() / 3600
        lookback = max(LIVE_LOOKBACK_HOURS, int(gap_hours) + 2)
    else:
        lookback = LIVE_LOOKBACK_HOURS

    start = datetime.fromtimestamp(end.timestamp() - lookback * 3600, tz=timezone.utc)
    try:
        print(f"[api_server] fetching {lookback:.0f}h of live ticks …", flush=True)
        ticks = dk.fetch_ticks(SYMBOL, start, end, threads=8)
        if ticks.empty:
            return base
        for tf in ("M15", "M30", "H1"):
            if tf not in base:
                continue
            fresh = ohlcv_from_ticks(ticks, tf, price_col="mid", volume_col="volume")
            if fresh.empty:
                continue
            combined = pd.concat([base[tf], fresh])
            base[tf] = (combined
                        .loc[~combined.index.duplicated(keep="last")]
                        .sort_index())
        global _last_price
        _last_price = round(float(ticks["mid"].iloc[-1]), 2)
    except Exception as exc:
        print(f"[api_server] live tick fetch failed: {exc}", flush=True)
    return base


def _build_signal(frames: dict[str, pd.DataFrame]) -> dict | None:
    """Run the engine on the last closed M30 bar; return a JSON-serialisable dict."""
    now = pd.Timestamp.now(tz="UTC")
    closed: dict[str, pd.DataFrame] = {}
    for tf, df in frames.items():
        c = closed_bars_only(df, tf, now)
        if len(c) > 0:
            closed[tf] = c

    pf = CFG.primary_frame
    if pf not in closed or len(closed[pf]) < 2:
        return None

    idx = len(closed[pf]) - 1
    result = evaluate(idx, closed, CFG, return_snapshot=True)
    sig, snap = result if isinstance(result, tuple) else (result, None)
    return {
        "direction": sig.direction,
        "entry": sig.entry,
        "stop": sig.stop,
        "take_profit": sig.take_profit,
        "tp": sig.take_profit,          # alias for frontend compat
        "confidence": round(sig.confidence, 4),
        "reasons": sig.reasons,
        "bar_time": sig.bar_time.isoformat(),
        "bar_time_ms": int(sig.bar_time.timestamp() * 1000),
        "session": CFG.session_of(int(sig.bar_time.hour)),
        "htf_bias": snap.htf_bias if snap else None,
        "d1_bias": snap.d1_bias if snap else None,
        "zone": snap.zone if snap else None,
        "last_price": _last_price,
        "evaluated_utc": datetime.now(timezone.utc).isoformat(),
        "bars": {tf: len(df) for tf, df in closed.items()},
    }


def _refresh() -> None:
    """Reload frames + live ticks + recompute signal. Runs in background thread."""
    global _frames, _signal_cache, _last_refresh
    print("[api_server] refreshing …", flush=True)
    base = _load_base_frames()
    extended = _extend_with_live(base)
    sig = _build_signal(extended)
    with _lock:
        _frames = extended
        _signal_cache = sig
        _last_refresh = time.monotonic()
    print(f"[api_server] ready — signal: {sig['direction'] if sig else 'n/a'}", flush=True)


def _background_loop() -> None:
    """Periodically refresh so the cache stays warm."""
    while True:
        time.sleep(REFRESH_SECONDS)
        try:
            _refresh()
        except Exception as exc:
            print(f"[api_server] background refresh error: {exc}", flush=True)


def _get_candles(tf: str, n: int) -> list[dict] | None:
    with _lock:
        df = _frames.get(tf)
    if df is None or df.empty:
        return None
    now = pd.Timestamp.now(tz="UTC")
    closed = closed_bars_only(df, tf, now)
    tail = closed.tail(n)
    out = []
    for ts, row in tail.iterrows():
        out.append({
            "t": int(ts.timestamp() * 1000),
            "o": round(float(row["open"]), 2),
            "h": round(float(row["high"]), 2),
            "l": round(float(row["low"]), 2),
            "c": round(float(row["close"]), 2),
            "vol": float(row["volume"]) if pd.notna(row["volume"]) else 0,
            "volumeSource": "provider",
            "closed": True,
        })
    # Append the live (open) bar using last price
    if _last_price is not None and out:
        ms = TIMEFRAME_MINUTES.get(tf, 30) * 60 * 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bucket_ms = (now_ms // ms) * ms
        last_closed_t = out[-1]["t"]
        if bucket_ms > last_closed_t:
            prev_close = out[-1]["c"]
            out.append({
                "t": bucket_ms,
                "o": prev_close,
                "h": max(prev_close, _last_price),
                "l": min(prev_close, _last_price),
                "c": _last_price,
                "vol": 0,
                "volumeSource": "live",
                "closed": False,
            })
    return out


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress per-request logs; errors still go to stderr

    def _send(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/health":
            with _lock:
                bars = {tf: len(df) for tf, df in _frames.items()}
            self._send(200, {"ok": True, "bars": bars,
                             "signal": _signal_cache["direction"] if _signal_cache else None})

        elif parsed.path == "/signal":
            with _lock:
                sig = _signal_cache
            if sig is None:
                self._send(503, {"error": "signal not ready yet"})
            else:
                self._send(200, sig)

        elif parsed.path == "/candles":
            tf = qs.get("tf", ["M30"])[0].upper()
            n = int(qs.get("n", ["240"])[0])
            if tf not in TF_LABEL:
                self._send(400, {"error": f"unknown timeframe {tf}"})
                return
            candles = _get_candles(tf, n)
            if candles is None:
                self._send(503, {"error": f"no data for {tf}"})
            else:
                self._send(200, {"tf": tf, "candles": candles,
                                 "live": _last_price, "symbol": "XAU/USD"})
        else:
            self._send(404, {"error": "not found"})


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[api_server] loading clean parquet files …", flush=True)
    _refresh()                              # initial blocking load
    t = threading.Thread(target=_background_loop, daemon=True)
    t.start()
    server = HTTPServer((HOST, PORT), Handler)
    print(f"[api_server] listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
