"""DESK MANAGER: one run per hour (GitHub Actions). Coordinates, reports, never trades itself.

  python -m desk.desk            hourly run: scout, paper engine, then any due report
  python -m desk.desk once JOB   force one report now (morning, evening, weekly)
"""
import json
import sys
import time
import traceback
from datetime import datetime

from desk import bridge, engine, gates, launches

REPORTS = gates.ROOT / "reports"
STATE = gates.STATE
LIM = engine.LIMITS


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


def report(text):
    REPORTS.mkdir(exist_ok=True)
    with open(REPORTS / f"{datetime.now():%Y-%m-%d}.md", "a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n")
    print(text, flush=True)


def decisions(since_ts, gate="check_signal"):
    try:
        lines = (STATE / "decisions.jsonl").read_text().splitlines()
    except OSError:
        return []
    return [d for d in map(json.loads, filter(str.strip, lines)) if d["gate"] == gate and d["ts"] >= since_ts]


def side(t):
    return "short" if t["is_short"] else "long"


# --- hourly: scout + paper engine ---------------------------------------------

def hourly():
    sc = bridge.scout(LIM["pairs"])
    save_json("scout.json", sc)
    data = {p: bridge.candles(p) for p in LIM["pairs"] + [engine.BTC]}
    state = load_json("paper.json", None) or engine.new_state()
    events = engine.step(state, data, {p: d["funding"] for p, d in sc["pairs"].items()})
    save_json("paper.json", state)
    lines = []
    for kind, e in events:
        if kind == "decision":
            gates.log_decision("check_signal", e["inputs"], {k: v for k, v in e.items() if k != "inputs"})
            i = e["inputs"]
            lines.append(f"- signal {i['pair']} {i['direction']} @ {i['price']:,.6g}: **{e['choice']}** "
                         f"({e['confidence']:.0%}) - {', '.join(e['reasons'])}")
        elif kind == "open":
            lines.append(f"- OPENED #{e['trade_id']} {e['pair']} {side(e)} ${e['stake']:,.0f} @ ${e['open_rate']:,.6g}, "
                         f"stop {e['stop']:,.6g}, target {e['target']:,.6g}")
        else:
            lines.append(f"- CLOSED #{e['trade_id']} {e['pair']} {side(e)}: {e['exit_reason']} @ {e['close_rate']:,.6g}, "
                         f"{e['close_profit']:+.2%} (${e['profit_abs']:+,.2f})")
    if lines:  # speak only if something happened
        report(f"## {datetime.now():%H:%M} desk run\n" + "\n".join(lines))
    write_status(state, sc)
    eq = load_json("equity.json", [])
    eq.append([int(time.time()), round(equity(state, sc), 2)])
    save_json("equity.json", eq[-15000:])  # ~7 months at 3 runs/hour


def equity(state, sc):
    """Wallet + unrealized P&L of open paper trades at the scout's last price."""
    open_pnl = sum(t["stake"] * ((-1 if t["is_short"] else 1) * (sc["pairs"][p]["last"] / t["open_rate"] - 1) - 2 * LIM["fee"])
                   for p, t in state["open"].items() if p in sc["pairs"])
    return state["wallet"] + open_pnl


def tail_jsonl(name, n):
    try:
        return [json.loads(x) for x in (STATE / name).read_text().splitlines()[-n:] if x.strip()]
    except OSError:
        return []


