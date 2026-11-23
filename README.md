# Trading desk in a box (paper trading, free, 24/7)

Research -> decision gate -> paper engine executes -> human on the dangerous edges.
**Trades SOL only, in US dollars** (Coinbase SOL-USD prices). **Paper money only.** No exchange account or key exists anywhere in this project.
Runs every hour on GitHub Actions (free), state and reports are committed back to this repo.

**Dashboard (live, animated):** https://ay6741ay6741-sudo.github.io/trading-desk/
Live SOL price streams from Coinbase in the browser; desk data (`state/dashboard.json`) refreshes after every hourly run.
Plain-text status: [`reports/STATUS.md`](reports/STATUS.md).
Daily notes: `reports/YYYY-MM-DD.md`. Errors: `reports/errors.log`.

| Seat | What | Where |
|---|---|---|
| SCOUT | SOL USD price + 24h move (Coinbase), perp funding + open interest as sentiment (OKX) | `desk/bridge.py` |
| CHART | 24h channel breakouts on SOL-USD 1h candles, long and short (paper shorts); BTC-USD as regime filter | `desk/engine.py` |
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

Hard limits live in `desk/limits.json` (paper_only, SOL/USD only, $1,000 wallet, $50 per trade, 1 trade at a time, -3% stop, +6% target, 0.05% fee per side). Self-tuning never edits it. Setting `paper_only` to false makes every trade `human_review`, so the engine stops opening trades.

## Schedule (New York time)
- every hour, 3 min after the candle closes: scout, then the engine walks every candle since the last run (stops and targets are checked candle by candle, so a late run misses nothing; entries only on the newest candle)
- 08:00 morning brief, 21:00 evening report (signals checked, trades closed with fill review, P&L)
- Sunday 21:00 weekly review: grades every signal from the last 28 days against the next 48h (+2R target first, -1R stop first, 0 neither), then moves `accept_score` in `desk/thresholds.json` one step toward the best value. Needs 10+ graded signals before changing anything. History: `state/tuning.jsonl`.

## Backtest (SOL only, 1h, 2025-10-01 to 2026-11-22, same engine code, $1,000 wallet, $50 per trade)
| | trades | avg/trade | total | win rate |
|---|---|---|---|---|
| gate on | 154 | +0.34% | +$26.35 (+2.63%) | 38.3% |
| gate off | 271 | +0.08% | +$11.20 (+1.12%) | 33.6% |

History replayed from OKX SOL-USDT (USDT ~ $1) as a USD proxy. Earlier 3-pair version vs Freqtrade on the same rules: 425 vs 426 trades, so the engine matches.
Only 5% of the wallet is at risk per trade, so dollar results stay small; that is the $50 stake limit, not the strategy.
Backtests are not forecasts. Funding data is missing in backtests, so the funding check only works live. Fills assume candle close / exact stop, no slippage.

## Local
- self-checks: `python tests/test_desk.py`
- one run now: `python -m desk.desk`
- backtest (needs `.venv` with pandas + downloaded data): `.venv\Scripts\python.exe tests\backtest.py [off]`

Limits of the free host: GitHub can start scheduled runs 5-20 min late, and may pause workflows used as an app host. Not built yet: news feed, phone alerts, more pairs, live trading.
