---
name: backtest-review
description: >
  Runs and interprets a walk-forward backtest of the XAU/USD engine and judges
  whether an apparent edge is real or curve-fit. Use when the user asks to
  backtest, check performance, read a report in reports/, compare in-sample vs
  out-of-sample, or decide whether a rule/strategy is worth keeping. Reports the
  full honest metric set (expectancy in R, max drawdown, longest losing streak,
  trade count, OOS gap, parameter sensitivity, costs) and calls out
  survivorship bias and look-ahead leakage rather than the flattering numbers.
---

# Backtest Review

Your job is to find out whether the edge is *real*, fully expecting the answer may
be "no" — and to make a negative result just as visible as a positive one.

## Run it right

- Use 2+ years of M30 XAU/USD covering trending, ranging, and high-vol
  (NFP/CPI/FOMC) regimes. Data in UTC; resampled from real M1/tick, not
  pre-aggregated low-TF candles.
- Model costs explicitly: spread (widening at London/NY opens), slippage,
  commission. An untested cost assumption is a blown account.
- Walk-forward, not a single in-sample fit. Keep a held-out out-of-sample tail
  the rules never saw.

## Report every metric — including the ugly ones

Never present win rate alone. Always report:

- **Expectancy per trade in R multiples.** A 40% win rate can be very profitable;
  an 80% win rate can lose money.
- **Max drawdown** and **longest losing streak** — framed as "can the human
  actually sit through this?"
- **In-sample vs out-of-sample spread.** A big gap = curve-fitting. This is the
  headline number, not an afterthought.
- **Trade count.** Under ~100 trades, the stats are noise — say so.
- **Sensitivity.** Re-run at ±10% on the key parameters. If results collapse, it's
  fragile and you say it's fragile.
- **FLAT share.** Confirm the engine is flat most of the time; a suspiciously busy
  engine usually means a rule is too loose.

## Smell tests for fake profits

- **Look-ahead leakage.** Equity curve too smooth, win rate implausibly high, or
  entries landing at exact extremes → suspect a bar-*t* peek. Trace it before
  believing anything.
- **Survivorship bias.** Remember the source is self-selected social-media wins.
  The losers don't get posted; your backtest must surface them.
- **Regime dependence.** Check whether all the profit comes from one trending
  stretch. If so, it's a regime bet, not an edge.
- **Cost sensitivity.** Re-run with spread/slippage doubled. If the edge dies, it
  was never robust.

## Verdict

End with a plain call: **keep / iterate / kill**, the single most decision-relevant
number behind it, and what specifically you'd want to see to change the verdict.
"It worked on the last seven sessions" is not validation — that's ~2 weeks of one
regime, an anecdote. Say so if that's all there is.
