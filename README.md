# Trading desk in a box (paper trading, free, 24/7)

Research -> decision gate -> paper engine executes -> human on the dangerous edges.
**Paper money only** on live OKX prices. No exchange account or key exists anywhere in this project.
Runs every hour on GitHub Actions (free), state and reports are committed back to this repo.

**Check on it:** open [`reports/STATUS.md`](reports/STATUS.md) (wallet, open trades, last 10 closed).
Daily notes: `reports/YYYY-MM-DD.md`. Errors: `reports/errors.log`.

| Seat | What | Where |
|---|---|---|
| SCOUT | Funding, open interest, 24h move per pair (OKX public API) | `desk/bridge.py` |
| CHART | 24h channel breakouts on 1h candles, long and short | `desk/engine.py` |
| Gates | Rules-only typed decisions with probabilities + confidence | `desk/gates.py` |
| Engine | Paper ledger. The only thing that can open a (paper) trade | `desk/engine.py` + `desk/limits.json` |
| DESK MANAGER | Hourly run, reports, weekly self-tuning. Never trades | `desk/desk.py` |

## Gates (pick one option from a fixed menu)
- `check_signal` -> accept / verify_more / reject. Every breakout passes it; no accept, no trade.
- `review_trade` -> closed_as_planned / verify_more / mismatch. No fill, no done.
- `guard_action` -> allow / confirm / human_review / deny by action level. The engine asks it before every open.
- `route_task` -> SCOUT / trading_bot / stand_down / human_review. Only offers seats that exist right now.

Typed output does not mean the call is right. A gate, not an oracle.

## Action levels
| Level | Examples | Rule |
|---|---|---|
| 0 Read | prices, funding, status | automatic |
| 1 Prepare | plans, reports | automatic |
| 2 Reversible | pause entries, blacklist a pair | allowed + logged on paper, confirm when live |
| 3 Trade | open a position inside the limits | paper: allowed. Live: human approval |
| 4 Money / irreversible | stake, leverage, stop, keys, transfers, going live | human, always |

Hard limits live in `desk/limits.json` (paper_only, 3 pairs, 1000 USDT wallet, 50 USDT stake, 3 trades max, -3% stop, +6% target, 0.05% fee per side). Self-tuning never edits it. Setting `paper_only` to false makes every trade `human_review`, so the engine stops opening trades.

## Schedule (New York time)
- every hour, 3 min after the candle closes: scout, then the engine walks every candle since the last run (stops and targets are checked candle by candle, so a late run misses nothing; entries only on the newest candle)
- 08:00 morning brief, 21:00 evening report (signals checked, trades closed with fill review, P&L)
- Sunday 21:00 weekly review: grades every signal from the last 28 days against the next 48h (+2R target first, -1R stop first, 0 neither), then moves `accept_score` in `desk/thresholds.json` one step toward the best value. Needs 10+ graded signals before changing anything. History: `state/tuning.jsonl`.

## Backtest (OKX futures 1h, 2025-10-01 to 2026-11-22, same engine code)
| | trades | avg/trade | total | win rate |
|---|---|---|---|---|
| gate on | 425 | +0.28% | +5.97% | 36.9% |
| gate off | 749 | +0.08% | +3.03% | 34.6% |

Market fell 57% in that period. Freqtrade on the same rules gave 426 trades / +5.82%, so the engine matches.
Backtests are not forecasts. Funding data is missing in backtests, so the funding check only works live. Fills assume candle close / exact stop, no slippage.

## Local
- self-checks: `python tests/test_desk.py`
- one run now: `python -m desk.desk`
- backtest (needs `.venv` with pandas + downloaded data): `.venv\Scripts\python.exe tests\backtest.py [off]`

Limits of the free host: GitHub can start scheduled runs 5-20 min late, and may pause workflows used as an app host. Not built yet: news feed, phone alerts, more pairs, live trading.
