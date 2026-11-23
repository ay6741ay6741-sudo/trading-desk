# Trading desk in a box (paper trading)

Research -> decision gate -> bot executes -> human on the dangerous edges.
Runs in **dry_run only**: paper money on live OKX prices. No exchange key exists anywhere in this project.

| Seat | What | Where |
|---|---|---|
| SCOUT | Funding, open interest, 24h move per pair (OKX public API), hourly | `desk/bridge.py` `scout()` |
| CHART | 24h channel breakouts, long and short, 1h candles | `user_data/strategies/DeskStrategy.py` |
| Gates | Rules-only typed decisions with probabilities + confidence | `desk/gates.py` |
| Bot | Freqtrade. The only thing that can place an order | `user_data/config.json` |
| DESK MANAGER | Schedule, reports, weekly self-tuning. Never trades | `desk/desk.py` |

## Gates (pick one option from a fixed menu)
- `check_signal` -> accept / verify_more / reject. Runs inside the bot's `confirm_trade_entry`: no accept, no trade.
- `review_trade` -> closed_as_planned / verify_more / mismatch. No fill, no done.
- `guard_action` -> allow / confirm / human_review / deny, by action level below.
- `route_task` -> SCOUT / trading_bot / stand_down / human_review. Only offers seats that exist right now.

Typed output does not mean the call is right. A gate, not an oracle.

## Action levels
| Level | Examples | Rule |
|---|---|---|
| 0 Read | prices, balances, bot status | automatic |
| 1 Prepare | plans, reports | automatic |
| 2 Reversible | pause entries, blacklist a pair | allowed + logged in dry_run, confirm when live |
| 3 Trade | bot opens a position inside its limits | paper: allowed. Live: human approval |
| 4 Money / irreversible | stake, leverage, stop, API keys, transfers, dry_run off | human, always |

Hard limits live in `user_data/config.json`, not in a prompt: dry_run, 3 trades max, 50 USDT stake, -3% stop, +6% target, force entries off, API on localhost only.
The desk bridge has no write endpoint; asking it for one raises an error.

## Schedule (server time, America/New_York)
- hourly: scout snapshot (feeds the funding check)
- 08:00 morning brief
- every 30 min: position watch, writes only when something changed (or the bot goes down/comes back)
- 21:00 evening report: signals checked, trades closed with fill review, paper P&L
- Sunday 21:30 weekly review: grades every signal decision from the last 28 days against what price did in the next 48h (+2R target first, -1R stop first, 0 neither), then moves `accept_score` in `desk/thresholds.json` one step toward the best value. Needs 10+ graded signals before it changes anything. History in `state/tuning.jsonl`.

Reports: `reports/YYYY-MM-DD.md`. Errors: `reports/errors.log`. Every gate decision: `state/decisions.jsonl`.

## Backtest (OKX futures, 1h, 2025-10-01 to 2026-11-22, 1000 USDT paper wallet)
| | trades | avg/trade | total | win rate |
|---|---|---|---|---|
| gate on | 426 | +0.29% | +5.82% | 36.9% |
| gate off | 750 | +0.09% | +3.11% | 34.4% |

Market fell 57% over the same period. Backtests are not forecasts; funding data is missing in backtests, so the funding check only works live.

## Run
Server (Ubuntu, 24/7): copy this folder, then `bash deploy/setup-server.sh`. Logs: `sudo docker compose logs -f desk`.
Local test: `.venv\Scripts\python.exe tests\test_desk.py` (gate self-checks).

Not built yet (add when needed): news feed for SCOUT, phone notifications, more pairs, live trading.
