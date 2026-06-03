---
name: add-trading-rule
description: >
  Adds or changes a trading rule in the XAU/USD signal engine following this
  repo's "definition of done". Use when the user wants to add/modify/remove a
  signal rule, a feature (S/R, liquidity, OB/FVG, sessions, volume), a filter, or
  risk logic. Enforces the order LOGIC.md → rules.py → pytest → no-lookahead
  check → walk-forward backtest, keeps `reasons` strings in sync with rule names,
  and refuses changes that relax realism (peeking ahead, dropping FLAT, removing
  spread, or raising risk above the 2% cap).
---

# Add / Change a Trading Rule

This skill encodes the AGENTS.md "Definition of done for any rule change". Follow
it in order. Do not skip steps to save time — a rule that ships without a test or
a fresh backtest is a liability, not a feature.

## Step 0 — Understand the request as a boolean

Restate the proposed rule as a condition over numeric inputs available **at or
before** the signal bar. If you cannot, it is discretionary: it goes in LOGIC.md
§20 (discretionary), not into the engine. Say so and stop.

## Step 1 — LOGIC.md first (source of truth)

- Write the rule in plain language in `LOGIC.md`, in the right section, tagged
  `[CODEABLE]`.
- Define every helper it needs (which timeframe, which levels, tolerances,
  defaults). Reuse the existing vocabulary and the §21 variable names.
- Give it a stable rule name; the `reasons` string in code must match it exactly.

## Step 2 — rules.py

- Implement it as a **small, individually-testable function**, not a branch in a
  giant conditional. Prefer pure functions: `(features) -> bool` or
  `(features) -> SignalState | None`.
- Read only closed data. Index defensively so bar *t* never touches bar *t*'s
  unclosed extremes or any bar > *t*.
- Append the matching name to `reasons`. Keep confidence flowing from the §13
  confluence count, not hard-coded.
- Never silently change `risk_per_trade` or remove the no-look-ahead guard.

## Step 3 — tests (pytest)

- Add a case in `tests/test_rules.py` with a **hand-built fixture candle series**:
  known input → known output. Cover the firing case AND a near-miss that must NOT
  fire (off-by-one on the level, missing volume, wrong session).
- Ensure `tests/test_no_lookahead.py` still passes — and add a look-ahead probe
  for the new rule if it touches new bars.
- If the rule affects sizing/stops, extend `tests/test_risk.py`.
- Run the suite. Do not proceed on red.

## Step 4 — walk-forward backtest

- Run the backtest (see the **backtest-review** skill). Use realistic spread,
  slippage, and commission — gold spreads widen at the session opens this method
  trades.
- Update `reports/`.

## Step 5 — report honestly, let the human decide

Summarise the before/after on the metrics that matter (expectancy in R, max
drawdown, longest losing streak, trade count, in-vs-out-of-sample gap, ±10%
sensitivity). State plainly whether the rule helped, hurt, or was noise. The human
reviews before merge.

## Hard refusals

If the request would improve results by relaxing realism, refuse and explain:
- peeking at future/unclosed bars,
- dropping or suppressing FLAT to force more trades,
- removing spread/slippage/commission,
- raising risk above the 2% per-trade cap,
- curve-fitting to the last handful of sessions.

These are the exact moves that produce a great backtest and a blown account.
