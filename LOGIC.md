# LOGIC.md

The trading logic, distilled into rules you can turn into code. This is the
**source of truth** for `src/rules.py`.

> **Status of this document.** These rules merge several source methods into one
> spec, organised in two parts:
>
> **Part I (§1–§23) — Structure / "where to trade".**
> 1. a **session S/R + volume** price-action method (range breakout / rejection
>    on M30, M15 entries, H1/H4/D1 bias), and
> 2. two near-identical **Smart-Money / "Alchemist"** systems (SMC + SNR +
>    liquidity + order blocks + ICT time/dealing-range concepts).
>
> **Part II (§24–§33) — Execution & management / "how to pull the trigger".**
> An intraday **M30/H1 wick-flip** discretionary method (vol-time windows, wick /
> candle-flip / break-of-prev-H-L entry triggers, a lettered setup library, tight
> pip-based exits, aggressive in-trade cutting, and operator discipline). It is an
> *execution layer* that composes with Part I: Part I picks the zone and direction,
> Part II decides the exact entry, stop, and trade management.
>
> Everything here is a **hypothesis to be backtested**, not a validated edge.
> Each rule is tagged `[CODEABLE]` (goes into the engine) or `[DISCRETIONARY]`
> (documented only, to proxy later). The two source methods agree on a lot — the
> *spine* is the session/volume method because it is already deterministic; the
> Smart-Money concepts are layered in as additional codeable features and signals
> where they can be expressed as booleans over numeric inputs, and parked in §20
> where they cannot.
>
> **Honesty note (read CLAUDE.md too):** all three sources ultimately come from
> social-media / e-book material. Posted setups are self-selected wins. Treat
> every "high-probability" claim below as unproven until the walk-forward report
> says otherwise.

---

## 1. Core idea

Two compatible framings of the same market:

- **Range framing.** Price spends most of its time inside a range bounded by a
  recent support (S) and resistance (R). Money is made when **volume pushes
  price through one side** (breakout) or when price **rejects a level with no
  follow-through** (reversion). Between levels with no momentum, do nothing.
- **Smart-Money framing.** Price is engineered to take liquidity: it
  **accumulates**, then **manipulates** (a fake move / liquidity sweep that traps
  retail), then moves in the **true direction** (distribution), then resets. We
  want to enter *with* the true move, at a refined zone, after the sweep.

These are not in conflict: a "fakeout" in the range framing is a "liquidity
sweep" in the SMC framing. The engine treats them as the same family of signals.

**Core loop (SMC):** `Accumulation → Manipulation (fake/sweep) → True Direction → Distribution → Reset`

Output per closed bar: `LONG`, `SHORT`, or `FLAT`. **FLAT is the default and the
most frequent output.**

---

## 2. Timeframe roles

| Timeframe | Role | What we read from it |
|-----------|------|----------------------|
| W1 / D1   | Bias | Major levels, dealing range (premium/discount), weekly/daily liquidity (PWH/PWL, PDH/PDL) |
| H4        | Bias / Structure | HTF trend (HH-HL vs LH-LL), major SBR/RBS & QM levels, H4 gaps |
| H1        | Structure | Active S/R for the session, intermediate POIs/order blocks |
| M30       | Primary signal | Breakout / rejection / confirmation candle, BOS/CHoCH |
| M15       | Entry refinement | Tighter entry + stop, LTF CHoCH/MSS, esp. at London/NY open |
| M5 / M1   | Precision (optional) | CE / OB / FVG refinement `[DISCRETIONARY]` until data supports it |

**Hierarchy rules (SMC):**
- Only a **higher** timeframe can break a lower timeframe's structure.
- When a timeframe's structure is broken, it can **no longer be used to confirm
  direction** until it re-forms (e.g. if M30 support breaks, stop using M30 to
  buy — defer to H1).
- A signal requires **agreement between the M30 trigger and H1 structure**, and
  must **not** fight H4/D1 bias. If H4/D1 points price into a major opposing
  level, downgrade to `FLAT`.

---

## 3. Market structure `[CODEABLE]`

Label swings from confirmed pivots only (a pivot is confirmed once `p` bars have
closed on each side; default `p = 2`). **No look-ahead:** a swing at bar *t* is
only usable once its right-hand confirmation bars have closed.

- **Uptrend:** Higher Highs (HH) + Higher Lows (HL).
- **Downtrend:** Lower Lows (LL) + Lower Highs (LH).

Structure-break events:

| Event | Meaning | Bias effect |
|-------|---------|-------------|
| **BOS** (Break of Structure) | Close beyond the last swing **in the trend direction** | Continuation — bias unchanged |
| **CHoCH** (Change of Character) | First close beyond a swing **against** the prevailing trend | Reversal warning — flip bias candidate |
| **MSS / SHIFF** (Market Structure Shift) | LTF confirmation of the directional change | Permission to look for entries in the new direction |

Rules:
- BOS confirms continuation; CHoCH signals a possible reversal; require an **LTF
  CHoCH/MSS** before taking a reversal entry at an HTF zone.
- "Break" = candle **close** beyond the level, not just a wick. (Wick-only =
  candidate liquidity sweep, see §6.)

---

## 4. Level construction & SNR `[CODEABLE]` / `[DISCRETIONARY]`

### 4.1 Range levels `[CODEABLE]`
Define the active range each session:

- **R (resistance):** highest swing high of the last `N` H1 bars (default N=20)
  with at least `k` touches within a tolerance band (default tol = 0.10% of
  price, k=2).
- **S (support):** symmetric — lowest swing low with the same touch logic.
- A "touch" = a candle whose high (for R) or low (for S) enters the tolerance
  band. Cluster nearby touches into one level.
- Maintain a small set: `levels = [S2, S1, R1, R2]`. These map to the annotated
  "potential buys above X / sells below Y" charts.
- **Mark levels by closing price, not wick.**

### 4.2 SNR freshness `[CODEABLE]`
| State | Condition |
|-------|-----------|
| Fresh | Never been traded into since creation |
| Unfresh | Touched once by a wick |
| Re-fresh | Broken through by a full candle body (becomes a flip level, see §4.3) |

- **Max 2 uses per zone** before it is considered exhausted (reduce size or skip).
- Exception: daily-gap zones that produced strong reactions may be reused.