def write_dashboard():
    """Everything the web dashboard (index.html) shows, in one file."""
    state, sc = load_json("paper.json", None) or engine.new_state(), load_json("scout.json", {"pairs": {}, "ts": 0})
    allsig = decisions(0)
    closed = state["closed"]
    recent, tuning = tail_jsonl("decisions.jsonl", 40), tail_jsonl("tuning.jsonl", 20)
    days = sorted(REPORTS.glob("20*.md"))[-3:]
    errors = (REPORTS / "errors.log").read_text()[-1500:] if (REPORTS / "errors.log").exists() else ""
    save_json("dashboard.json", {
        "updated": time.time(), "limits": LIM, "thresholds": gates.load_thresholds(),
        "wallet": state["wallet"], "equity_now": equity(state, sc), "open": list(state["open"].values()),
        "closed": closed[-100:], "equity": load_json("equity.json", []), "scout": sc,
        "decisions": recent[::-1], "tuning": tuning,
        "gate_counts": {c: sum(d["choice"] == c for d in allsig) for c in ("accept", "verify_more", "reject")},
        "stats": {"trades": len(closed), "wins": sum(t["close_profit"] > 0 for t in closed),
                  "best": max((t["close_profit"] for t in closed), default=0),
                  "worst": min((t["close_profit"] for t in closed), default=0)},
        "reports": [{"day": p.stem, "text": p.read_text(encoding="utf-8")} for p in days[::-1]],
        "errors": errors,
        "launches": launch_dash(),
    })


def launch_dash():
    st = load_json("launch.json", None) or launches.new_state()
    checks = tail_jsonl("launch_decisions.jsonl", 40)[::-1]
    closed = st["closed"]
    return {"limits": launches.LIM, "wallet": st["wallet"], "equity_now": launch_equity(st), "open": list(st["open"].values()),
            "closed": closed[-100:], "equity": load_json("equity_launch.json", []), "checks": checks,
            "watching": len(st["watch"]), "graded": tail_jsonl("launch_graded.jsonl", 30)[::-1],
            "stats": {"trades": len(closed), "wins": sum(t["close_profit"] > 0 for t in closed),
                      "best": max((t["close_profit"] for t in closed), default=0),
                      "worst": min((t["close_profit"] for t in closed), default=0)}}


def launch_job():
    st = load_json("launch.json", None) or launches.new_state()
    events = launches.run(st)
    save_json("launch.json", st)
    lines = []
    for kind, e in events:
        if kind == "decision":
            if e.pop("changed"):  # log first verdict per token and every change, not every re-check
                gates.log_decision("check_launch", e["inputs"], {k: v for k, v in e.items() if k != "inputs"}, "launch_decisions.jsonl")
            if e["choice"] == "accept":
                i = e["inputs"]
                lines.append(f"- launch {i['symbol']} ({i['age_h']:.1f}h old, liq ${i['liquidity']:,.0f}): **accept** "
                             f"({e['confidence']:.0%}) - {', '.join(e['reasons'])}")
        elif kind == "open":
            lines.append(f"- LAUNCH OPENED #{e['trade_id']} {e['pair']} ${e['stake']:,.0f} @ ${e['open_rate']:.8g}")
        else:
            lines.append(f"- LAUNCH CLOSED #{e['trade_id']} {e['pair']}: {e['exit_reason']}, {e['close_profit']:+.2%} (${e['profit_abs']:+,.2f})")
    if lines:
        report(f"## {datetime.now():%H:%M} launch desk\n" + "\n".join(lines))
    eq = load_json("equity_launch.json", [])
    eq.append([int(time.time()), round(launch_equity(st), 2)])
    save_json("equity_launch.json", eq[-15000:])


def launch_equity(st):
    """Cash + open launch positions at their last seen price (cost on exit included)."""
    return st["wallet"] + sum(p["stake"] * ((p.get("last_price") or p["open_rate"]) * (1 - launches.LIM["cost"]) / p["open_rate"])
                              for p in st["open"].values())


