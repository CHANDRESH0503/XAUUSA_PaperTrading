const http = require("http");
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const { URL } = require("url");
const { spawn } = require("child_process");

const ROOT = __dirname;

loadEnvFile(path.join(ROOT, ".env"));

const PORT = Number(process.env.PORT || 4173);
const HOST = process.env.HOST || "127.0.0.1";
const SYMBOL = process.env.XAUUSD_SYMBOL || "XAU/USD";
const API_KEY = process.env.TWELVE_DATA_API_KEY || process.env.TWELVEDATA_API_KEY || "";

// ── Python signal engine bridge ───────────────────────────────────────────────
const PY_PORT = 4175;
let pyReady = false;

function startPythonEngine() {
  const py = spawn("python3", [path.join(ROOT, "src", "api_server.py")], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  py.stdout.on("data", (d) => {
    const msg = d.toString().trim();
    console.log("[engine]", msg);
    if (msg.includes("listening on")) pyReady = true;
  });
  py.stderr.on("data", (d) => process.stderr.write("[engine] " + d));
  py.on("exit", (code) => {
    console.warn(`[engine] exited (${code}); restarting in 5 s`);
    pyReady = false;
    setTimeout(startPythonEngine, 5000);
  });
  return py;
}

async function fetchPython(endpoint, timeoutMs = 3000) {
  if (!pyReady) return null;
  try {
    const res = await fetch(`http://127.0.0.1:${PY_PORT}${endpoint}`,
      { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Map frontend chart timeframes to closest Python engine timeframe
const CHART_TO_ENGINE_TF = { M2: "M15", M5: "M15", M15: "M15", D1: "D1" };

const TIMEFRAMES = {
  M2: { apiInterval: "1min", ms: 120_000, sourceMs: 60_000, count: 240, resampleFromApi: true },
  M5: { apiInterval: "5min", ms: 300_000, count: 240 },
  M15: { apiInterval: "15min", ms: 900_000, count: 220 },
  D1: { apiInterval: "1day", ms: 86_400_000, count: 180 },
};

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    if (!key || process.env[key] != null) continue;
    let value = trimmed.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

function sendJson(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(payload));
}

function normalizeTimeframe(value) {
  const tf = String(value || "M2").toUpperCase();
  return TIMEFRAMES[tf] ? tf : "M2";
}

function toCandle(row) {
  const rawVolume = Number(row.volume);
  const hasProviderVolume = Number.isFinite(rawVolume) && rawVolume > 0;
  return {
    t: Date.parse(row.datetime || row.timestamp || row.time),
    o: Number(row.open),
    h: Number(row.high),
    l: Number(row.low),
    c: Number(row.close),
    vol: hasProviderVolume ? rawVolume : null,
    volumeSource: hasProviderVolume ? "provider" : "missing",
  };
}

function markClosed(candles, tf, now = Date.now()) {
  const cfg = TIMEFRAMES[tf];
  return candles.map((c) => ({
    ...c,
    closed: c.t + cfg.ms <= now,
  }));
}

function resampleCandles(candles, targetMs) {
  const buckets = new Map();
  for (const candle of candles) {
    const bucketTime = Math.floor(candle.t / targetMs) * targetMs;
    const current = buckets.get(bucketTime);
    if (!current) {
      buckets.set(bucketTime, {
        t: bucketTime,
        o: candle.o,
        h: candle.h,
        l: candle.l,
        c: candle.c,
        vol: candle.volumeSource === "provider" ? candle.vol : null,
        volumeSource: candle.volumeSource === "provider" ? "provider" : "missing",
      });
      continue;
    }
    current.h = Math.max(current.h, candle.h);
    current.l = Math.min(current.l, candle.l);
    current.c = candle.c;
    if (current.volumeSource === "provider" && candle.volumeSource === "provider") {
      current.vol += candle.vol;
    } else if (candle.volumeSource === "provider") {
      current.vol = candle.vol;
      current.volumeSource = "provider";
    }
  }
  return [...buckets.values()].sort((a, b) => a.t - b.t);
}

function applyVolume(candles) {
  const ranges = candles
    .map((c) => Math.max(c.h - c.l, 0))
    .filter((range) => Number.isFinite(range) && range > 0);
  const avgRange = ranges.length
    ? ranges.reduce((sum, range) => sum + range, 0) / ranges.length
    : 1;

  return candles.map((c) => {
    if (Number.isFinite(c.vol) && c.vol > 0 && c.volumeSource === "provider") {
      return { ...c, vol: Math.round(c.vol), volumeSource: "provider" };
    }
    const range = Math.max(c.h - c.l, avgRange * 0.05, 0.01);
    const proxy = Math.max(1, Math.round((range / Math.max(avgRange, 0.01)) * 1000));
    return { ...c, vol: proxy, volumeSource: "range_proxy" };
  });
}

async function fetchJson(url, label) {
  const response = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!response.ok) {
    throw new Error(`${label} returned HTTP ${response.status}`);
  }
  const body = await response.json();
  if (body.status === "error" || body.code) {
    throw new Error(body.message || `${label} error`);
  }
  return body;
}

async function fetchTwelvePrice() {
  const url = new URL("https://api.twelvedata.com/price");
  url.searchParams.set("symbol", SYMBOL);
  url.searchParams.set("apikey", API_KEY);
  const body = await fetchJson(url, "Twelve Data price");
  const price = Number(body.price);
  return Number.isFinite(price) ? round(price) : null;
}

function applyLivePrice(candles, tf, livePrice, historyLagMs = 0) {
  if (!Number.isFinite(livePrice) || !candles.length) return candles;
  const cfg = TIMEFRAMES[tf];
  const maxUsableLag = Math.max(cfg.ms * 3, 5 * 60_000);
  if (historyLagMs > maxUsableLag) return candles;
  const nowBucket = Math.floor(Date.now() / cfg.ms) * cfg.ms;
  const out = candles.map((c) => ({ ...c }));
  let last = out[out.length - 1];

  if (last.t < nowBucket) {
    last = {
      t: nowBucket,
      o: out[out.length - 1].c,
      h: livePrice,
      l: livePrice,
      c: livePrice,
      vol: null,
      volumeSource: "missing",
    };
    out.push(last);
  } else if (last.t === nowBucket) {
    last.c = livePrice;
    last.h = Math.max(last.h, livePrice);
    last.l = Math.min(last.l, livePrice);
  }

  return out.slice(-cfg.count);
}

function formatDuration(ms) {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem ? `${hours}h ${rem}m` : `${hours}h`;
}

async function fetchTwelveData(tf) {
  if (!API_KEY) return null;

  const cfg = TIMEFRAMES[tf];
  const url = new URL("https://api.twelvedata.com/time_series");
  url.searchParams.set("symbol", SYMBOL);
  url.searchParams.set("interval", cfg.apiInterval);
  url.searchParams.set("outputsize", String(cfg.resampleFromApi ? cfg.count * 2 + 4 : cfg.count));
  url.searchParams.set("timezone", "UTC");
  url.searchParams.set("apikey", API_KEY);

  const [body, livePrice] = await Promise.all([
    fetchJson(url, "Twelve Data candles"),
    fetchTwelvePrice().catch(() => null),
  ]);

  const values = Array.isArray(body.values) ? body.values : [];
  let candles = values.map(toCandle).filter((c) =>
    Number.isFinite(c.t) && Number.isFinite(c.o) && Number.isFinite(c.h) &&
    Number.isFinite(c.l) && Number.isFinite(c.c)
  ).sort((a, b) => a.t - b.t);

  if (cfg.resampleFromApi) {
    candles = resampleCandles(candles, cfg.ms).slice(-cfg.count);
  }

  if (!candles.length) {
    throw new Error("No candles returned from Twelve Data");
  }

  const latestHistoryTime = candles[candles.length - 1].t;
  const historyLagMs = Math.max(0, Date.now() - (latestHistoryTime + cfg.ms));
  candles = applyLivePrice(candles, tf, livePrice, historyLagMs);
  candles = applyVolume(candles);
  candles = markClosed(candles, tf);
  const closed = candles.filter((c) => c.closed);
  const lastClosed = closed[closed.length - 1] || candles[candles.length - 1];
  const displayedPrice = Number.isFinite(livePrice) ? livePrice : candles[candles.length - 1].c;
  const warning = historyLagMs > Math.max(cfg.ms * 3, 5 * 60_000)
    ? `Candle history lags live price by ${formatDuration(historyLagMs)}; showing live price without fabricating missing candles.`
    : "";

  return {
    provider: "twelvedata",
    symbol: SYMBOL,
    timeframe: tf,
    candles,
    live: displayedPrice,
    livePrice: displayedPrice,
    asOf: new Date().toISOString(),
    lastClosedTime: new Date(lastClosed.t).toISOString(),
    nextCloseTime: new Date(lastClosed.t + cfg.ms).toISOString(),
    historyLagMs,
    volumeSource: candles.some((c) => c.volumeSource === "provider") ? "provider" : "range_proxy",
    warning,
  };
}

function simulatedMarket(tf) {
  const cfg = TIMEFRAMES[tf];
  const count = cfg.count;
  const now = Date.now();
  const currentBucket = Math.floor(now / cfg.ms) * cfg.ms;
  const lastClosed = currentBucket - cfg.ms;
  let price = 2350 + Math.sin(now / 18_000_000) * 18;
  let seed = Math.floor(lastClosed / cfg.ms) ^ tf.charCodeAt(1);
  const rand = () => {
    seed |= 0;
    seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  const candles = [];
  const start = lastClosed - (count - 1) * cfg.ms;
  for (let i = 0; i < count; i += 1) {
    const t = start + i * cfg.ms;
    const hour = new Date(t).getUTCHours();
    const sessionBoost = (hour >= 7 && hour < 10) || (hour >= 12 && hour < 15) ? 1.8 : 1;
    const scale = tf === "D1" ? 18 : tf === "M15" ? 4.8 : tf === "M5" ? 2.8 : 1.8;
    const o = price;
    const drift = (rand() - 0.49) * scale * sessionBoost;
    const c = o + drift + Math.sin((t / cfg.ms) / 17) * scale * 0.12;
    const h = Math.max(o, c) + rand() * scale * sessionBoost;
    const l = Math.min(o, c) - rand() * scale * sessionBoost;
    const vol = Math.round((700 + rand() * 1700) * sessionBoost);
    candles.push({ t, o: round(o), h: round(h), l: round(l), c: round(c), vol, volumeSource: "simulated", closed: true });
    price = c;
  }
  const live = round(candles[candles.length - 1].c + (Math.random() - 0.5) * (tf === "D1" ? 1.5 : 0.4));
  candles.push({
    t: currentBucket,
    o: candles[candles.length - 1].c,
    h: Math.max(candles[candles.length - 1].c, live),
    l: Math.min(candles[candles.length - 1].c, live),
    c: live,
    vol: Math.max(1, Math.round(Math.abs(live - candles[candles.length - 1].c) * 650)),
    volumeSource: "simulated",
    closed: false,
  });

  return {
    provider: "simulated",
    symbol: SYMBOL,
    timeframe: tf,
    candles: candles.slice(-count),
    live,
    livePrice: live,
    volumeSource: "simulated",
    asOf: new Date().toISOString(),
    lastClosedTime: new Date(lastClosed).toISOString(),
    nextCloseTime: new Date(currentBucket + cfg.ms).toISOString(),
  };
}

function round(value) {
  return Math.round(value * 100) / 100;
}

function detectPatterns(candles) {
  const patterns = [];
  for (let i = 1; i < candles.length; i += 1) {
    const prev = candles[i - 1];
    const cur = candles[i];
    const body = Math.abs(cur.c - cur.o);
    const range = Math.max(cur.h - cur.l, 0.01);
    const upper = cur.h - Math.max(cur.o, cur.c);
    const lower = Math.min(cur.o, cur.c) - cur.l;

    if (cur.c > cur.o && cur.o <= prev.c && cur.c >= prev.o && prev.c < prev.o) {
      patterns.push({ idx: i, type: "bullish_engulfing", direction: "LONG" });
    }
    if (cur.c < cur.o && cur.o >= prev.c && cur.c <= prev.o && prev.c > prev.o) {
      patterns.push({ idx: i, type: "bearish_engulfing", direction: "SHORT" });
    }
    if (lower > body * 1.8 && lower > upper * 1.4 && cur.c > cur.o) {
      patterns.push({ idx: i, type: "bullish_rejection", direction: "LONG" });
    }
    if (upper > body * 1.8 && upper > lower * 1.4 && cur.c < cur.o) {
      patterns.push({ idx: i, type: "bearish_rejection", direction: "SHORT" });
    }
    if (cur.h > prev.h && cur.l < prev.l && body / range > 0.45) {
      patterns.push({ idx: i, type: "outside_bar", direction: cur.c >= cur.o ? "LONG" : "SHORT" });
    }
    if (cur.h < prev.h && cur.l > prev.l) {
      patterns.push({ idx: i, type: "inside_bar", direction: "FLAT" });
    }
  }
  return patterns.slice(-24);
}

function buildRuleSnapshot(market) {
  const candles = market.candles;
  const closedCandles = candles.filter((c) => c.closed !== false);
  const ruleCandles = closedCandles.length >= 2 ? closedCandles : candles;
  const last = ruleCandles[ruleCandles.length - 1];
  const prev = ruleCandles[ruleCandles.length - 2] || last;
  const foundSignalIdx = candles.findIndex((c) => c.t === last.t);
  const signalIdx = foundSignalIdx >= 0 ? foundSignalIdx : Math.max(0, ruleCandles.length - 1);
  const patterns = detectPatterns(ruleCandles);
  const recent = ruleCandles.slice(-80);
  const hi = Math.max(...recent.map((c) => c.h));
  const lo = Math.min(...recent.map((c) => c.l));
  const mid = (hi + lo) / 2;
  const span = Math.max(hi - lo, 0.01);
  const levels = [
    { name: "R2", price: round(hi - span * 0.04), kind: "res" },
    { name: "R1", price: round(lo + span * 0.72), kind: "res" },
    { name: "S1", price: round(lo + span * 0.32), kind: "sup" },
    { name: "S2", price: round(lo + span * 0.06), kind: "sup" },
  ];
  const lastPattern = patterns[patterns.length - 1];
  const hour = new Date(last.t).getUTCHours();
  const inSession = (hour >= 7 && hour < 10) || (hour >= 12 && hour < 15);
  const session = hour >= 7 && hour < 10 ? "London" : hour >= 12 && hour < 15 ? "NY" : "Off-session";
  const zone = last.c > mid ? "Premium" : "Discount";
  const avgRange = recent.reduce((sum, c) => sum + (c.h - c.l), 0) / recent.length;
  const rangeOk = last.h - last.l > avgRange * 1.15;
  const nearSupport = levels.filter((l) => l.kind === "sup").some((l) => Math.abs(last.c - l.price) <= span * 0.08);
  const nearResistance = levels.filter((l) => l.kind === "res").some((l) => Math.abs(last.c - l.price) <= span * 0.08);

  let direction = "FLAT";
  const reasons = [];
  if (lastPattern && lastPattern.direction === "LONG" && zone === "Discount" && (nearSupport || inSession)) {
    direction = "LONG";
    reasons.push(lastPattern.type, zone.toLowerCase() + "_zone");
  } else if (lastPattern && lastPattern.direction === "SHORT" && zone === "Premium" && (nearResistance || inSession)) {
    direction = "SHORT";
    reasons.push(lastPattern.type, zone.toLowerCase() + "_zone");
  } else {
    reasons.push("no_complete_rule_confluence", zone.toLowerCase() + "_zone");
  }
  if (inSession) reasons.push("kill_zone");
  if (rangeOk) reasons.push("range_expansion");

  const stopDistance = market.timeframe === "D1" ? 18 : market.timeframe === "M15" ? 5 : market.timeframe === "M5" ? 3 : 2;
  const entry = direction === "FLAT" ? null : last.c;
  const stop = direction === "LONG" ? round(last.c - stopDistance) : direction === "SHORT" ? round(last.c + stopDistance) : null;
  const tp = direction === "LONG" ? round(last.c + stopDistance * 1.8) : direction === "SHORT" ? round(last.c - stopDistance * 1.8) : null;
  const confidence = direction === "FLAT" ? 0.18 : Math.min(0.88, 0.38 + reasons.length * 0.1);

  return {
    ...market,
    change: round((market.livePrice || last.c) - prev.c),
    changePct: round((((market.livePrice || last.c) - prev.c) / prev.c) * 100),
    levels,
    patterns,
    dr: { high: round(hi), low: round(lo), mid: round(mid) },
    session,
    zone,
    signal: {
      idx: signalIdx,
      t: last.t,
      direction,
      entry,
      stop,
      tp,
      confidence,
      session,
      reasons,
      result: 0,
    },
  };
}

async function getMarket(tf) {
  // Fetch Python signal and chart candles in parallel with the TwelveData pull
  const engineTf = CHART_TO_ENGINE_TF[tf] || "M15";
  const candleCount = TIMEFRAMES[tf]?.count || 240;
  const [pySignal, pyCandles] = await Promise.all([
    fetchPython("/signal"),
    fetchPython(`/candles?tf=${engineTf}&n=${candleCount}`),
  ]);

  // Try to get the market from the Python candle feed
  if (pyCandles && Array.isArray(pyCandles.candles) && pyCandles.candles.length > 4) {
    const market = buildPythonMarket(pyCandles, pySignal, tf);
    return market;
  }

  // Fall back to TwelveData (or simulated) for chart candles, but inject Python signal
  try {
    const live = await fetchTwelveData(tf);
    if (live) {
      const snap = buildRuleSnapshot(live);
      if (pySignal) snap.signal = normalisePySignal(pySignal, snap.candles);
      return snap;
    }
  } catch (error) {
    const fallback = simulatedMarket(tf);
    const snap = buildRuleSnapshot({
      ...fallback,
      provider: "simulated",
      warning: `Live API failed: ${error.message}`,
    });
    if (pySignal) snap.signal = normalisePySignal(pySignal, snap.candles);
    return snap;
  }
  const snap = buildRuleSnapshot(simulatedMarket(tf));
  if (pySignal) snap.signal = normalisePySignal(pySignal, snap.candles);
  return snap;
}

function normalisePySignal(py, candles) {
  // Find the candle closest to the engine's bar_time for the chart marker
  const targetMs = py.bar_time_ms || 0;
  let idx = candles.length - 1;
  let minDiff = Infinity;
  candles.forEach((c, i) => {
    const diff = Math.abs(c.t - targetMs);
    if (diff < minDiff) { minDiff = diff; idx = i; }
  });
  return {
    idx,
    t: targetMs || candles[idx]?.t,
    direction: py.direction,
    entry: py.entry ?? null,
    stop: py.stop ?? null,
    tp: py.tp ?? py.take_profit ?? null,
    confidence: py.confidence ?? 0,
    session: py.session || "OFF",
    reasons: py.reasons || [],
    htf_bias: py.htf_bias ?? null,
    d1_bias: py.d1_bias ?? null,
    zone: py.zone ?? null,
    result: 0,
    source: "python_engine",
  };
}

function buildPythonMarket(pyCandles, pySignal, tf) {
  // Build a market object from Dukascopy candles + Python signal
  const candles = pyCandles.candles;
  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2] || last;
  const livePrice = pyCandles.live ?? last.c;

  const market = {
    provider: "dukascopy",
    symbol: "XAU/USD",
    timeframe: tf,
    candles,
    live: livePrice,
    livePrice,
    volumeSource: "provider",
    asOf: new Date().toISOString(),
    lastClosedTime: new Date(last.t).toISOString(),
    nextCloseTime: null,
    warning: "",
  };

  const snap = buildRuleSnapshot(market);
  if (pySignal) snap.signal = normalisePySignal(pySignal, candles);
  return snap;
}

async function serveStatic(req, res, pathname) {
  const vendorChartPath = "/vendor/lightweight-charts.standalone.production.js";
  const filePath = pathname === "/"
    ? path.join(ROOT, "dashboard.html")
    : pathname === vendorChartPath
      ? path.join(ROOT, "node_modules", "lightweight-charts", "dist", "lightweight-charts.standalone.production.js")
      : path.join(ROOT, pathname);
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  try {
    const ext = path.extname(filePath);
    const body = await fsp.readFile(filePath);
    res.writeHead(200, { "Content-Type": contentTypes[ext] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}

// ── Paper trading helpers ─────────────────────────────────────────────────────
const PAPER_BOOK = path.join(ROOT, "reports", "paper_book.json");
const PAPER_TRADES_LOG = path.join(ROOT, "reports", "paper_trades.jsonl");

function paperR(trade) {
  if (trade.status === "open" || trade.exit_price == null) return null;
  const risk = Math.abs(trade.entry - trade.stop);
  if (risk === 0) return 0;
  const gross = trade.direction === "LONG"
    ? trade.exit_price - trade.entry
    : trade.entry - trade.exit_price;
  return Math.round((gross / risk) * 1000) / 1000;
}

async function getPaperStats() {
  let book = [];
  try { book = JSON.parse(await fsp.readFile(PAPER_BOOK, "utf8")); } catch {}
  const closed = book.filter(t => t.status !== "open");
  const open   = book.filter(t => t.status === "open");
  const rs     = closed.map(paperR).filter(r => r !== null);
  const wins   = rs.filter(r => r > 0);
  const losses = rs.filter(r => r <= 0);
  // build cumulative equity curve
  const equity = [0];
  for (const r of rs) equity.push(Math.round((equity.at(-1) + r) * 1000) / 1000);
  return {
    trades: book,
    stats: {
      total: closed.length,
      open:  open.length,
      wins:  wins.length,
      losses: losses.length,
      win_rate: closed.length ? Math.round(wins.length / closed.length * 1000) / 1000 : 0,
      total_r: Math.round(rs.reduce((s, r) => s + r, 0) * 1000) / 1000,
      avg_win:  wins.length   ? Math.round(wins.reduce((s,r)=>s+r,0)  / wins.length  * 100)/100 : 0,
      avg_loss: losses.length ? Math.round(losses.reduce((s,r)=>s+r,0)/ losses.length* 100)/100 : 0,
      open_trade: open[0] ?? null,
    },
    equity,
  };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname === "/api/market") {
    const tf = normalizeTimeframe(url.searchParams.get("timeframe"));
    try {
      sendJson(res, 200, await getMarket(tf));
    } catch (error) {
      sendJson(res, 500, { error: error.message });
    }
    return;
  }
  if (url.pathname === "/api/paper") {
    sendJson(res, 200, await getPaperStats());
    return;
  }
  await serveStatic(req, res, decodeURIComponent(url.pathname));
});

function startPaperWatcher() {
  const py = spawn("python3", ["-m", "src.live", "--watch", "1800"], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  py.stdout.on("data", (d) => process.stdout.write("[paper] " + d));
  py.stderr.on("data", (d) => process.stderr.write("[paper] " + d));
  py.on("exit", (code) => {
    console.warn(`[paper] watcher exited (${code}); restarting in 120s`);
    setTimeout(startPaperWatcher, 120_000);
  });
  return py;
}

startPythonEngine();
startPaperWatcher();

server.listen(PORT, HOST, () => {
  console.log(`\nXAU/USD Signal Engine`);
  console.log(`  Dashboard   →  http://${HOST}:${PORT}`);
  console.log(`  Engine API  →  http://127.0.0.1:${PY_PORT}`);
  console.log(`  Paper book  →  reports/paper_book.json`);
  console.log(`  Signal log  →  reports/paper_signals.jsonl\n`);
});
