# XAU/USD Signal Dashboard

Research/paper-trading dashboard for closed-candle XAU/USD signals.

## Run

```bash
npm install
node server.js
```

Open:

```text
http://127.0.0.1:4173
```

Without credentials, the server uses a simulated live fallback so the chart and
paper-signal workflow can be tested immediately.

## Real Live Data

Set a Twelve Data key in `.env` or export it before starting the server:

```bash
export TWELVE_DATA_API_KEY="your_key"
export XAUUSD_SYMBOL="XAU/USD"
node server.js
```

The dashboard calls the local `/api/market` proxy, not Twelve Data directly from
the browser. This keeps credentials out of frontend code.

The server fetches both candles and the current `/price` value. If Twelve Data's
candle history lags behind the live price feed, the UI shows a warning and a
live price line instead of fabricating missing candles. Signals still evaluate
only the latest closed candle.

## Chart

The main chart uses TradingView Lightweight Charts, giving the dashboard a
Binance-style candlestick view with pan/zoom, crosshair, right-side price scale,
volume bars, support/resistance lines, and signal markers.

## Timeframes

The dashboard supports:

- `2m`
- `5m`
- `15m`
- `1D`

Use the buttons above the chart. Mouse wheel zooms the candle window; click-drag
pans left/right. Signals are paper/watch outputs only and no live orders are
placed.

## Refresh Cadence

The free Twelve Data quota is limited, so the dashboard avoids 1-minute polling.
The `2m` chart is built by requesting Twelve Data's supported `1min` candles and
resampling them locally into 2-minute candles. It refreshes every 2 minutes:

```text
2m chart   -> 720 requests/day
5m chart   -> 288 requests/day
15m chart  -> 96 requests/day
1D chart   -> 24 requests/day
```