### 4.3 Flip zones — RBS / SBR `[CODEABLE]`
- **RBS (Resistance Becomes Support):** price closes above resistance → old
  resistance becomes a **buy** retest zone. Pattern: *Break → Retest → Continue up*.
- **SBR (Support Becomes Resistance):** price closes below support → old support
  becomes a **sell** retest zone. Pattern: *Break → Retest → Continue down*.
- Higher-timeframe flip zones are stronger. This is the engine's primary
  "broken zone becomes support/resistance + retest" continuation setup (§9.5).

### 4.4 Other SNR / POI types
- **Classic V / Classic A** `[CODEABLE]` — V-bounce at support = buy; A-rejection
  at resistance = sell (i.e. a clean rejection candle at a level, see §8).
- **OCL (Open–Close Level)** `[CODEABLE]` — zone from the open/close of a
  significant HTF candle (breakout or strong reversal candle). Close = price the
  session *accepted*; next open = where new pressure starts. More reliable than
  wick lines. London/NY OCLs > Asian OCLs.
- **H4 Gap (decision level)** `[CODEABLE]` — a gap forming on H4 at a major HTF
  SNR after price runs into it, with a small LTF break in the new direction and
  the most recent swing tagging the level. Flags a likely reversal zone.
- **Head & Shoulders / Broken H&S, Gap levels, Fibonacci circles** `[DISCRETIONARY]`
  — pattern-recognition heavy; documented, not yet coded.

> **SNR validation (SMC, 3 conditions to call a zone "valid"):** price taps the
> zone with a wick, a **liquidity sweep** occurred nearby (§6), and the zone sits
> in a congestion/POI area. Codeable as a confluence count; "congestion" is the
> soft part.

---

## 5. Order Blocks, FVG & Candle Equilibrium `[CODEABLE]`

### 5.1 Order Block (OB)
The **last opposite-color candle before a strong displacement** that breaks
structure.
- **Bullish OB:** last bearish candle before a bullish displacement; OB zone =
  that candle's `low → high` (book variant: `open → low`). Mark **0.5 OB** = its
  midpoint = primary entry.
- **Bearish OB:** last bullish candle before a bearish displacement; OB zone =
  `high → low`; 0.5 OB at midpoint.
- An OB can be retouched while context holds (structure still valid, HTF bias
  unchanged, liquidity still resting beyond it).
- **OB + RBS/SBR overlap = high-probability zone.**

### 5.2 Fair Value Gap (FVG) / imbalance
A 3-candle imbalance:
- **Bullish FVG:** `candle[n-1].high < candle[n+1].low` → gap = that span.
- **Bearish FVG:** `candle[n-1].low > candle[n+1].high` → gap = that span.
- **CE (Consequent Encroachment)** = midpoint of the FVG = the level to watch for
  the retracement entry.
- Related (parked `[DISCRETIONARY]` until needed): IFVG (inverse FVG), BPR,
  Breaker Block (failed OB flipping to opposite POI), BISI.

### 5.3 Candle Equilibrium (CE of a candle)
- 45–50% of a candle's full range (incl. wicks). Bullish candle measured
  `open→low`, bearish `open→high`.
- Above CE of a bull candle → buyers in control; below → sellers gaining.
- CE that overlaps SNR/OB = **extremely strong zone**. London/NY-session CE >
  Asian CE.

> Note: two distinct "CE"s exist in the sources — *Candle* Equilibrium (this
> section) and *Consequent Encroachment* (midpoint of an FVG, §5.2). Keep them
> named distinctly in code: `candle_eq` vs `fvg_ce`.

---

## 6. Liquidity & sweeps `[CODEABLE]`

The central SMC tenet: price hunts resting orders before the real move.

### 6.1 Liquidity pools
| Type | Where it sits |
|------|---------------|
| **BSL** (Buy-side Liquidity) | Above swing highs / equal highs / resistance — sellers' stops |
| **SSL** (Sell-side Liquidity) | Below swing lows / equal lows / support — buyers' stops |
| **EQH / EQL** (Equal Highs/Lows) | ≥2 highs/lows at ~same price (tolerance band) = a pool |
| **ERL** (External Range Liquidity) | Beyond the main swing high/low — primary target |
| **IRL** (Internal Range Liquidity) | Inside the dealing range (FVG/OB/EQH/EQL) — used during manipulation |
| **PWH/PWL, PDH/PDL** | Previous week/day high/low — standing weekly/daily BSL/SSL |

### 6.2 Liquidity sweep (a.k.a. fakeout)
- **BSL sweep:** price exceeds a prior swing high by a small margin (default
  1–3 pips / a tolerance), then **closes back below** it within the same or next
  bar, with a bearish rejection → expect move **down** toward SSL.
- **SSL sweep:** mirror below a prior low → expect move **up** toward BSL.
- A sweep near/at a valid SNR or OB is the highest-quality reversal trigger. It
  is the SMC name for the §9.4 fakeout signal.

---

## 7. Session context & Quarterly Theory `[CODEABLE]`

