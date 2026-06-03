# XAU/USD backtest — 20260603T064947Z

> Research output only. NOT financial advice. No live orders placed.

## Headline metrics
- Trades: **251**  (long 168 / short 83)
- Expectancy: **0.1492 R/trade**
- Win rate: 0.4502  | Profit factor: 1.2641
- Total: 37.4542 R | Max drawdown: -17.7376 R
- Longest losing streak: 9
- Outcomes: {'tp': 113, 'stop': 138, 'timeout': 0}

## FLAT frequency (should dominate)
- FLAT 0.988 | LONG 0.0055 | SHORT 0.0065

## In-sample vs out-of-sample
- IS expectancy: 0.1042 R (165 trades)
- OOS expectancy: 0.2357 R (86 trades)
- Expectancy gap (IS-OOS): -0.1315 R  (large positive gap = curve-fitting)

## ±10% parameter sensitivity
- baseline: 0.1492 R over 251 trades (total 37.4542 R)
- -10%: 0.0979 R over 239 trades (total 23.4085 R)
- +10%: 0.1207 R over 261 trades (total 31.5001 R)

## Walk-forward folds
- Fold 1: 0.1006 R over 58 trades
- Fold 2: 0.2141 R over 51 trades
- Fold 3: 0.0676 R over 63 trades
- Fold 4: 0.2081 R over 79 trades

## Notes
- Research output only — NOT financial advice, NO live orders.
- Costs modelled: spread + slippage + commission (see config).
- FLAT is the intended majority output; high trade counts are suspect.
- Walk-forward + ±10% sensitivity included; read OOS gap before trusting.
