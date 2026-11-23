"""DESK MANAGER: one loop, runs forever. Coordinates, reports, never trades.

  python -m desk.desk            run forever
  python -m desk.desk once JOB   run one job now (scout, morning, watch, evening, weekly)
"""
import json
import sys
import time
import traceback
from datetime import datetime, timedelta

from desk import bridge, gates

REPORTS = gates.ROOT / "reports"
STATE = gates.STATE
TICK = 60


def save_json(name, data):
    STATE.mkdir(exist_ok=True)
    tmp = STATE / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(STATE / name)


def load_json(name, default):
    try:
        return json.loads((STATE / name).read_text())
    except (OSError, ValueError):
        return default


def report(text, day=None):
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{(day or datetime.now()).strftime('%Y-%m-%d')}.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")
    print(text, flush=True)


def decisions(since_ts, gate="check_signal"):
    try:
        lines = (STATE / "decisions.jsonl").read_text().splitlines()
    except OSError:
        return []
    rows = (json.loads(x) for x in lines if x.strip())
    return [d for d in rows if d["gate"] == gate and d["ts"] >= since_ts]


def bot_state():
    try:
        cfg, status = bridge.bot("show_config"), bridge.bot("status")
        return {"bot_reachable": True, "bot_state": cfg.get("state"), "dry_run": cfg.get("dry_run"),
                "max_open_trades": cfg.get("max_open_trades"), "open": status,
                "whitelist": bridge.bot("whitelist").get("whitelist", [])}
    except Exception as e:  # bot down = stand down, never crash the desk
        return {"bot_reachable": False, "error": f"{type(e).__name__}: {e}"}


def check_safety(s):
    """dry_run off without a human = the one thing that must never pass silently."""
    if s.get("bot_reachable") and s.get("dry_run") is False:
        g = gates.guard_action("disable_dry_run", dry_run=False)
        report(f"## ALERT {datetime.now():%H:%M}\nBot reports dry_run = false. Guard: {g['choice']} ({g['reasons'][0]}). "
               "Live trading was never approved by the desk. Stop the bot and check config.json.")


# --- jobs ---------------------------------------------------------------------

def job_scout():
    s = bot_state()
    pairs = s.get("whitelist") or json.loads((gates.ROOT / "user_data" / "config.json").read_text())["exchange"]["pair_whitelist"]
    save_json("scout.json", bridge.scout(pairs))


def job_morning():
    job_scout()
    s, sc = bot_state(), load_json("scout.json", {"pairs": {}})
    brief_fresh = time.time() - sc.get("ts", 0) < 7200
    route = gates.route_task({**s, "brief_fresh": brief_fresh})
    gates.log_decision("route_task", {"brief_fresh": brief_fresh, "bot_reachable": s["bot_reachable"]}, route)
    lines = [f"## Morning brief {datetime.now():%H:%M}",
             f"Bot: {'up, ' + str(s.get('bot_state')) + (', dry_run' if s.get('dry_run') else '') if s['bot_reachable'] else 'DOWN (' + s['error'] + ')'}",
             f"Next seat: **{route['choice']}** ({route['confidence']:.0%}) - {route['reasons'][0]}", "",
             "| pair | last | 24h | funding/8h | open interest |", "|---|---|---|---|---|"]
    for p, d in sc["pairs"].items():
        crowd = " (crowded long)" if d["funding"] > gates.load_thresholds()["max_funding"] else ""
        lines.append(f"| {p} | {d['last']:,.6g} | {d['change_24h']:+.2%} | {d['funding']:+.4%}{crowd} | {d['open_interest']:,.0f} |")
    report("\n".join(lines))


def job_watch():
    s = bot_state()
    check_safety(s)
    if not s["bot_reachable"]:
        last = load_json("watch.json", {})
        if last.get("up", True):
            report(f"## Position watch {datetime.now():%H:%M}\nBot unreachable: {s['error']}. Desk stands down until it is back.")
        save_json("watch.json", {"up": False, "ids": last.get("ids", [])})
        return
    last = load_json("watch.json", {"up": True, "ids": []})
    ids = sorted(t["trade_id"] for t in s["open"])
    if ids == last.get("ids") and last.get("up", True):
        return  # speak only if something changed
    lines = [f"## Position watch {datetime.now():%H:%M}" + ("" if last.get("up", True) else " (bot is back)")]
    for t in s["open"]:
        side = "short" if t.get("is_short") else "long"
        lines.append(f"- #{t['trade_id']} {t['pair']} {side} @ {t['open_rate']:.6g}, now {t.get('current_rate', 0):.6g} "
                     f"({t.get('profit_ratio', 0):+.2%}), stop {t.get('stop_loss_abs', 0):.6g}")
    closed = set(last.get("ids", [])) - set(ids)
    if closed:
        lines.append(f"- closed since last check: {', '.join('#' + str(i) for i in sorted(closed))} (graded in evening report)")
    if not s["open"] and not closed:
        lines.append("- no open trades")
    report("\n".join(lines))
    save_json("watch.json", {"up": True, "ids": ids})


