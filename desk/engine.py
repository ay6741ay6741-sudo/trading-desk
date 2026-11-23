"""Paper engine: the desk's only "hands". Paper money only, stdlib only.

Same rules as before: 1h candles, 24h channel breakout proposes, gates.check_signal decides,
stop -3% / target +6% / opposite channel break exits. Hard limits come from limits.json,
which the desk's self-tuning never touches.

step() is pure over (state, candles): the live hourly run and the backtest call the same code.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from desk import gates

LIMITS = json.loads(Path(__file__).with_name("limits.json").read_text())
BTC = "BTC/USD"  # regime filter only, never traded
TS, O, H, L, C, V = range(6)  # candle = (ts_sec, open, high, low, close, volume), oldest first, closed only


def ema_last(values, n):
    # ponytail: EMA over the fetched window (600 candles) instead of full history; converged after ~3n candles
    k, e = 2 / (n + 1), values[0]
    for v in values[1:]:
        e += k * (v - e)
    return e


def features(cs, i, btc_vs_open):
    """Indicators at closed candle i. None if not enough history."""
    if i < 480:
        return None
    closes = [c[C] for c in cs[: i + 1]]
    return {
        "close": cs[i][C],
        "hi24": max(c[H] for c in cs[i - 24:i]),
        "lo24": min(c[L] for c in cs[i - 24:i]),
        "vol_ratio": cs[i][V] / (sum(c[V] for c in cs[i - 479:i + 1]) / 480 or 1),
        "ema50": ema_last(closes[-600:], 50),
        "ema200": ema_last(closes[-600:], 200),
        "btc_vs_open": btc_vs_open.get(cs[i][TS]),
    }


def btc_day_open_map(btc):
    day_open, out = {}, {}
    for c in btc:
        day = int(c[TS] // 86400)
        day_open.setdefault(day, c[O])
        out[c[TS]] = c[C] / day_open[day] - 1
    return out


def new_state():
    return {"wallet": LIMITS["wallet"], "next_id": 1, "open": {}, "closed": [], "last_candle": {}}


def _close(state, pair, pos, price, ts, reason):
    sign = -1 if pos["is_short"] else 1
    ratio = sign * (price / pos["open_rate"] - 1) - 2 * LIMITS["fee"]
    profit = pos["stake"] * ratio
    state["wallet"] += profit
    trade = {**pos, "is_open": False, "close_rate": price, "close_timestamp": ts * 1000,
             "close_date": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
             "close_profit": round(ratio, 6), "profit_abs": round(profit, 4), "exit_reason": reason,
             "orders": [{"status": "closed", "filled": pos["amount"]}, {"status": "closed", "filled": pos["amount"]}]}
    state["closed"].append(trade)
    del state["open"][pair]
    return trade


def _exit_check(pos, c, f):
    """Exit price + reason for one closed candle, or None. Stop beats target inside the same candle."""
    if pos["is_short"]:
        if c[H] >= pos["stop"]:
            return pos["stop"], "stop_loss"
        if c[L] <= pos["target"]:
            return pos["target"], "roi"
        if f and c[C] > f["hi24"]:
            return c[C], "exit_signal"
    else:
        if c[L] <= pos["stop"]:
            return pos["stop"], "stop_loss"
        if c[H] >= pos["target"]:
            return pos["target"], "roi"
        if f and c[C] < f["lo24"]:
            return c[C], "exit_signal"
    return None


def step(state, data, funding=None, t=None, whitelist=None):
    """Process candles closed since the last step. Returns events: ("decision"|"open"|"close", payload)."""
    t = t or gates.load_thresholds()
    funding = funding or {}
    whitelist = whitelist or LIMITS["pairs"]
    btc_map = btc_day_open_map(data.get(BTC, []))
    events = []
    for pair, cs in data.items():
        if pair not in whitelist:
            continue  # context data (BTC), not tradeable
        last_seen = state["last_candle"].get(pair, 0)
        new_idx = [i for i, c in enumerate(cs) if c[TS] > last_seen]
        if not new_idx:
            continue
        for i in new_idx:  # exits: walk every candle we haven't seen, nothing skipped on late runs
            pos = state["open"].get(pair)
            if pos and cs[i][TS] > pos["open_candle"]:
                hit = _exit_check(pos, cs[i], features(cs, i, btc_map))
                if hit:
                    events.append(("close", _close(state, pair, pos, hit[0], cs[i][TS] + 3600, hit[1])))
        state["last_candle"][pair] = cs[-1][TS]
        i = len(cs) - 1  # entries: only on the newest closed candle, never backfilled
        f = features(cs, i, btc_map)
        if not f or pair in state["open"] or len(state["open"]) >= LIMITS["max_open_trades"]:
            continue
        side = "long" if f["close"] > f["hi24"] else "short" if f["close"] < f["lo24"] else None
        if not side or state["wallet"] < LIMITS["stake"]:
            continue
        long, btc = side == "long", f["btc_vs_open"]
        ev = {"pair": pair, "direction": side, "volume_ratio": round(f["vol_ratio"], 3),
              "trend_ok": (f["ema50"] > f["ema200"]) == long,
              "btc_conflict": None if btc is None else (btc < 0 if long else btc > 0),
              "funding": funding.get(pair), "candle_age_h": 0, "whitelisted": pair in whitelist,
              "price": f["close"], "candle_ts": cs[i][TS]}
        res = gates.check_signal(ev, t)
        events.append(("decision", {"inputs": ev, **res}))
        if res["choice"] != "accept":
            continue
        if gates.guard_action("open_trade", dry_run=LIMITS["paper_only"])["choice"] != "allow":
            continue  # paper_only off = every trade needs a human, so the engine never opens it
        price = f["close"]  # ponytail: fill at candle close, no slippage; add a slippage haircut if live fills matter
        sign = -1 if not long else 1
        pos = {"trade_id": state["next_id"], "pair": pair, "is_short": not long, "open_rate": price,
               "open_date": datetime.fromtimestamp(cs[i][TS] + 3600, timezone.utc).isoformat(),
               "open_candle": cs[i][TS], "stake": LIMITS["stake"], "amount": LIMITS["stake"] / price,
               "stop": price * (1 + sign * LIMITS["stoploss"]), "target": price * (1 + sign * LIMITS["target"])}
        state["next_id"] += 1
        state["open"][pair] = pos
        events.append(("open", pos))
    return events