def write_status(state, sc):
    last = {p: d["last"] for p, d in sc["pairs"].items()}
    pnl = state["wallet"] - LIM["wallet"]
    lines = [f"# Paper desk status", f"Updated {datetime.now():%Y-%m-%d %H:%M} (New York). Paper money only.", "",
             f"Wallet: **${state['wallet']:,.2f}** ({pnl:+,.2f} USD, {pnl / LIM['wallet']:+.2%}) - "
             f"{len(state['closed'])} closed trades, {sum(t['close_profit'] > 0 for t in state['closed'])} wins", "",
             "## Open trades"]
    for p, t in state["open"].items():
        sign = -1 if t["is_short"] else 1
        now = sign * (last[p] / t["open_rate"] - 1)
        lines.append(f"- #{t['trade_id']} {p} {side(t)} @ {t['open_rate']:,.6g}, now {last[p]:,.6g} ({now:+.2%}), "
                     f"stop {t['stop']:,.6g}, target {t['target']:,.6g}")
    if not state["open"]:
        lines.append("- none")
    lines += ["", "## Last 10 closed"]
    for t in state["closed"][-10:][::-1]:
        lines.append(f"- #{t['trade_id']} {t['pair']} {side(t)} {t['close_date'][:16]}: {t['exit_reason']}, {t['close_profit']:+.2%}")
    lines += ["", f"Gate threshold (self-tuned weekly): accept_score = {gates.load_thresholds()['accept_score']}",
              "Daily reports: `reports/YYYY-MM-DD.md`"]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- daily / weekly reports ----------------------------------------------------

def job_morning():
    sc = load_json("scout.json", {"pairs": {}, "ts": 0})
    route = gates.route_task({"bot_reachable": True, "bot_state": "running", "brief_fresh": time.time() - sc["ts"] < 7200})
    gates.log_decision("route_task", {"brief_fresh": time.time() - sc["ts"] < 7200}, route)
    lines = [f"## Morning brief {datetime.now():%H:%M}",
             f"Next seat: **{route['choice']}** ({route['confidence']:.0%}) - {route['reasons'][0]}", "",
             "| pair | last | 24h | funding/8h | open interest |", "|---|---|---|---|---|"]
    max_f = gates.load_thresholds()["max_funding"]
    for p, d in sc["pairs"].items():
        crowd = " crowded long" if d["funding"] > max_f else " crowded short" if d["funding"] < -max_f else ""
        lines.append(f"| {p} | {d['last']:,.6g} | {d['change_24h']:+.2%} | {d['funding']:+.4%}{crowd} | {d['open_interest']:,.0f} |")
    report("\n".join(lines))


def job_evening():
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    sigs = decisions(start)
    counts = {c: sum(d["choice"] == c for d in sigs) for c in ("accept", "verify_more", "reject")}
    state = load_json("paper.json", None) or engine.new_state()
    today = [t for t in state["closed"] if t["close_timestamp"] / 1000 >= start]
    lines = [f"## Evening report {datetime.now():%H:%M}",
             f"Signals checked: {len(sigs)} (accept {counts['accept']}, verify_more {counts['verify_more']}, reject {counts['reject']})",
             f"Trades closed today: {len(today)}, open now: {len(state['open'])}"]
    for t in today:
        rv = gates.review_trade(t, stoploss=LIM["stoploss"])
        gates.log_decision("review_trade", {"trade_id": t["trade_id"]}, rv)
        lines.append(f"- #{t['trade_id']} {t['pair']} {side(t)}: {t['open_rate']:,.6g} -> {t['close_rate']:,.6g}, "
                     f"{t['close_profit']:+.2%} - **{rv['choice']}** ({rv['reasons'][0]})")
    pnl = state["wallet"] - LIM["wallet"]
    lines.append(f"Paper P&L all time: {pnl:+,.2f} USD ({pnl / LIM['wallet']:+.2%})")
    lst = load_json("launch.json", None) or launches.new_state()
    lt = [t for t in lst["closed"] if t["close_timestamp"] / 1000 >= start]
    lch = [d for d in tail_jsonl("launch_decisions.jsonl", 5000) if d["ts"] >= start]
    lines.append(f"Launch desk: {len(lch)} token checks ({sum(d['choice'] == 'accept' for d in lch)} accepted), "
                 f"{len(lt)} closed today, {len(lst['open'])} open, equity ${launch_equity(lst):,.2f}")
    for t in lt:
        rv = gates.review_trade(t, stoploss=launches.LIM["stoploss"])
        lines.append(f"- #{t['trade_id']} {t['pair']}: {t['exit_reason']} {t['close_profit']:+.2%} - **{rv['choice']}** ({rv['reasons'][0]})")
    blockers = [r for d in sigs if d["choice"] != "accept" for r in d["reasons"]]
    if blockers:
        top = max(set(blockers), key=blockers.count)
        lines.append(f"Most common blocker today: {top} ({blockers.count(top)}x)")
    report("\n".join(lines))


