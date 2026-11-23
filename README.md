# Meme paper desk (Solana memecoins, USD, free, 24/7)

**Dashboard (live, animated, add to iPhone home screen via Safari > Share > Add to Home Screen):**
https://ay6741ay6741-sudo.github.io/trading-desk/

**Paper money only.** No exchange account, wallet or key exists anywhere in this project.
Runs 3x per hour on GitHub Actions (free). State and reports are committed back to this repo.

Research -> decision gate -> paper engine executes -> human on the dangerous edges.

## Two desks
| | Meme desk | Launch desk |
|---|---|---|
| Trades | 8 established Solana memecoins: BONK, WIF, POPCAT, PENGU, TRUMP, FARTCOIN, PNUT, MOODENG | brand-new Solana tokens found on DexScreener |
| Prices | Coinbase USD (1h candles) | DexScreener USD |
| Setup | 24h channel breakout, long or short | survived its first 30 min, graduated pool, real flow |
| Gate | `check_signal`: volume, trend, perp funding (OKX), BTC regime | `check_launch`: rug rules first (mint/freeze authority via Solana RPC, RugCheck danger flags, graduated DEX, liquidity), then quality score (liquidity, turnover, buyers vs sellers, momentum, value vs pool, LP lock) |
| Limits | $1,000 wallet, $50/trade, max 3 open, stop -8%, target +16%, 0.05% fee/side | $1,000 wallet, $25/trade, max 5 open, stop -25%, target +50%, max hold 24h, 1%/side cost |
| Exits | stop / target checked candle by candle, opposite breakout | stop / target / 24h time exit / rug exit if liquidity drops 70% |
| Self-tuning | weekly: grades every signal 48h later, moves `accept_score` | weekly: grades every scored token 24h after first check, moves `launch_accept` |

Hard limits live in `desk/limits.json`; self-tuning only edits `desk/thresholds.json`. `paper_only: false` turns every trade into `human_review`, so nothing opens.

## Gates (pick one option from a fixed menu, with probabilities + confidence)
- `check_signal` / `check_launch` -> accept / verify_more / reject. No accept, no trade.
- `review_trade` -> closed_as_planned / verify_more / mismatch. No fill, no done. Launch stops that gap past -26% show as mismatch on purpose.
- `guard_action` -> allow / confirm / human_review / deny by action level (0 read, 1 prepare, 2 reversible, 3 trade, 4 money). Asked before every open.
- `route_task` -> only offers seats that exist right now.

Typed output does not mean the call is right. A gate, not an oracle.

## Backtest: meme desk (2025-10-01 to 2026-11-22, $1,000 wallet, $50/trade)
| stop / target | gate | trades | avg/trade | total | win rate |
|---|---|---|---|---|---|
| -8% / +16% | on | 433 | +0.51% | +$110.37 (+11.0%) | 37.9% |
| -8% / +16% | off | 656 | +0.07% | +$21.39 (+2.1%) | 34.5% |
| -3% / +6% | on | 863 | +0.15% | +$62.59 | 35.8% |
| -5% / +10% | on | 585 | +0.31% | +$91.27 | 36.6% |
| -10% / +20% | on | 397 | +0.14% | +$27.81 | 36.0% |

-8/+16 was picked from these 4 variants on the same data, so treat its result as optimistic. History is OKX USDT perps as a USD proxy. Funding is missing in backtests. Backtests are not forecasts.
The launch desk has no backtest (no free history of new-token pools); it learns from its own graded tokens.

## Honest limits
- Runs every ~20 min: the launch desk follows tokens that survived their first minutes. It cannot snipe, and stops fill at the next observed price.
- GitHub can delay or drop scheduled runs; 3 runs/hour cover single drops.
- Memecoins and new launches are extremely risky; most new tokens go to zero.

## Files
- `desk/engine.py` meme paper engine · `desk/launches.py` launch scanner + ledger · `desk/gates.py` all gates + tuning
- `desk/bridge.py` read-only market data · `desk/desk.py` run loop, reports, dashboard data · `index.html` dashboard
- `state/` ledgers, decisions, equity · `reports/` daily notes, STATUS.md, errors.log

## Local
- self-checks: `python tests/test_desk.py`
- one run now: `python -m desk.desk`
- meme backtest (needs `.venv` + downloaded data): `.venv\Scripts\python.exe tests\backtest.py [off] [stop target]`
