"""Replay history through the SAME engine.step() the live run uses.
Needs pandas + downloaded data (local .venv only):  .venv\\Scripts\\python.exe tests\\backtest.py [off]
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desk import engine, gates  # noqa: E402

START = pd.Timestamp("2025-10-01", tz="UTC").timestamp()
t = gates.load_thresholds()
if "off" in sys.argv:
    t["accept_score"] = -99  # gate off baseline

data = {}
for pair in engine.LIMITS["pairs"] + [engine.BTC]:
    # ponytail: replays OKX USDT-perp history as a USD proxy (USDT ~ $1); swap for Coinbase history if the gap matters
    f = ROOT / "user_data/data/okx/futures" / (pair.split("/")[0] + "_USDT_USDT-1h-futures.feather")
    df = pd.read_feather(f)
    data[pair] = list(zip(df["date"].map(lambda d: d.timestamp()), df["open"], df["high"], df["low"], df["close"], df["volume"]))

state = engine.new_state()
n = len(data[engine.BTC])
for k in range(n):
    ts = data[engine.BTC][k][0]
    if ts < START:
        for p in data:
            state["last_candle"][p] = ts
        continue
    window = {p: [c for c in cs[max(0, k - 599):k + 1] if c[0] <= ts] for p, cs in data.items()}
    engine.step(state, window, t=t)

closed = state["closed"]
wins = sum(x["close_profit"] > 0 for x in closed)
total = sum(x["profit_abs"] for x in closed)
avg = sum(x["close_profit"] for x in closed) / max(1, len(closed))
print(f"gate {'off' if 'off' in sys.argv else 'on'}: {len(closed)} trades, avg {avg:+.2%}, "
      f"total {total:+.2f} USDT ({total / engine.LIMITS['wallet']:+.2%}), win {wins / max(1, len(closed)):.1%}, "
      f"still open {len(state['open'])}")