Store all data in **UTC**; convert for session logic. (Source tables use GMT+7;
re-derive offsets for your broker — do not hard-code someone else's clock.)

Intraday phases (AMD / Quarterly Theory):

| Phase | Name | Session (intraday) | Behaviour | Action |
|-------|------|--------------------|-----------|--------|
| Q1 | Accumulation | Asia | Tight range, quiet | **Observe, bias FLAT** |
| Q2 | Manipulation | London open | Fakeouts / liquidity grabs | Watch for the trap (the sweep) |
| Q3 | Distribution | NY AM | Real direction, strongest trend | **Primary entry window** |
| Q4 | Reversal / Reset | NY PM | Reverse or form next cycle | Manage / take profit |

Nested (same shape on larger scales): Weekly → Mon=Q1, Tue=Q2, Wed=Q3, Thu=Q4;
Monthly → Week1..Week4. Use coarsely; do not over-fit to a calendar.

Engine rules:
- **Breakout signals only in the London and NY windows** (`session in
  {LONDON_OPEN, NY_OPEN}`, i.e. Q2-end / Q3). Outside them, only rejection /
  reversion signals are allowed and confidence is reduced.
- Asia (Q1) biases toward `FLAT`.
- ICT "kill zone" timing (London/NY) is one confluence point in §13.

---

## 8. Volume gate & candle confirmation `[CODEABLE]`

### 8.1 Volume gate
Zones break only with participation.
- `vol_ma = rolling mean of volume over last 20 M30 bars`.
- `volume_ok = current_bar.volume > vol_ma * 1.5` (tunable).
- A **breakout** signal is INVALID without `volume_ok` → treat the break as a
  probable fakeout/sweep (§6.2, §9.4).
- Spot-gold broker volume is unreliable: if so, proxy with candle range
  (`high - low` vs its 20-bar mean) and **document which proxy is used**.

### 8.2 Candle confirmation
Entries are taken on a **confirmation candle**, never the breaking candle alone.
Helpers on an M30 bar:
- `bull_close = close > open`; `bear_close = close < open`
- `body = abs(close - open)`
- `upper_wick = high - max(open, close)`; `lower_wick = min(open, close) - low`
- `strong_body = body > (high - low) * 0.6`
- `rejection_up = upper_wick > body * 1.5` (sellers rejected higher prices)
- `rejection_down = lower_wick > body * 1.5` (buyers rejected lower prices)
- `engulfing` = current body fully covers prior body in the signal direction.

Valid LTF confirmations: engulfing, strong close, rejection wick, or a
close-back-into-zone after a sweep.

---

## 9. The signals

Each returns a `SignalState` (see CLAUDE.md). `reasons` strings must match the
rule name.

### 9.1 Breakout LONG `[CODEABLE]`
All true:
- `session in {LONDON_OPEN, NY_OPEN}`
- prior bar **closed above** R1
- `volume_ok` on the breaking bar
- confirmation: next M30 bar `bull_close`, **or** a shallow pullback holding above
  R1 (R1 now acts as support → RBS)
- H4/D1 not sitting on major resistance directly overhead

→ `entry` = confirmation close (or M15 retest of R1) → `stop` = below confirmation
low (or below R1) → `take_profit` = next level up (R2) or fixed `2R`, nearer wins.

### 9.2 Breakout SHORT `[CODEABLE]`
Mirror of 9.1 around S1 (`bear_close`, closes below S1, S1 becomes resistance → SBR).

### 9.3 Rejection / reversion `[CODEABLE]`
- **SHORT:** price tags R1 from below, `rejection_up` candle, **no** `volume_ok`
  break → SHORT back into range; stop above R1 + buffer; TP mid-range or S1.
- **LONG:** mirror at S1 with `rejection_down`.
- This is the same shape as "Classic A / Classic V" (§4.4).

Two alignment gates required `[CODEABLE]`:

1. **D1 bias confirmation.** `rejection_short` requires `d1_bias == "BEAR"` — a
   rejection at resistance is a valid reversal only when the daily structure is
   already turning down. In a D1 uptrend or neutral, price tagging resistance is a
   *pullback*, not a reversal, and fading it loses. Mirror: `rejection_long` requires
   `d1_bias == "BULL"`.

2. **Session gate.** Both rejection rules require `session in {LONDON_OPEN, NY_OPEN}`
   (the kill zone). Rejections in NY_PM and OFF sessions have near-zero win rates
   because volume is dying and the pattern lacks institutional follow-through.

*Evidence:* walk-forward analysis showed rejection_short with D1=NEUTRAL/BULL
averaged −0.42 R; with D1=BEAR it averaged +0.11 R. Session OFF rejection_short
averaged −1.04 R (0 % win rate).

### 9.4 Fakeout / liquidity-sweep reversal `[CODEABLE]`
A fakeout = close beyond a level **without** `volume_ok`, then close back inside
within 1–2 bars (= a liquidity sweep, §6.2).
- Wick above R1 / sweep of BSL, no volume, next bar closes back below R1 → **SHORT**.
- Wick below S1 / sweep of SSL, no volume, next bar closes back above S1 → **LONG**.

Two additional gates apply to both directions `[CODEABLE]`:

1. **Zone alignment.** A `sweep_long` (SSL sweep → buy) is only valid when price is in
   the **DISCOUNT or MID zone** (i.e. `zone != "PREMIUM"`). Buying after a stop-run
   when price is already in premium territory is buying expensive and against the
   structural position of the dealing range. Mirror: `sweep_short` (BSL sweep → sell)
   is only valid when `zone != "DISCOUNT"`.

2. **D1 bias alignment.** `sweep_long` requires `d1_bias != "BEAR"` — do not buy a
   stop-run against a daily downtrend. `sweep_short` requires `d1_bias != "BULL"` —
   do not sell a stop-run against a daily uptrend. Note: this uses the *raw* D1
   structural bias, not the H4/D1 consensus, because the daily trend is the
   highest-timeframe constraint for sweep direction.

3. **London session gate for `sweep_short`.** During `LONDON_OPEN`, a BSL sweep entry
   SHORT is only valid when `htf_bias == "BEAR"` (both H4 and D1 agree bearish).
   London open carries strong directional momentum; shorting a BSL sweep there in a
   neutral or bullish multi-TF context fights the session's dominant flow.
   Walk-forward data: London sweep_short with htf_bias≠BEAR averaged −0.33 R
   (57 trades). `[CODEABLE]`

4. **M30 structure alignment for sweeps.** `[CODEABLE]`
   - `sweep_long` is blocked when `m30_trend == "BULL"`. In a bullish M30 structure
     (HH-HL), the SSL sweep is a shallow dip that often resumes upward before
     reaching the 2R target — the stop gets hit instead. The SSL sweep works best
     when it is *reversing* a bearish M30 structure. Data: sweep_long with
     m30=BULL averaged −0.21 R; with m30=BEAR it averaged +0.21 R.
   - `sweep_short` is blocked when `m30_trend == "NEUTRAL"`. In choppy/sideways
     M30 there is no directional follow-through after the BSL sweep. Swap_short
     with m30=NEUTRAL averaged −0.22 R.

5. **No-CHoCH entry for `sweep_long`.** `[CODEABLE]`
   When the signal bar itself produced a CHoCH (Change of Character — a structural
   break against the recent M30 trend), the entire reversal move has already happened
   inside that candle. Entering on the close means buying at the top of an extended
   reversal bar; the subsequent bar typically retraces and hits the stop.
   Block `sweep_long` when `choch == True` on the signal bar.
   Data: sweep_long + CHoCH=True averaged −0.39 R (23 trades, 26 % win rate).

*Why these gates exist:* walk-forward analysis on 925 trades showed sweep_long in
PREMIUM zone averaged −0.33 R (26 % win rate) and sweep_short in DISCOUNT zone
averaged −0.38 R (24 % win rate). Zone-misaligned sweeps are the single biggest
source of losses in the engine.

### 9.5 Pullback / flip-zone continuation `[CODEABLE]`
After a confirmed breakout, if price pulls back to the broken level (now RBS/SBR)
and prints a rejection/engulfing candle in the breakout direction, re-enter in
that direction.

### 9.6 Quasimodo (QM) reversal `[CODEABLE]` / `[DISCRETIONARY]`
A reversal structure that traps before turning. Codeable from swing labels:
- **Bearish QM:** `HH` → break to `LL` (CHoCH) → retrace up to an `LH` near the
  prior HH = QM sell zone → enter SHORT on retest with confirmation.
- **Bullish QM:** `LL` → break to `HH` (CHoCH) → retrace down to an `HL` near the
  prior LL = QM buy zone → enter LONG on retest with confirmation.
- **Rule:** never enter at the break — wait for the **retest** of the QM zone with
  an engulfing / pin / strong close.
- Variants (`[DISCRETIONARY]` for now): **QMM** (fakes the QM break then continues
  original direction) and **QMC** (looks like a reversal at QM but is a trap that
  continues trend). Both require "there must be a fake/liquidity grab before the
  real move" — proxy via §6.2 once the base QM is validated.

### 9.7 SMC reversal playbook (confluence entry) `[CODEABLE]`
The unifying entry model behind 9.3–9.6. **BUY** version (mirror for SELL):
1. HTF bias up (or CHoCH up) and price in the **discount** half of the dealing
   range (§10).
2. Price **sweeps SSL** (takes a prior low / EQL) and reverses.
3. Price taps an HTF **POI**: RBS / bullish QM / OCL support / Classic V.
4. Within the POI, price reaches a refined level: 0.5 OB / FVG CE / candle CE.
5. **LTF CHoCH/MSS** confirms (M15/M5).
6. Enter on the confirmation candle.
   → `stop` = below the sweep low / below the OB.
   → `take_profit` = opposite liquidity (BSL): nearest IRL → swing high → EQH →
     ERL → PDH/PWH, in that order (§14).

---

## 10. Dealing range — premium / discount `[CODEABLE]`

From the active swing range:
```
DRH = range high, DRL = range low
premium  = upper half (0.0–0.5 from the top)  → SELL area (expensive)
discount = lower half (0.5–1.0)               → BUY area (cheap)
midpoint = 0.5 (equilibrium / fair value)
```
- In **discount** → only look for LONGs. In **premium** → only look for SHORTs.
- At the midpoint → wait for direction confirmation.
- Combine with HTF bias: discount + bullish HTF = strongest buy context.

---

## 11. Fibonacci `[CODEABLE]` (core) / `[DISCRETIONARY]` (advanced)

- Draw **low→high** for bullish setups, **high→low** for bearish.
- Core retracement levels: `0, 0.382, 0.5, 0.618, 0.786, 1.0`. **0.5 = CE /
  equilibrium**, the primary retracement entry zone (pairs with §5/§10).
- Extension targets: `1.272, 1.414, 1.618, 2.0, 2.618` (and the method's quoted
  `-0.19 … -2.56` negative-extension TPs — treat as TP candidates, validate).
- **Fibonacci circles** and the long custom level lists are `[DISCRETIONARY]`.
- Use Fib as a **secondary** confirmation only — never as a standalone trigger.

---

## 12. No-trade zone (force FLAT) `[CODEABLE]`

Return `FLAT` when:
- price is between S1 and R1 with no level interaction this bar, **or**
- `not volume_ok` and no rejection/sweep pattern, **or**
- the entry candle is the 4th+ consecutive candle same direction (momentum likely
  exhausted), **or**
- distance to the nearest opposing level < intended stop distance (reward too
  small), **or**
- price is at dealing-range midpoint with no confirmation (§10), **or**
- it is Q1 / Asia — return `FLAT` for **all** signals including sweeps. Asia volume is
  thin; stop-runs in this session are noise rather than institutional order flow.
  Walk-forward data: 307 Asia trades at −0.25 R average. `[CODEABLE]`

The engine should be `FLAT` the majority of the time. **That is correct, not a bug.**

---

## 13. Confluence & confidence `[CODEABLE]`

Score the confluences present, then map to `confidence` and a gate.

| Condition | Points |
|-----------|--------|
| Price in correct dealing zone (premium/discount) | +1 |
| HTF structure aligned with trade direction | +1 |
| Liquidity swept before entry | +2 |
| HTF POI confirmed (QM / RBS / SBR / OCL / H4 gap) | +2 |
| FVG / OB / CE present within the POI | +1 |
| LTF CHoCH / MSS confirmed | +2 |
| Fibonacci 0.5 alignment | +1 |
| Session in kill zone (London / NY = Q2-end/Q3) | +1 |
| Volume_ok (for breakouts) / strong confirmation candle | +1 |
| Clean range (≥ ~40 pip room to target) | +1 |
| Trendline confluence at zone `[DISCRETIONARY]` | +1 |

- `confidence = fired_points / max_points` (0..1).
- Suggested gates to test (do **not** treat as proven): **enter ≥ 7/12**, **high
  confidence ≥ 9/12**. Use confidence for sizing tiers and backtest filtering.

---

## 14. Targets / take-profit logic `[CODEABLE]`

Priority ladder (BUY; mirror for SELL):
1. **IRL** — nearest internal liquidity (FVG / OB) ahead.
2. **Swing high** — last significant high.
3. **EQH** — equal-highs cluster.
4. **ERL** — external range liquidity (BSL beyond the main range).
5. **PDH / PWH** — daily / weekly high.

Take the nearer of (next ladder level) and a fixed `2R`. Document which was used
per trade in `reasons`.

---

## 15. Higher-timeframe veto & invalidation `[CODEABLE]`

**Veto (before emitting any directional signal):**
- If D1/H4 shows price entering a **major rejection zone in the trade's direction**
  within `2 * stop_distance`, downgrade to `FLAT` (the "coming into a massive
  Daily rejection, so I closed early" lesson).
- Never trade against H4/D1 bias. **D1 is the highest-level arbiter.**
  - If H4 and D1 both agree on a direction → use that direction as the bias.
  - If H4 is NEUTRAL → defer to D1.
  - If D1 is NEUTRAL → defer to H4.
  - If H4 and D1 **disagree** (e.g. H4=BEAR inside a D1=BULL uptrend) → consensus
    bias is **NEUTRAL**: this is a pullback condition. The HTF veto does NOT fire;
    instead the directional rules' own zone and pattern requirements (discount
    zone, sweep/rejection confirmation) do the filtering.
  - Codeable: `htf_bias = consensus(h4_trend, d1_trend)` where `d1_bias` is also
    stored in `FeatureSnapshot` for the audit log. `[CODEABLE]`

**Invalidation (exit / don't enter):**
- BUY invalid if price **closes** below the POI / 0.5 OB, LTF makes a new LL after
  the CHoCH, or HTF closes below the dealing-range low.
- SELL invalid if price closes above the POI / 0.5 OB, LTF makes a new HH after
  the CHoCH, or HTF closes above the dealing-range high.
- "Close beyond", not "wick beyond" — a wick beyond may be a sweep (§6.2).

---

## 16. Risk & exits `[CODEABLE]` (+ one `[DISCRETIONARY]` piece)

- `risk_per_trade = 0.01` of equity (**hard cap 0.02**). Never silently change this.
- `size = (equity * risk_per_trade) / (stop_distance_in_price * contract_value)`.
- Initial stop per the signal definition (below confirmation low / below 0.5 OB /
  below sweep low; mirror for shorts).
- **Minimum RR ≈ 1:1.5** to take a trade (the source's "1:15" is read as 1:1.5).
- **Partial + breakeven:** take partial at `+1R`, move stop to breakeven, ride the
  rest risk-free.
- Reduce size on unfresh zones or multiple OB touches (§4.2, §5.1).
- **Active exit** `[DISCRETIONARY]`: if the thesis breaks before stop (opposing
  confirmation candle at your level), close manually. Hard to code well — backtest
  the **fixed-stop** version first, then experiment.

---

## 17. Common mistakes to avoid (encode as guards)

1. Entering before the confirmation **close**.
2. Trading lines only, ignoring liquidity / OB context.
3. Trading **against** H4/D1 bias.
4. Chasing price at the end of Q3 (exhaustion, §12 consecutive-candle guard).
5. Acting on SMT divergence without a BOS/CHoCH confirmation.
6. Using a **broken** timeframe to confirm direction (§2).
7. Treating every QM as a reversal without HTF confirmation.

---

## 18. Pseudocode (the whole thing)

```python
def evaluate(bar, frames) -> SignalState:
    H1, M30, M15, H4, D1 = frames["H1"], frames["M30"], frames["M15"], frames["H4"], frames["D1"]
    S1, R1, S2, R2 = build_levels(H1, bar.time)        # §4.1
    drh, drl = dealing_range(H1, bar.time)             # §10
    zone     = premium_discount(bar.close, drh, drl)   # §10
    sess     = session_of(bar.time)                    # §7
    bias     = htf_bias(H4, D1, bar.time)              # §2/§3
    vol_ok   = M30.volume[bar] > 1.5 * vol_ma(M30, bar)  # §8.1
    reasons  = []

    # --- vetoes first (§12, §15) ---
    if in_no_trade_zone(bar, S1, R1, vol_ok, zone, sess):
        return FLAT(bar.time, reasons=["no_trade_zone"])
    if htf_rejection_ahead(D1, H4, proposed_dir):
        return FLAT(bar.time, reasons=["htf_veto"])

    # --- breakouts: London/NY only, with volume (§9.1–9.2) ---
    if sess in (LONDON_OPEN, NY_OPEN):
        if broke_above(R1) and vol_ok and confirms_bull(M30, bar) and bias != "BEAR":
            return LONG(entry, stop_below_conf, tp_ladder("LONG"), conf, reasons+["breakout_long"])
        if broke_below(S1) and vol_ok and confirms_bear(M30, bar) and bias != "BULL":
            return SHORT(entry, stop_above_conf, tp_ladder("SHORT"), conf, reasons+["breakout_short"])

    # --- rejections / fakeouts / sweeps: any session (§9.3–9.4) ---
    if at_level(R1) and rejection_up(M30, bar) and not vol_ok and zone == "PREMIUM":
        return SHORT(..., reasons+["rejection_short"])
    if at_level(S1) and rejection_down(M30, bar) and not vol_ok and zone == "DISCOUNT":
        return LONG(..., reasons+["rejection_long"])
    if swept_bsl(R1) and closed_back_below(R1):   # fakeout / liquidity sweep
        return SHORT(..., reasons+["sweep_short"])
    if swept_ssl(S1) and closed_back_above(S1):
        return LONG(..., reasons+["sweep_long"])

    # --- SMC reversal playbook (confluence-gated, §9.7) ---
    setup = smc_reversal(bias, zone, sweep, poi, refine, ltf_choch)  # §9.7
    if setup and confluence_score(setup) >= 7:
        return setup.to_signal(reasons + setup.reasons)

    return FLAT(bar.time, reasons=["no_setup"])
```

All confidence values come from §13; all stops/targets from §14 and §16; no
function may read any candle that has not closed at or before `bar` (CLAUDE.md
hard rule #1).

---

## 19. SMT divergence `[DISCRETIONARY]`

Two correlated assets (esp. **XAUUSD vs XAGUSD**) diverging at the end of a move:
"the side that doesn't fake is where the real strength is." Requires: occurs near
the **end** of a trend, includes a liquidity grab, the non-faking side shows a
BOS, and the faking side returns inside its range before entry. Useful as a manual
confluence; left out of the engine until we have aligned multi-asset data and can
test it without look-ahead.

---

## 20. Discretionary pieces we are NOT pretending to code yet `[DISCRETIONARY]`

- "Feel" for whether volume / a sweep is **institutional** vs noise.
- News interpretation (NFP / CPI / FOMC directional read).
- Clean vs messy range judgment beyond the ~40-pip proxy.
- **Trendlines** (regular / breakout / divergence), X-Factor confluence, QMX/QML/
  SHIFG trendline combos — drawing valid lines is subjective.
- **Fibonacci circles** and the extended custom level sets.
- QMM / QMC trap variants beyond the base QM.
- Deciding to hold a winner past the next liquidity target.

Document outcomes whenever we try to proxy any of these; until then they stay out
of the engine.

---

## 21. Variables for automation / scanning `[CODEABLE]`

| Variable | Description | Source |
|----------|-------------|--------|
| `htf_bias` | BULL / BEAR / NEUTRAL from H4/D1 | §2, §3 |
| `levels` | `[S2, S1, R1, R2]` | §4.1 |
| `snr_fresh` | fresh / unfresh / re-fresh per zone | §4.2 |
| `flip_zone` | RBS / SBR level (price, side) | §4.3 |
| `dealing_range` | DRH, DRL, midpoint | §10 |
| `zone` | PREMIUM / DISCOUNT / MID | §10 |
| `ob_zone`, `ob_mid` | order block hi/lo and 0.5 OB | §5.1 |
| `fvg_zone`, `fvg_ce` | fair value gap + its midpoint | §5.2 |
| `candle_eq` | 45–50% of a key candle's range | §5.3 |
| `liquidity` | BSL/SSL/EQH/EQL/ERL/IRL, PWH/PWL/PDH/PDL | §6 |
| `swept` | bool — sweep present near zone? | §6.2 |
| `qt_phase` | Q1/Q2/Q3/Q4 from session clock | §7 |
| `session` | ASIA / LONDON_OPEN / NY_OPEN / NY_PM | §7 |
| `volume_ok` | volume (or range proxy) > 1.5× MA | §8.1 |
| `confirm` | engulfing / strong-close / rejection / close-back-in | §8.2 |
| `bos`, `choch`, `mss` | structure-break flags | §3 |
| `fib_level` | nearest Fib level at zone | §11 |
| `confluence` | score 0–12 → confidence | §13 |
| `tp_ladder` | ordered target list | §14 |

---

## 22. Glossary

| Term | Definition |
|------|-----------|
| HTF / LTF | Higher / Lower timeframe |
| HH/HL/LH/LL | Higher High / Higher Low / Lower High / Lower Low |
| BOS | Break of Structure (continuation) |
| CHoCH | Change of Character (reversal warning) |
| MSS / SHIFF | Market Structure Shift (LTF confirmation of change) |
| SNR | Support & Resistance |
| RBS / SBR | Resistance-Becomes-Support / Support-Becomes-Resistance (flip zones) |
| POI / AOI | Point / Area of Interest (entry zone) |
| OB / 0.5 OB | Order Block / its midpoint (fair value of the zone) |
| FVG / IFVG | Fair Value Gap / Inverse FVG (imbalance) |
| CE | Two meanings: **Candle Equilibrium** (45–50% of a candle) and **Consequent Encroachment** (50% of an FVG). Code as `candle_eq` / `fvg_ce` |
| OCL | Open–Close Level (zone from a candle's open/close) |
| QM / QMM / QMC | Quasimodo reversal / manipulation / continuation |
| BSL / SSL | Buy-side / Sell-side Liquidity |
| EQH / EQL | Equal Highs / Equal Lows |
| ERL / IRL | External / Internal Range Liquidity |
| PWH/PWL, PDH/PDL | Previous Week / Day High & Low |
| DRH / DRL | Dealing Range High / Low |
| Premium / Discount | Upper (sell) / lower (buy) half of the dealing range |
| AMD | Accumulation–Manipulation–Distribution (with Q4 reset) |
| QT | Quarterly Theory (Q1–Q4 time cycles) |
| SMT | Smart Money Tool — correlated-asset divergence |
| DOL | Draw on Liquidity (the price target) |
| Sweep / Fakeout | Wick beyond a level taking stops, then close back inside |
| TL | Trendline |

---

## 23. First experiment to run

1. Implement the **spine** only: §3 structure, §4.1–4.3 levels/flips, §7 sessions,
   §8 volume + confirmation, §9.1–9.5 (breakouts, rejections, fakeouts, pullback),
   §10 premium/discount, §12 no-trade, §15 veto/invalidation, §16 fixed risk.
2. Add the SMC layer (§5 OB/FVG, §6 liquidity, §9.6–9.7 QM + reversal playbook,
   §13 confluence scoring) as a **second pass**, gated behind its own flag so you
   can measure whether it adds expectancy or just complexity.
3. Backtest on 2+ years of M30 XAUUSD with realistic spread / slippage / commission.
4. Read **expectancy (R)**, **max drawdown**, **longest losing streak**,
   **trade count**, and the **in-sample vs out-of-sample gap**. Re-run at ±10%
   parameters for sensitivity.
5. Decide from the numbers — not from the screenshots or the e-books — whether the
   edge is real. A negative result is a valid, valuable outcome.

---

## 24. BIGEY execution layer: scope and hierarchy

This section merges the non-duplicate knowledge from `LOGIC1.md` and `LOGIC2.md`.
It is an execution and operator-discipline layer on top of §1–§23, not a separate
strategy. Part I answers **where and in which direction** to trade; this layer
answers **when to pull the trigger, where to place the stop, when to cut, and
when the human should stop trading for the day**.

Core hierarchy:
- Entry timeframes are **M30/H1 only**; M15 can refine wick/volume context, and
  M5 can help with manual cut decisions. Do not add lower-TF precision until the
  data path is clean enough to test it without look-ahead.
- Daily flip + H4 flip set the session bias. M30/H1 S/R zones define the actual
  trade location. This is consistent with §2 and §15.
- A valid execution must align with the current session flow unless it is an
  explicitly labelled counter setup (§29.5), and counter setups require S and R
  to form first.
- "Clean left" means there is clear room to the target with no nearby H1/H4 zone
  blocking price. Proxy this as `room_to_next_htf_zone >= max(40 pips,
  stop_distance)`.
- If an H1/H4 zone is nearby in the trade direction, force `FLAT`; this is the
  BIGEY version of the HTF veto in §15.

---

## 25. Vol-time window and day guardrails `[CODEABLE]` / `[DISCRETIONARY]`

The sources give session times in GMT. Store data in UTC as required by the repo,
then convert source-time windows to the broker/session calendar under test. Do
not hard-code a social-media clock without verifying the broker feed.

| Source GMT window | Use | Engine stance |
|-------------------|-----|---------------|
| 07:00–07:15 | Pre-London prep | Mental check / mark zones only `[DISCRETIONARY]` |
| 07:30–08:00 | London setup prep | Plan; avoid early impulse entries unless A+ |
| 08:00–08:45 | London peak | Primary vol-time execution window |
| 09:00–09:15 | Late London | Last-chance London window; reduce selectivity threshold only if tested |
| 09:30–11:30 | Dead zone | No new trades; journal / reset `[DISCRETIONARY]` |
| 11:30–14:00 | Pre-NY + NY | Primary NY execution window; 12:00 and 13:00 candles are key |
| 14:00+ | Done | No new discretionary trading |

Day-level guardrails:
- Max 4 trades per day.
- Max 2 London trades.
- Max 2 losses per day; after 2 losses, no more trades that day.
- No trading during major scheduled news events. News direction is
  `[DISCRETIONARY]`, but the event-time blockout is `[CODEABLE]` once an economic
  calendar feed exists.
- Max chart time is 4.5 hours; this is an operator rule, not a signal rule.

---

## 26. BIGEY hard filters: force `FLAT` before setup scoring `[CODEABLE]`

Apply these before evaluating the Part II setup library:

- Not in vol time for breakout / impulse setups.
- H1 or H4 zone too close in the trade direction.
- Entry candle is the 4th or 5th consecutive motion candle in the same direction.
- Wick has already retraced more than 50% against the intended entry.
- Stop placement is too far for fixed-fractional risk, or the trade cannot meet
  the repo risk cap in §16.
- Price has already pushed about 150 pips or more without a pullback; wait for a
  pullback or new S/R to form.
- Candle closed far from S/R and did not retrace; wait for an A+ continuation or
  a pullback.
- M15/M30 volume proxy is weak: small-body closures, no range expansion, or no
  participation relative to recent bars.
- Candle just opened; first 1–2 minutes are not enough information for a
  closed-candle engine. For automation, this means evaluate only closed candles.
- Trend/volume flow is opposite the desired entry direction, unless the setup is
  an explicit counter setup with S and R both formed.
- Left side is messy or target room is less than the clean-range proxy in §24.

Operator-only no-trade filters `[DISCRETIONARY]`: emotional state is poor,
operator is stressed, or the session plan was not written before execution.

---

## 27. Flow, big pushes, and trend context `[CODEABLE]`

Flow is the execution-layer version of bias:
- **Bullish flow:** M30/H1 HH-HL, strong bullish candles dominate, support is
  respected, pullbacks are shallow.
- **Bearish flow:** M30/H1 LH-LL, strong bearish candles dominate, resistance is
  respected, rallies are rejected.
- **Strong flow:** directional momentum candles with clean left-side room.
- **Weak flow:** mixed candles, small bodies, chop, or repeated failure to extend.
  Weak flow means half risk or `FLAT` until tested.

After an extended push:
- If price has moved about **200 pips or more** bearish on M30/H1 full candles,
  do not buy just because support formed. Wait for both S and R to form and watch
  which side breaks. Valid outcomes: counter buy after bearish volume dies and R
  breaks, or pullback sell if S breaks again.
- Mirror for a 200-pip bullish push: do not sell just because resistance formed.
  Valid outcomes: counter sell after bullish volume dies and S breaks, or
  pullback buy if R breaks again.
- The 150-pip rule in §26 is a chase filter; the 200-pip rule is a post-extension
  scenario map.

---

## 28. Entry trigger mechanics `[CODEABLE]`

Every BIGEY setup reduces to one of two entry triggers after the location and
flow checks pass:

1. **Candle flip:** after a wick forms, the next candle flips/closes in the trade
   direction.
2. **Break of previous high/low:** for buys, the next candle breaks the previous
   candle high (`BOPCH`); for sells, it breaks the previous candle low (`BOPCL`).

Wick rules:
- Small wick: wait for `BOPCH`/`BOPCL`.
- Long wick: enter only on the flip.
- Wick must have at least 10–15 pips formed before it is treated as valid.
- If a long wick breaks the previous low during a buy idea, wait for a reflip
  back with trend, then enter on the re-break or the candle's own high break.
  Mirror for sells.
- First break is a warning; prefer the **re-break** for entry.
- No wick plus a valid high/low break can still be valid; stop goes at the current
  low/high.
- If the M30 second half / M15 refinement candle has no wick, use half risk until
  backtested.
- Entering on a wick requires session trend and volume to align; once the candle
  flips in profit, move to breakeven per §30.

---

## 29. BIGEY setup library, de-duplicated against §9 `[CODEABLE]`

The setup names below are stable `reasons` candidates. They refine existing §9
families rather than replacing them.

### 29.1 Breakout setups

- `bigey_breakout_a_plus`: clean left, close above R / below S, then a weak
  opposing close or continuation close on the broken side. Entry is
  `BOPCH`/`BOPCL` after wick forms.
- `bigey_breakout_impulse`: bullish close into R for buy, bearish close into S
  for sell, valid only in vol time. Requires weak opposing response at the level.
- `bigey_breakout_small_body`: previous candle is a small-body close above R or
  below S; enter on next `BOPCH`/`BOPCL`.
- `bigey_breakout_wickfill`: candle closes beyond the level with a large left
  wick in the trend direction; enter on next flip and target the wick-fill area.
- `bigey_breakout_big_body`: large body closes beyond S/R with strong flow; enter
  on next flip, trail stop below/above entry candle, secure 25–30 pips.
- `bigey_breakout_defended`: level is defended first, then price breaks and
  closes beyond it; enter on next flip.
- `bigey_celery_play`: A+ vol-time impulse break where the entry exceeds the high
  for buys or low for sells with strong directional volume. Keep separate until
  enough examples prove it is not just the impulse breakout renamed.

### 29.2 Pullback setups

- `bigey_pullback_snr_formed`: trend continuation after price pulls back to old
  R now support / old S now resistance and the new S/R forms.
- `bigey_pullback_impulse_a_plus`: weak opposing close plus minor S/R formed
  during the pullback; highest-grade pullback variant.
- `bigey_pullback_impulse`: weak body plus exhaustion wick; may occur without a
  formal S/R level, so require stronger flow and smaller size until tested.
- `bigey_pullback_wickfill`: pullback forms support/resistance with a huge
  wick-fill candle; enter on next flip.

### 29.3 S/R bounce setups

- `bigey_snr_buy_sell`: price reaches M30/H1 support/resistance, forms the level,
  then enters on next flip.
- `bigey_snr_impulse`: weak body closure at S/R with exhaustion wick; enter on
  next `BOPCH`/`BOPCL`.
- `bigey_flow_a_plus`: strong flow plus weak opposing close, no fresh S/R
  required. Treat as lower priority until it is proven because it has less
  location structure than the other setups.

### 29.4 Fakeout and closure-back-in-range setups

- `bigey_fakeout_buy`: price sweeps below support, closes back inside the range,
  and the bullish body is stronger than the previous bearish body.
- `bigey_fakeout_sell`: mirror above resistance with strong bearish close back
  inside the range.
- `bigey_closure_back_in_range`: generic close-back-in after a failed break;
  maps to §9.4 liquidity sweep and should share the same sweep variables.

### 29.5 Counter setups

- `bigey_counter_buy`: strong bearish flow fails to break support, bearish volume
  dies, both S and R form, then price breaks R.
- `bigey_counter_sell`: strong bullish flow fails to break resistance, bullish
  volume dies, both S and R form, then price breaks S.
- Never enter counter setups before both S and R exist.

### 29.6 Wick-fill-in-range setups

- `bigey_wickfill_mid_range`: in a range, wick forms in the middle with session
  flow; enter on next flip and target the wick-fill / opposite side of range.
- `bigey_wickfill_rejected_level`: wick rejects from S/R; wait for a second
  candle confirming volume in the trade direction before entry.

### 29.7 After one big body breakout

After one extended breakout candle, do not chase the first close blindly. Choose
one of three paths:
- **Path A:** wait for the second candle, then look for A+ impulse continuation.
- **Path B:** wait for range consolidation, then trade the range breakout.
- **Path C:** wait for pullback and new S/R, then trade the pullback continuation.

---

## 30. BIGEY stops, targets, and active management `[CODEABLE]`

These rules are stricter and more tactical than §16; use them only after a
separate Part II backtest.

Stops:
- Buy stop below the entry candle low, or below the recent low if the entry wick
  is long.
- Sell stop above the entry candle high, or above the recent high if the entry
  wick is long.
- Big-body breakout buy can trail below the entry candle rather than using a
  static stop; mirror for sells if tested.
- If the stop would exceed the fixed-fractional risk cap, reduce size or skip.

Profit-taking:
- Secure 75–90% of the position at 25–30 pips when volatility supports it.
- After that partial, move to breakeven and leave a runner toward the Part I
  target ladder in §14.
- Even when stop distance is more than 40 pips, the first secured profit should
  still be at least 25 pips if the trade is taken at all. Backtest this carefully:
  it can conflict with R-multiple expectancy.
- Wick-fill setup target is the wick-fill area.

Active cut rules:
- At +15 pips, move to breakeven or place a partial stop with 50% protected at
  entry.
- If price reaches 6–7 pips profit then stalls, stop should be at breakeven for
  the protected portion.
- If price struggles to push after entry, cut 50–75%.
- If price breaks the previous high/low in the trade direction and then returns
  to entry, cut 50–75%.
- If the entry candle reflips against the trade, cut 75%.
- If the candle's own low/high breaks against the trade, cut 75% or the full
  position.
- If entered on a wick, move to breakeven as soon as the candle flips in profit.
- Long wick plus large stop: if price retraces more than 50% of the wick again,
  cut.
- If 15-minute candles close opposite the thesis after entry, exit.
- M5 can be used for manual cut decisions, but keep the automated backtest on
  closed candles first.

---

## 31. Setup priority and sizing tiers `[CODEABLE]`

Use these as initial hypotheses only; they must earn their place in the walk-
forward report.

| Grade | Setups | Initial sizing stance |
|-------|--------|-----------------------|
| A+ | A+ breakout, impulse A+, celery, highest-grade pullback impulse | Full configured risk if all §26 filters pass |
| A | Big-body breakout, defended breakout, confirmed counter with S/R | Normal or slightly reduced risk |
| B | Pullback S/R formed, wick-fill, fakeout | Half to normal risk depending on confluence |
| C | Generic closure above/below, small-body breakout | Half risk or wait for extra confirmation |

Never let grade override the hard risk cap in §16 or the `FLAT` filters in §26.

---

## 32. BIGEY journaling and operator protocol `[DISCRETIONARY]`

This is not signal logic, but it protects the human from overtrading the signal.

After every winning trade:
- Was the entry correct, or was there a cleaner one?
- Could the stop have been smaller without violating structure?
- Was the partial taken at the planned place?
- Did the runner follow the target ladder or become emotional holding?

After every losing trade:
- Did the trade have Daily/H4 agreement?
- Did every §26 checklist item pass?
- Was 50–75% cut when the active-management rule required it?
- Was the loss a valid expense of the edge or a process mistake?

Weekend review:
- Review all trades from the week.
- Identify which setup names performed best and worst.
- Separate execution mistakes from rule failure.
- Recalibrate watchlist levels and session behaviour for the next week.

Mindset principles to preserve in the research process:
- Every trade is one event in a series; do not revenge trade.
- The analysis is always a probability statement, never certainty.
- A losing trade is data if the plan was followed.
- No trade is better than a forced trade.

---

## 33. BIGEY variables to add when automating Part II `[CODEABLE]`

| Variable | Description |
|----------|-------------|
| `vol_time` | bool/session label from §25 |
| `session_flow` | BULL / BEAR / MIXED from M30/H1 flow (§27) |
| `clean_left_room` | distance to next blocking H1/H4 level |
| `motion_count` | consecutive same-direction candles before entry |
| `push_distance_pips` | distance moved since last pullback / S/R reset |
| `wick_pips` | formed wick size on entry/setup candle |
| `wick_retrace_pct` | retrace against the formed wick |
| `entry_trigger` | `flip`, `bopch`, `bopcl`, or `rebreak` |
| `setup_grade` | A+ / A / B / C from §31 |
| `daily_trade_count` | trades taken in current session day |
| `daily_loss_count` | realised losing trades in current session day |
| `partial_taken` | whether 25–30 pip partial or +1R partial was executed |
| `active_cut_reason` | matched cut rule from §30, if any |

Part II experiment:
1. Backtest Part I alone.
2. Add only §26 hard filters plus §28 entry triggers.
3. Add setup names from §29 one family at a time.
4. Add §30 active management last, because tactical cuts can hide whether entry
   logic has an edge.
5. Compare expectancy in R, max drawdown, longest losing streak, trade count,
   OOS gap, and ±10% sensitivity after every layer.
