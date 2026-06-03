const $ = (selector) => document.querySelector(selector);
const fmt = (n, d = 2) => n == null || Number.isNaN(Number(n))
  ? "-"
  : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtTime = (t) => new Date(t).toISOString().slice(5, 16).replace("T", " ");

const COLORS = {
  text: "#e8e4da",
  muted: "#7d8178",
  line: "#30332e",
  gold: "#d4a93f",
  long: "#56b98a",
  short: "#d96459",
  flat: "#7d8178",
};

const APP = {
  timeframe: "M2",
  timer: null,
  viewStart: 0,
  viewCount: 140,
  drag: null,
  chartReady: false,
};

const sessionBands = [{ h0: 7, h1: 10 }, { h0: 12, h1: 15 }];
const chartGeom = {};
let SYSTEM_DATA = null;
let PRICE_CHART = null;
let CANDLE_SERIES = null;
let VOLUME_SERIES = null;
let SERIES_MARKERS = null;
let PRICE_LINES = [];

function mulberry32(a) {
  return function rand() {
    a |= 0;
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function round(n) {
  return Math.round(n * 100) / 100;
}

function chartTime(t) {
  return Math.floor(Number(t) / 1000);
}

function sessionName(t) {
  const h = new Date(t).getUTCHours();
  if (h >= 7 && h < 10) return "London";
  if (h >= 12 && h < 15) return "NY";
  if (h < 7) return "Asia";
  return "Off";
}

function detectPatterns(candles) {
  const patterns = [];
  for (let i = 1; i < candles.length; i += 1) {
    const p = candles[i - 1];
    const c = candles[i];
    const body = Math.abs(c.c - c.o);
    const range = Math.max(c.h - c.l, 0.01);
    const upper = c.h - Math.max(c.o, c.c);
    const lower = Math.min(c.o, c.c) - c.l;
    if (c.c > c.o && c.o <= p.c && c.c >= p.o && p.c < p.o) patterns.push({ idx: i, type: "bullish_engulfing", direction: "LONG" });
    if (c.c < c.o && c.o >= p.c && c.c <= p.o && p.c > p.o) patterns.push({ idx: i, type: "bearish_engulfing", direction: "SHORT" });
    if (lower > body * 1.8 && lower > upper * 1.4 && c.c > c.o) patterns.push({ idx: i, type: "bullish_rejection", direction: "LONG" });
    if (upper > body * 1.8 && upper > lower * 1.4 && c.c < c.o) patterns.push({ idx: i, type: "bearish_rejection", direction: "SHORT" });
    if (c.h > p.h && c.l < p.l && body / range > 0.45) patterns.push({ idx: i, type: "outside_bar", direction: c.c >= c.o ? "LONG" : "SHORT" });
    if (c.h < p.h && c.l > p.l) patterns.push({ idx: i, type: "inside_bar", direction: "FLAT" });
  }
  return patterns.slice(-24);
}

function simulatedMarket(tf) {
  const cfg = { M2: [120_000, 240], M5: [300_000, 240], M15: [900_000, 220], D1: [86_400_000, 180] }[tf];
  const [ms, count] = cfg;
  const now = Date.now();
  const lastClosed = Math.floor(now / ms) * ms;
  const rng = mulberry32(Math.floor(lastClosed / ms) ^ tf.charCodeAt(1));
  let price = 2350 + Math.sin(now / 18_000_000) * 18;
  const candles = [];

  for (let i = 0; i < count; i += 1) {
    const t = lastClosed - (count - 1 - i) * ms;
    const h = new Date(t).getUTCHours();
    const sessionBoost = (h >= 7 && h < 10) || (h >= 12 && h < 15) ? 1.8 : 1;
    const scale = tf === "D1" ? 18 : tf === "M15" ? 4.8 : tf === "M5" ? 2.8 : 1.8;
    const o = price;
    const c = o + (rng() - 0.49) * scale * sessionBoost + Math.sin((t / ms) / 17) * scale * 0.12;
    const high = Math.max(o, c) + rng() * scale * sessionBoost;
    const low = Math.min(o, c) - rng() * scale * sessionBoost;
    candles.push({ t, o: round(o), h: round(high), l: round(low), c: round(c), vol: Math.round((700 + rng() * 1700) * sessionBoost), volumeSource: "simulated" });
    price = c;
  }

  return buildData({
    provider: "simulated",
    symbol: "XAU/USD",
    timeframe: tf,
    candles,
    live: candles.at(-1).c,
    volumeSource: "simulated",
    asOf: new Date().toISOString(),
  });
}

function fallbackSignal(candles, patterns, zone, session) {
  const last = candles.at(-1);
  const latestPattern = patterns.at(-1);
  const inSession = session.startsWith("London") || session.startsWith("NY");
  let direction = "FLAT";
  let reasons = ["no_complete_rule_confluence", `${zone.toLowerCase()}_zone`];

  if (latestPattern && latestPattern.idx === candles.length - 1 && latestPattern.direction === "LONG" && zone === "Discount" && inSession) {
    direction = "LONG";
    reasons = [latestPattern.type, "discount_zone", "kill_zone"];
  } else if (latestPattern && latestPattern.idx === candles.length - 1 && latestPattern.direction === "SHORT" && zone === "Premium" && inSession) {
    direction = "SHORT";
    reasons = [latestPattern.type, "premium_zone", "kill_zone"];
  }

  const d = APP.timeframe === "D1" ? 18 : APP.timeframe === "M15" ? 5 : APP.timeframe === "M5" ? 3 : 2;
  return {
    idx: candles.length - 1,
    t: last.t,
    direction,
    entry: direction === "FLAT" ? null : last.c,
    stop: direction === "LONG" ? round(last.c - d) : direction === "SHORT" ? round(last.c + d) : null,
    tp: direction === "LONG" ? round(last.c + d * 1.8) : direction === "SHORT" ? round(last.c - d * 1.8) : null,
    confidence: direction === "FLAT" ? 0.18 : 0.62,
    session: session.replace(/ .*/, ""),
    reasons,
    result: 0,
  };
}

function buildData(market) {
  const candles = market.candles.map((c) => ({
    t: Number(c.t),
    o: Number(c.o),
    h: Number(c.h),
    l: Number(c.l),
    c: Number(c.c),
    vol: Number(c.vol || 0),
    volumeSource: c.volumeSource || market.volumeSource || "unknown",
    closed: c.closed !== false,
  })).filter((c) => Number.isFinite(c.t) && Number.isFinite(c.c));
  const last = candles.at(-1);
  const prev = candles.at(-2) || last;

  if (last && last.closed === false && Number.isFinite(Number(market.live))) {
    last.c = round(Number(market.live));
    last.h = Math.max(last.h, last.c);
    last.l = Math.min(last.l, last.c);
  }

  const recent = candles.slice(-80);
  const hi = Math.max(...recent.map((c) => c.h));
  const lo = Math.min(...recent.map((c) => c.l));
  const span = Math.max(hi - lo, 0.01);
  const mid = (hi + lo) / 2;
  const patterns = market.patterns || detectPatterns(candles);
  const levels = market.levels || [
    { name: "R2", price: round(hi - span * 0.04), kind: "res" },
    { name: "R1", price: round(lo + span * 0.72), kind: "res" },
    { name: "S1", price: round(lo + span * 0.32), kind: "sup" },
    { name: "S2", price: round(lo + span * 0.06), kind: "sup" },
  ];
  const slope = last.c - candles[Math.max(0, candles.length - 50)].c;
  const bias = slope > span * 0.08
    ? { label: "Bullish", dir: "bull", detail: "Recent closes above 50-bar slope" }
    : slope < -span * 0.08
      ? { label: "Bearish", dir: "bear", detail: "Recent closes below 50-bar slope" }
      : { label: "Neutral", dir: "neutral", detail: "Mixed range / no clean slope" };
  const h = new Date(last.t).getUTCHours();
  const session = h >= 7 && h < 10 ? "London (Q2 · Manipulation)" : h >= 12 && h < 15 ? "NY (Q3 · Distribution)" : h < 7 ? "Asia / pre-London" : "Off-session";
  const zone = last.c > mid ? "Premium" : "Discount";
  const current = market.signal || fallbackSignal(candles, patterns, zone, session);
  const state = new Array(candles.length).fill("FLAT");
  if (current.direction !== "FLAT") state[current.idx] = current.direction;
  const patternRows = patterns.slice(-12).map((p) => {
    const c = candles[p.idx];
    return {
      idx: p.idx,
      t: c.t,
      direction: p.direction,
      session: sessionName(c.t),
      confidence: p.direction === "FLAT" ? 0.15 : 0.34,
      entry: p.direction === "FLAT" ? null : c.c,
      stop: null,
      tp: null,
      reasons: [p.type],
      result: 0,
    };
  });
  const signals = [...patternRows, current].filter((s, i, rows) =>
    rows.findIndex((r) => r.t === s.t && r.reasons.join() === s.reasons.join()) === i
  );
  const confluenceTable = [
    { label: "Dealing zone clear", pts: 1, on: zone === "Premium" || zone === "Discount" },
    { label: "Recent structure bias readable", pts: 1, on: bias.dir !== "neutral" },
    { label: "Latest candle pattern present", pts: 2, on: patterns.at(-1)?.idx === candles.length - 1 },
    { label: "London / NY timing", pts: 1, on: session.startsWith("London") || session.startsWith("NY") },
    { label: "Rule produced directional signal", pts: 2, on: current.direction !== "FLAT" },
  ];
  const flatShare = state.filter((s) => s === "FLAT").length / state.length;
  return {
    instrument: market.symbol || "XAU/USD",
    timeframe: market.timeframe || APP.timeframe,
    asOf: new Date(market.asOf || last.t).toISOString().slice(0, 19).replace("T", " ") + " UTC",
    price: Number.isFinite(Number(market.livePrice ?? market.live)) ? Number(market.livePrice ?? market.live) : last.c,
    change: Number.isFinite(Number(market.change)) ? Number(market.change) : round(last.c - prev.c),
    changePct: Number.isFinite(Number(market.changePct)) ? Number(market.changePct) : round(((last.c - prev.c) / prev.c) * 100),
    provider: market.provider || "local",
    volumeSource: market.volumeSource || candles.find((c) => c.volumeSource === "provider")?.volumeSource || candles.at(-1)?.volumeSource || "unknown",
    warning: market.warning || "",
    lastClosedTime: market.lastClosedTime || "",
    nextCloseTime: market.nextCloseTime || "",
    bias,
    session,
    zone,
    candles,
    levels,
    dr: { high: round(hi), low: round(lo), mid: round(mid) },
    sessionBands,
    state,
    patterns,
    signals,
    current,
    confluenceTable,
    metrics: { flatShare },
    equity: fakeEquity(candles),
  };
}

let PAPER_DATA = null;

async function loadPaper() {
  try {
    const res = await fetch("/api/paper", { cache: "no-store" });
    if (!res.ok) return;
    PAPER_DATA = await res.json();
    renderPaper();
  } catch {}
}

function renderPaper() {
  if (!PAPER_DATA) return;
  const { stats, equity, trades } = PAPER_DATA;

  // ── equity curve (replaces fake) ──────────────────────────────────────
  if (SYSTEM_DATA) {
    SYSTEM_DATA.equity = equity.length > 1
      ? { pts: equity, isLen: equity.length }
      : { pts: [0], isLen: 1 };
    drawEquity();
  }

  // ── paper KPI panel ───────────────────────────────────────────────────
  const noTrades = stats.total === 0;
  const kpis = noTrades
    ? [{ k: "Paper trades", v: "0", note: "waiting for first signal…" },
       { k: "Status", v: "watching", note: "paper watcher running" }]
    : [
        { k: "Total trades",  v: stats.total,                  note: "closed paper positions" },
        { k: "Open",          v: stats.open,                   note: stats.open_trade ? `${stats.open_trade.direction} @ ${stats.open_trade.entry}` : "no open trade" },
        { k: "Win rate",      v: `${Math.round(stats.win_rate * 100)} %`, note: `${stats.wins}W / ${stats.losses}L` },
        { k: "Total R",       v: (stats.total_r >= 0 ? "+" : "") + stats.total_r + " R",
          note: stats.total_r >= 0 ? "in profit" : "in drawdown", warn: stats.total_r < 0 },
        { k: "Avg win",       v: `+${stats.avg_win} R`,        note: "per winning trade" },
        { k: "Avg loss",      v: `${stats.avg_loss} R`,        note: "per losing trade" },
      ];
  $("#kpi-grid").innerHTML = kpis.map((k) =>
    `<div class="kpi ${k.warn ? "warn" : ""}">
      <div class="k">${k.k}</div>
      <div class="v">${k.v}</div>
      <div class="note">${k.note}</div>
    </div>`).join("");

  const noteEl = $("#oos-note");
  if (noteEl) {
    noteEl.textContent = noTrades
      ? "No paper trades yet — engine is watching for setups"
      : `Cumulative: ${(stats.total_r >= 0 ? "+" : "") + stats.total_r} R over ${stats.total} trades`;
  }

  // ── open position in signal panel ─────────────────────────────────────
  const openEl = $("#paper-open");
  if (openEl && stats.open_trade) {
    const t = stats.open_trade;
    openEl.innerHTML = `<span class="badge ${t.direction}">${t.direction}</span> `
      + `entry <b>${t.entry}</b> · stop <b>${t.stop}</b> · tp <b>${t.take_profit}</b>`;
    openEl.style.display = "block";
  } else if (openEl) {
    openEl.style.display = "none";
  }
  const noneEl = $("#paper-none");
  if (noneEl) noneEl.style.display = stats.open_trade ? "none" : "block";
}

async function loadMarket() {
  setFeedStatus("loading", "connecting");
  try {
    const res = await fetch(`/api/market?timeframe=${APP.timeframe}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    SYSTEM_DATA = buildData(data);
    setFeedStatus(SYSTEM_DATA.provider === "simulated" ? "fallback" : "live", SYSTEM_DATA.provider === "simulated" ? "simulated feed" : `${SYSTEM_DATA.provider} live`);
  } catch (error) {
    SYSTEM_DATA = simulatedMarket(APP.timeframe);
    SYSTEM_DATA.warning = error.message;
    setFeedStatus("fallback", "local fallback");
  }
  resetView(false);
  renderAll();
  loadPaper();
}

function setFeedStatus(kind, text) {
  const cls = kind === "live" ? "feed-live" : kind === "fallback" ? "feed-fallback" : kind === "error" ? "feed-error" : "";
  $("#feed-status").className = `head-actions ${cls}`;
  $("#feed-status").textContent = text;
  $("#t-feed").className = cls;
  $("#t-feed").textContent = text;
}

function resetView(force = true) {
  if (!SYSTEM_DATA) return;
  const n = SYSTEM_DATA.candles.length;
  const desired = APP.timeframe === "D1" ? 120 : 140;
  if (force || APP.viewStart + APP.viewCount >= n - 2) {
    APP.viewCount = Math.min(desired, n);
    APP.viewStart = Math.max(0, n - APP.viewCount);
  }
}

function setupCanvas(cv, h) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth;
  const targetH = h || cv.clientHeight || 300;
  cv.width = w * dpr;
  cv.height = targetH * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h: targetH };
}

function renderTop() {
  const d = SYSTEM_DATA;
  $("#t-instrument").textContent = d.instrument;
  $("#t-price").textContent = fmt(d.price);
  const up = d.change >= 0;
  $("#t-change").textContent = `${up ? "+" : ""}${fmt(d.change)}  (${up ? "+" : ""}${d.changePct}%)`;
  $("#t-change").className = `chg mono ${up ? "pos" : "neg"}`;
  $("#t-tf").textContent = d.timeframe;
  $("#chart-title").textContent = `Price & Levels - ${d.timeframe}`;
  $("#t-bias").textContent = d.bias.label;
  $("#t-bias-dot").className = `dot ${d.bias.dir === "bull" ? "bull" : d.bias.dir === "bear" ? "bear" : "neutral"}`;
  $("#t-session").innerHTML = `Session <b>${d.session}</b>`;
  $("#t-zone").innerHTML = `Range <b>${d.zone}</b>`;
  $("#t-asof").textContent = d.asOf;
  $("#ctx-bias").textContent = d.bias.detail;
  $("#ctx-session").textContent = d.session;
  $("#ctx-zone").textContent = d.zone;
  $("#ctx-pattern").textContent = (d.patterns.at(-1)?.type || "none").replaceAll("_", " ");
}

function initTradingChart() {
  if (PRICE_CHART) return;
  const el = $("#chart");
  const lc = window.LightweightCharts;
  if (!lc) throw new Error("Lightweight Charts failed to load");

  PRICE_CHART = lc.createChart(el, {
    width: el.clientWidth,
    height: el.clientHeight,
    layout: {
      background: { type: "solid", color: "#0b0c0c" },
      textColor: COLORS.text,
      fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif",
    },
    grid: {
      vertLines: { color: "rgba(48,51,46,0.55)" },
      horzLines: { color: "rgba(48,51,46,0.75)" },
    },
    rightPriceScale: {
      borderColor: COLORS.line,
      scaleMargins: { top: 0.08, bottom: 0.22 },
    },
    timeScale: {
      borderColor: COLORS.line,
      timeVisible: APP.timeframe !== "D1",
      secondsVisible: false,
      rightOffset: 8,
      barSpacing: 7,
      fixLeftEdge: false,
      lockVisibleTimeRangeOnResize: true,
    },
    crosshair: {
      mode: lc.CrosshairMode.Normal,
      vertLine: { color: "rgba(184,180,170,0.35)", width: 1, style: lc.LineStyle.Dashed },
      horzLine: { color: "rgba(184,180,170,0.35)", width: 1, style: lc.LineStyle.Dashed },
    },
    localization: { priceFormatter: (p) => fmt(p) },
  });

  CANDLE_SERIES = PRICE_CHART.addSeries(lc.CandlestickSeries, {
    upColor: COLORS.long,
    downColor: COLORS.short,
    borderUpColor: COLORS.long,
    borderDownColor: COLORS.short,
    wickUpColor: COLORS.long,
    wickDownColor: COLORS.short,
    priceFormat: { type: "price", precision: 2, minMove: 0.01 },
  });
  VOLUME_SERIES = PRICE_CHART.addSeries(lc.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "",
    color: "rgba(125,129,120,0.35)",
    lastValueVisible: false,
    priceLineVisible: false,
  });
  PRICE_CHART.priceScale("").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  PRICE_CHART.subscribeCrosshairMove(showTradingTooltip);
}

function candleSeriesData(candles) {
  return candles.map((c) => {
    const up = c.c >= c.o;
    const live = c.closed === false;
    const color = live ? COLORS.gold : up ? COLORS.long : COLORS.short;
    return {
      time: chartTime(c.t),
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
      color,
      borderColor: color,
      wickColor: live ? COLORS.gold : color,
    };
  });
}

function markerData(d) {
  const markers = [];
  d.patterns.slice(-10).forEach((p) => {
    const c = d.candles[p.idx];
    if (!c) return;
    markers.push({
      time: chartTime(c.t),
      position: p.direction === "LONG" ? "belowBar" : p.direction === "SHORT" ? "aboveBar" : "inBar",
      shape: "circle",
      color: p.direction === "LONG" ? COLORS.long : p.direction === "SHORT" ? COLORS.short : COLORS.gold,
      size: 0.45,
    });
  });
  d.signals.filter((s) => s.direction !== "FLAT").forEach((s) => {
    const c = d.candles[s.idx];
    if (!c) return;
    const long = s.direction === "LONG";
    markers.push({
      time: chartTime(c.t),
      position: long ? "belowBar" : "aboveBar",
      shape: long ? "arrowUp" : "arrowDown",
      color: long ? COLORS.long : COLORS.short,
      text: s.direction,
      size: 1.1,
    });
  });
  return markers;
}

function redrawPriceLines(d) {
  PRICE_LINES.forEach((line) => CANDLE_SERIES.removePriceLine(line));
  PRICE_LINES = [];
  const lc = window.LightweightCharts;
  const add = (price, color, title, width = 1, style = lc.LineStyle.Dashed) => {
    if (price == null || !Number.isFinite(Number(price))) return;
    PRICE_LINES.push(CANDLE_SERIES.createPriceLine({
      price: Number(price),
      color,
      lineWidth: width,
      lineStyle: style,
      axisLabelVisible: true,
      title,
    }));
  };
  add(d.dr.mid, "rgba(184,180,170,0.65)", "0.5");
  d.levels.forEach((level) => add(level.price, COLORS.gold, level.name));
  add(d.price, COLORS.gold, "LIVE", 1, lc.LineStyle.Solid);
  add(d.current.entry, COLORS.text, "ENTRY", 2);
  add(d.current.stop, COLORS.short, "STOP", 2);
  add(d.current.tp, COLORS.long, "TP", 2);
}

function drawChart() {
  if (!SYSTEM_DATA) return;
  initTradingChart();
  const d = SYSTEM_DATA;
  const chartEl = $("#chart");
  PRICE_CHART.resize(chartEl.clientWidth, chartEl.clientHeight);
  const visible = PRICE_CHART.timeScale().getVisibleLogicalRange();
  CANDLE_SERIES.setData(candleSeriesData(d.candles));
  VOLUME_SERIES.setData(d.candles.map((c) => ({
    time: chartTime(c.t),
    value: c.vol || 0,
    color: c.c >= c.o ? "rgba(86,185,138,0.26)" : "rgba(217,100,89,0.26)",
  })));
  if (SERIES_MARKERS) {
    SERIES_MARKERS.setMarkers(markerData(d));
  } else if (window.LightweightCharts.createSeriesMarkers) {
    SERIES_MARKERS = window.LightweightCharts.createSeriesMarkers(CANDLE_SERIES, markerData(d));
  }
  redrawPriceLines(d);
  PRICE_CHART.applyOptions({ timeScale: { timeVisible: APP.timeframe !== "D1" } });
  if (APP.chartReady && visible) {
    PRICE_CHART.timeScale().setVisibleLogicalRange(visible);
  } else {
    const from = Math.max(0, d.candles.length - (APP.timeframe === "D1" ? 120 : 140));
    PRICE_CHART.timeScale().setVisibleLogicalRange({ from, to: d.candles.length + 8 });
    APP.chartReady = true;
  }
}

function drawRibbon() {
  if (!SYSTEM_DATA) return;
  const cv = $("#ribbon");
  const { ctx, w, h } = setupCanvas(cv);
  const st = SYSTEM_DATA.state.slice(APP.viewStart, APP.viewStart + APP.viewCount);
  const cellW = w / st.length;
  const colOf = { LONG: COLORS.long, SHORT: COLORS.short, FLAT: "#32362f" };
  ctx.clearRect(0, 0, w, h);
  st.forEach((s, i) => {
    ctx.fillStyle = colOf[s];
    ctx.fillRect(i * cellW, 4, Math.max(1, cellW - 0.5), h - 8);
  });
  const flats = SYSTEM_DATA.state.filter((s) => s === "FLAT").length;
  $("#flat-share").textContent = `FLAT ${Math.round(flats / SYSTEM_DATA.state.length * 100)}% of bars`;
}

function renderSignal() {
  const s = SYSTEM_DATA.current;
  $("#sig-time").textContent = `${fmtTime(s.t)} UTC`;
  $("#sig-state").className = `signal-state ${s.direction}`;
  $("#sig-dir").textContent = s.direction;
  $("#sig-conf").textContent = s.confidence.toFixed(2);
  $("#sig-entry").textContent = fmt(s.entry);
  $("#sig-stop").textContent = fmt(s.stop);
  $("#sig-tp").textContent = fmt(s.tp);
  const risk = s.entry == null || s.stop == null ? 0 : Math.abs(s.entry - s.stop);
  const reward = s.tp == null || s.entry == null ? 0 : Math.abs(s.tp - s.entry);
  $("#sig-rr").textContent = risk ? `1 : ${(reward / risk).toFixed(2)}` : "-";

  const tbl = SYSTEM_DATA.confluenceTable;
  const got = tbl.filter((r) => r.on).reduce((a, r) => a + r.pts, 0);
  const max = tbl.reduce((a, r) => a + r.pts, 0);
  $("#conf-score").textContent = `${got} / ${max}`;
  $("#conf-fill").style.width = `${got / max * 100}%`;
  $("#conf-list").innerHTML = tbl.map((r) =>
    `<div class="conf-item ${r.on ? "on" : ""}"><span class="mark">${r.on ? "✓" : ""}</span>${r.label}<span class="pts">+${r.pts}</span></div>`
  ).join("");
  $("#sig-reasons").innerHTML = s.reasons.map((r) => `<span class="tag">${r}</span>`).join("");
  const srcEl = $("#sig-source");
  if (srcEl) {
    const isPy = s.source === "python_engine";
    srcEl.textContent = isPy ? "python engine" : "js fallback";
    srcEl.className = `tag ${isPy ? "engine-live" : "engine-fallback"}`;
  }
  const biasEl = $("#sig-htf-bias");
  if (biasEl && s.htf_bias) {
    biasEl.textContent = `H4/D1 bias: ${s.htf_bias}`;
    biasEl.className = `tag ${s.htf_bias === "BULL" ? "pos" : s.htf_bias === "BEAR" ? "neg" : ""}`;
  }
}

function renderMetrics() {
  const d = SYSTEM_DATA;
  const kpis = [
    { k: "Mode", v: "Paper watch", note: "no live orders" },
    { k: "Provider", v: d.provider, note: d.warning || "live/watch feed" },
    { k: "Volume", v: d.volumeSource === "provider" ? "provider" : d.volumeSource === "simulated" ? "simulated" : "range proxy", note: d.volumeSource === "provider" ? "live provider volume" : "XAU/USD volume proxy from candle range" },
    { k: "Patterns", v: d.patterns.length, note: "recent closed candles" },
    { k: "FLAT share", v: `${Math.round(d.metrics.flatShare * 100)}%`, note: "no-trade is correct" },
    { k: "Latest price", v: fmt(d.price), note: `${d.timeframe} candle feed` },
    { k: "Last change", v: `${d.change >= 0 ? "+" : ""}${fmt(d.change)}`, note: `${d.changePct}%` },
    { k: "Signal", v: d.current.direction, note: d.current.reasons.join(" · ") },
    { k: "Backtest", v: "pending", note: "needed before trusting edge", warn: true },
  ];
  $("#kpi-grid").innerHTML = kpis.map((k) =>
    `<div class="kpi ${k.warn ? "warn" : ""}"><div class="k">${k.k}</div><div class="v">${k.v}</div><div class="note">${k.note}</div></div>`
  ).join("");
  $("#oos-note").textContent = "Live watch only; walk-forward metrics are not generated yet.";
}

function drawEquity() {
  if (!SYSTEM_DATA) return;
  const cv = $("#equity");
  const { ctx, w, h } = setupCanvas(cv);
  const eq = SYSTEM_DATA.equity;
  const pts = eq.pts;
  const padL = 8, padR = 44, padT = 14, padB = 18;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;
  const mn = Math.min(...pts);
  const mx = Math.max(...pts);
  const x = (i) => padL + i / Math.max(pts.length - 1, 1) * plotW;
  const y = (v) => padT + (mx - v) / ((mx - mn) || 1) * plotH;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(48,51,46,0.95)";
  ctx.beginPath();
  ctx.moveTo(padL, y(0));
  ctx.lineTo(padL + plotW, y(0));
  ctx.stroke();
  ctx.fillStyle = "rgba(214,167,66,0.08)";
  ctx.fillRect(x(eq.isLen), padT, plotW - (x(eq.isLen) - padL), plotH);
  ctx.strokeStyle = COLORS.gold;
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(x(i), y(p)) : ctx.moveTo(x(i), y(p)));
  ctx.stroke();
  ctx.lineWidth = 1;
}

function renderTradesStats() {
  const d = SYSTEM_DATA;
  const directional = d.signals.filter((s) => s.direction !== "FLAT");
  const rows = [
    ["Latest feed", d.provider],
    ["Visible timeframe", d.timeframe],
    ["Recent patterns", d.patterns.length],
    ["Directional rows", directional.length],
    ["Current state", d.current.direction],
    ["Last update", d.asOf.replace(" UTC", "")],
  ];
  $("#trades-stats").innerHTML = rows.map(([k, v]) =>
    `<div class="tstat"><span class="k">${k}</span><span class="v">${v}</span></div>`
  ).join("");
}

function renderLog() {
  $("#log-body").innerHTML = [...SYSTEM_DATA.signals].reverse().map((s) => {
    const rcls = s.direction === "LONG" ? "r-pos" : s.direction === "SHORT" ? "r-neg" : "";
    return `<tr>
      <td class="mono">${fmtTime(s.t)}</td>
      <td><span class="badge ${s.direction}">${s.direction}</span></td>
      <td>${s.session}</td>
      <td class="mono">${fmt(s.entry)}</td>
      <td class="mono">${fmt(s.stop)}</td>
      <td class="mono">${fmt(s.tp)}</td>
      <td class="mono">${s.confidence.toFixed(2)}</td>
      <td class="mono ${rcls}">paper</td>
      <td class="mono" style="color:var(--text-dim)">${s.reasons.join(" · ")}</td>
    </tr>`;
  }).join("");
}

function showTradingTooltip(param) {
  const tip = $("#tooltip");
  const wrap = $("#chart-wrap");
  if (!SYSTEM_DATA || !param?.time || !param.point || param.point.x < 0 || param.point.y < 0) {
    tip.style.opacity = 0;
    return;
  }
  const t = Number(param.time) * 1000;
  const idx = SYSTEM_DATA.candles.findIndex((c) => chartTime(c.t) === Number(param.time));
  const c = SYSTEM_DATA.candles[idx];
  if (!c) {
    tip.style.opacity = 0;
    return;
  }
  const sig = SYSTEM_DATA.signals.find((s) => s.idx === idx);
  const pat = SYSTEM_DATA.patterns.find((p) => p.idx === idx);
  const st = SYSTEM_DATA.state[idx] || "FLAT";
  const stCol = st === "LONG" ? "var(--long)" : st === "SHORT" ? "var(--short)" : "var(--flat)";
  let html = `<div class="tt-time">${fmtTime(t)} UTC${c.closed === false ? " · live" : ""}</div>`;
  html += `<div class="tt-row"><span>O</span><span class="mono">${fmt(c.o)}</span></div>`;
  html += `<div class="tt-row"><span>H</span><span class="mono">${fmt(c.h)}</span></div>`;
  html += `<div class="tt-row"><span>L</span><span class="mono">${fmt(c.l)}</span></div>`;
  html += `<div class="tt-row"><span>C</span><span class="mono">${fmt(c.c)}</span></div>`;
  html += `<div class="tt-row"><span>${c.volumeSource === "provider" ? "Vol" : "Vol proxy"}</span><span class="mono">${c.vol}</span></div>`;
  html += `<div class="tt-row"><span>State</span><span class="mono" style="color:${stCol}">${st}</span></div>`;
  if (pat) html += `<div class="tt-row"><span>Pattern</span><span class="mono">${pat.type}</span></div>`;
  if (sig) html += `<div class="tt-reason">${sig.reasons.join(" · ")}</div>`;
  tip.innerHTML = html;
  tip.style.opacity = 1;
  tip.style.left = `${Math.min(param.point.x + 16, wrap.clientWidth - 196)}px`;
  tip.style.top = `${Math.min(param.point.y + 16, wrap.clientHeight - 168)}px`;
}

function bindTooltip() {
  $("#chart").addEventListener("mouseleave", () => { $("#tooltip").style.opacity = 0; });
}

function renderAll() {
  if (!SYSTEM_DATA) return;
  renderTop();
  drawChart();
  drawRibbon();
  renderSignal();
  renderMetrics();
  drawEquity();
  renderTradesStats();
  renderLog();
}

function bindControls() {
  document.querySelectorAll("[data-tf]").forEach((btn) => {
    btn.addEventListener("click", () => {
      APP.timeframe = btn.dataset.tf;
      APP.chartReady = false;
      document.querySelectorAll("[data-tf]").forEach((b) => b.classList.toggle("active", b === btn));
      resetView(true);
      loadMarket();
      restartTimer();
    });
  });
  $("#refresh-btn").addEventListener("click", loadMarket);
}

function restartTimer() {
  clearInterval(APP.timer);
  const intervalMs = APP.timeframe === "M2" ? 120000 : APP.timeframe === "M5" ? 300000 : APP.timeframe === "M15" ? 900000 : 3600000;
  APP.timer = setInterval(loadMarket, intervalMs);
}

bindTooltip();
bindControls();
loadMarket();
restartTimer();
let rt;
window.addEventListener("resize", () => {
  clearTimeout(rt);
  rt = setTimeout(() => {
    if (!SYSTEM_DATA) return;
    if (PRICE_CHART) PRICE_CHART.resize($("#chart").clientWidth, $("#chart").clientHeight);
    drawChart();
    drawRibbon();
    drawEquity();
  }, 120);
});