def job_evening():
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    sigs = decisions(start)
    counts = {c: sum(d["choice"] == c for d in sigs) for c in ("accept", "verify_more", "reject")}
    lines = [f"## Evening report {datetime.now():%H:%M}",
             f"Signals checked: {len(sigs)} (accept {counts['accept']}, verify_more {counts['verify_more']}, reject {counts['reject']})"]
    s = bot_state()
    if not s["bot_reachable"]:
        lines.append(f"Bot unreachable: {s['error']}. No trade data today.")
        return report("\n".join(lines))
    trades = bridge.bot("trades", limit=500).get("trades", [])
    today = [t for t in trades if not t.get("is_open") and (t.get("close_timestamp") or 0) / 1000 >= start]
    lines.append(f"Trades closed today: {len(today)}, open now: {len(s['open'])}")
    for t in today:
        rv = gates.review_trade(t)
        gates.log_decision("review_trade", {"trade_id": t["trade_id"]}, rv)
        lines.append(f"- #{t['trade_id']} {t['pair']} {'short' if t.get('is_short') else 'long'}: "
                     f"{t['open_rate']:.6g} -> {t['close_rate']:.6g}, {t['close_profit']:+.2%} - **{rv['choice']}** ({rv['reasons'][0]})")
    p = bridge.bot("profit")
    lines.append(f"Paper P&L all time: {p.get('profit_all_coin', 0):+.2f} USDT ({p.get('profit_all_percent', 0):+.2f}%), "
                 f"{p.get('winning_trades', 0)}W / {p.get('losing_trades', 0)}L")
    reasons = [r for d in sigs if d["choice"] != "accept" for r in d["reasons"]]
    if reasons:
        top = max(set(reasons), key=reasons.count)
        lines.append(f"Most common blocker today: {top} ({reasons.count(top)}x)")
    report("\n".join(lines))


def grade(direction, price, candles, t):
    """+2 if target hit first, -1 if stop hit first (same candle = stop, conservative), 0 if neither."""
    for _, hi, lo, _ in candles:
        if direction == "long":
            stop, tgt = lo <= price * (1 + t["stoploss"]), hi >= price * (1 + t["target"])
        else:
            stop, tgt = hi >= price * (1 - t["stoploss"]), lo <= price * (1 - t["target"])
        if stop:
            return -1
        if tgt:
            return 2
    return 0


def job_weekly():
    t = gates.load_thresholds()
    cutoff = time.time() - 48 * 3600  # need 48h of hindsight
    records, by_choice = [], {}
    for d in decisions(time.time() - 28 * 86400):
        if "score" not in d or d["ts"] > cutoff:
            continue
        ev = d["inputs"]
        r = grade(ev["direction"], ev["price"], bridge.candles_after(ev["pair"], d["ts"]), t)
        records.append({"score": d["score"], "r": r})
        by_choice.setdefault(d["choice"], []).append(r)
        time.sleep(0.15)  # OKX rate limit
    new_t, note = gates.tune(records, t)
    lines = [f"## Weekly review {datetime.now():%H:%M}", f"Graded {len(records)} signal decisions (last 28 days, 48h hindsight)."]
    for c, rs in by_choice.items():
        lines.append(f"- {c}: {len(rs)} signals, avg {sum(rs) / len(rs):+.2f}R, "
                     f"{sum(r > 0 for r in rs)} hit target / {sum(r < 0 for r in rs)} hit stop")
    lines.append(f"Self-tuning: {note}")
    if new_t != t:
        gates.THRESHOLDS.write_text(json.dumps(new_t, indent=2))
        with open(STATE / "tuning.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "note": note, "accept_score": new_t["accept_score"]}) + "\n")
    report("\n".join(lines))


# --- scheduler ----------------------------------------------------------------

def every(seconds):
    return lambda now, last: now.timestamp() - last >= seconds


def daily(hour, minute=0, weekday=None):
    def due(now, last):
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (weekday is None or now.weekday() == weekday) and now >= target and last < target.timestamp()
    return due


JOBS = {
    "scout": (every(3600), job_scout),
    "morning": (daily(8), job_morning),
    "watch": (every(1800), job_watch),
    "evening": (daily(21), job_evening),
    "weekly": (daily(21, 30, weekday=6), job_weekly),
}


def run_forever():
    report(f"## Desk started {datetime.now():%H:%M}")
    while True:
        now, runs = datetime.now(), load_json("jobs.json", {})
        for name, (due, fn) in JOBS.items():
            if due(now, runs.get(name, 0)):
                try:
                    fn()
                except Exception:
                    with open(REPORTS / "errors.log", "a") as f:
                        f.write(f"{now:%Y-%m-%d %H:%M} {name}\n{traceback.format_exc()}\n")
                runs[name] = now.timestamp()  # failed jobs wait for the next slot, no retry storm
                save_json("jobs.json", runs)
        save_json("heartbeat.json", {"ts": time.time()})
        time.sleep(TICK)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "once":
        JOBS[sys.argv[2]][1]()
    else:
        run_forever()