def grade(direction, price, cs, lim):
    """+2 if target hit first, -1 if stop hit first (same candle = stop), 0 if neither."""
    for _, hi, lo, _ in cs:
        if direction == "long":
            stop, tgt = lo <= price * (1 + lim["stoploss"]), hi >= price * (1 + lim["target"])
        else:
            stop, tgt = hi >= price * (1 - lim["stoploss"]), lo <= price * (1 - lim["target"])
        if stop:
            return -1
        if tgt:
            return 2
    return 0


def job_weekly():
    t = gates.load_thresholds()
    records, by_choice = [], {}
    for d in decisions(time.time() - 28 * 86400):
        entry_ts = d["inputs"].get("candle_ts", d["ts"]) + 3600
        if "score" not in d or entry_ts > time.time() - 48 * 3600:
            continue  # needs 48h of hindsight
        ev = d["inputs"]
        r = grade(ev["direction"], ev["price"], bridge.candles_after(ev["pair"], entry_ts), LIM)
        records.append({"score": d["score"], "r": r})
        by_choice.setdefault(d["choice"], []).append(r)
        time.sleep(0.12)
    new_t, note = gates.tune(records, t)
    lines = [f"## Weekly review {datetime.now():%H:%M}", f"Graded {len(records)} signal decisions (last 28 days, 48h hindsight)."]
    for c, rs in by_choice.items():
        lines.append(f"- {c}: {len(rs)} signals, avg {sum(rs) / len(rs):+.2f}R, {sum(r > 0 for r in rs)} hit target / {sum(r < 0 for r in rs)} hit stop")
    lines.append(f"Self-tuning (memes): {note}")
    graded = [r for r in tail_jsonl("launch_graded.jsonl", 2000) if r["ts"] > time.time() - 28 * 86400]
    new_t, lnote = gates.tune(graded, new_t, "launch_accept", "launch_grid")
    lines.append(f"Launch desk: graded {len(graded)} tokens (24h after first check). Self-tuning (launches): {lnote}")
    note = f"memes: {note}; launches: {lnote}"
    if new_t != t:
        gates.THRESHOLDS.write_text(json.dumps(new_t, indent=2) + "\n")
        with open(STATE / "tuning.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "note": note, "accept_score": new_t["accept_score"], "launch_accept": new_t["launch_accept"]}) + "\n")
    report("\n".join(lines))


def daily(hour, weekday=None):
    def due(now, last):
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        return (weekday is None or now.weekday() == weekday) and now >= target and last < target.timestamp()
    return due


JOBS = {"morning": (daily(8), job_morning), "evening": (daily(21), job_evening), "weekly": (daily(21, weekday=6), job_weekly)}


def run():
    """One hourly run. Errors are logged, never fatal: one bad hour must not stop the next."""
    now, runs = datetime.now(), load_json("jobs.json", {})
    for name, fn in [("hourly", hourly), ("launches", launch_job)] + [(n, f) for n, (due, f) in JOBS.items() if due(now, runs.get(n, 0))]:
        try:
            fn()
        except Exception:
            REPORTS.mkdir(exist_ok=True)
            with open(REPORTS / "errors.log", "a") as f:
                f.write(f"{now:%Y-%m-%d %H:%M} {name}\n{traceback.format_exc()}\n")
            print(traceback.format_exc(), file=sys.stderr)
        runs[name] = now.timestamp()
    save_json("jobs.json", runs)
    write_dashboard()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "once":
        JOBS[sys.argv[2]][1]()
    else:
        run()
