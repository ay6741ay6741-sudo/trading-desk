"""Desk decision gates. Rules only, typed choices out.

Every gate picks ONE option from a fixed menu and returns
{"choice", "probabilities", "confidence", "reasons"}.
The format is guaranteed; the call can still be wrong. A gate, not an oracle.
"""
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
THRESHOLDS = Path(__file__).with_name("thresholds.json")
LIMITS_ALL = json.loads(Path(__file__).with_name("limits.json").read_text())


def load_thresholds():
    return json.loads(THRESHOLDS.read_text())


def _decide(logits, reasons):
    """Softmax over the menu. Ties go to the first key, so menus list the safest option first."""
    top = max(logits.values())
    exp = {k: math.exp(v - top) for k, v in logits.items()}
    z = sum(exp.values())
    probs = {k: round(v / z, 3) for k, v in exp.items()}
    choice = max(probs, key=probs.get)
    return {"choice": choice, "probabilities": probs, "confidence": probs[choice], "reasons": reasons}


def log_decision(gate, inputs, result):
    STATE.mkdir(exist_ok=True)
    with open(STATE / "decisions.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.time(), "gate": gate, "inputs": inputs, **result}) + "\n")


# --- jev_check_signal: accept / verify_more / reject -------------------------

def signal_score(ev, t):
    """Evidence -> score. Max 2.5 when volume, trend, funding all agree and BTC doesn't fight it."""
    score, reasons, gaps = 0.0, [], []
    long = ev["direction"] == "long"
    vr = ev["volume_ratio"]
    if vr >= t["min_volume_ratio"]:
        score += 1
        reasons.append(f"volume {vr:.2f}x 20-day avg")
    else:
        score -= 1
        reasons.append(f"volume only {vr:.2f}x avg")
    if ev["trend_ok"]:
        score += 1
        reasons.append("trend agrees")
    else:
        score -= 1.5
        reasons.append("against trend")
    funding = ev.get("funding")
    if funding is None:
        gaps.append("funding unknown")
    elif (funding > t["max_funding"]) if long else (funding < -t["max_funding"]):
        score -= 1
        reasons.append(f"crowded funding {funding:.4%}")
    else:
        score += 0.5
        reasons.append(f"funding neutral {funding:.4%}")
    if ev.get("btc_conflict") is None:
        gaps.append("BTC regime unknown")
    elif ev["btc_conflict"]:
        score -= 1
        reasons.append("BTC against the trade vs its daily open")
    return score, reasons, gaps


def check_signal(ev, t=None):
    t = t or load_thresholds()
    if not ev.get("whitelisted", True):
        return _decide({"reject": 9, "verify_more": 0, "accept": -9}, ["pair not on whitelist"])
    if ev["candle_age_h"] > 2:
        return _decide({"verify_more": 3, "reject": 0, "accept": -3}, [f"stale data ({ev['candle_age_h']:.1f}h old)"])
    score, reasons, gaps = signal_score(ev, t)
    edge = score - t["accept_score"]
    out = _decide({"verify_more": t["gap_weight"] * len(gaps), "reject": -edge, "accept": edge}, reasons + gaps)
    out["score"] = score
    return out


# --- jev_review_trade: closed_as_planned / verify_more / mismatch -------------

BAD_EXITS = {"liquidation", "force_exit", "emergency_exit"}


def review_trade(trade, t=None, stoploss=None):
    """trade = one closed paper trade. No fill, no done."""
    t = t or load_thresholds()
    stoploss = LIMITS_ALL["memes"]["stoploss"] if stoploss is None else stoploss
    menu = lambda pick, why: _decide({k: (4 if k == pick else 0) for k in ("verify_more", "mismatch", "closed_as_planned")}, why)
    if trade.get("is_open") or trade.get("close_rate") is None or not trade.get("close_date"):
        return menu("verify_more", ["no exit fill yet"])
    fills = [o for o in trade.get("orders", []) if o.get("status") == "closed" and (o.get("filled") or 0) > 0]
    if len(fills) < 2:
        return menu("verify_more", [f"only {len(fills)} filled order(s), need entry + exit"])
    profit, reason = trade.get("close_profit") or 0.0, trade.get("exit_reason") or "?"
    if reason in BAD_EXITS:
        return menu("mismatch", [f"exit reason {reason} is outside the plan"])
    if profit < stoploss - t["slippage_tolerance"]:
        return menu("mismatch", [f"loss {profit:.2%} beyond stop {stoploss:.0%} + slippage"])
    return menu("closed_as_planned", [f"{reason} at {profit:.2%}, {len(fills)} fills"])


# --- jev_guard_action: allow / confirm / human_review / deny -----------------

LEVELS = {  # 0 read, 1 prepare, 2 reversible, 3 trade, 4 money/irreversible
    "read": 0, "prepare_plan": 1, "write_report": 1,
    "pause_entries": 2, "blacklist_pair": 2,
    "open_trade": 3,
    "raise_stake": 4, "raise_leverage": 4, "change_stoploss": 4, "new_api_key": 4,
    "transfer": 4, "withdraw": 4, "disable_dry_run": 4,
}


def guard_action(action, dry_run=True):
    level = LEVELS.get(action)
    if level is None:
        pick, why = "deny", f"unknown action '{action}'"
    elif level <= 1:
        pick, why = "allow", f"level {level}: read/prepare"
    elif level == 2:
        pick, why = ("allow" if dry_run else "confirm"), "level 2: reversible, logged"
    elif level == 3:
        pick, why = ("allow" if dry_run else "human_review"), "level 3: trade (paper money only)" if dry_run else "level 3: real trade"
    else:
        pick, why = "human_review", f"level 4: money/irreversible, human always"
    return _decide({k: (4 if k == pick else 0) for k in ("deny", "human_review", "confirm", "allow")}, [why]) | {"level": level}


# --- jev_route_task: who owns the next step ----------------------------------

def route_task(s):
    """Only offer seats that exist right now."""
    logits = {"stand_down": 0.0, "SCOUT": 0.0}
    bot_up = s.get("bot_reachable") and s.get("bot_state") == "running"
    if bot_up:
        logits["trading_bot"] = 0.0
    if s.get("money_action_pending"):
        logits["human_review"] = 0.0
    if "human_review" in logits:
        logits["human_review"], why = 4, "an action moves money"
    elif not s.get("brief_fresh"):
        logits["SCOUT"], why = 3, "context is stale, scout first"
    elif bot_up:
        logits["trading_bot"], why = 3, "context fresh, bot running: bot trades, signal gate guards entries"
    else:
        logits["stand_down"], why = 3, "bot down or stopped"
    return _decide(logits, [why])


# --- weekly self-tuning ------------------------------------------------------

def tune(records, t=None, knob="accept_score", grid="tune_grid"):
    """records: [{"score": float, "r": +2 / -1 / 0}] for every signal seen (accepted or not).
    Picks the accept_score that maximised average R, moves one step toward it.
    # ponytail: grid search over one knob, add more knobs when there are months of data
    """
    t = dict(t or load_thresholds())
    best, best_r, note = t[knob], None, "not enough samples, unchanged"
    for thr in t[grid]:
        taken = [r["r"] for r in records if r["score"] - thr > 0]
        if len(taken) < t["tune_min_samples"]:
            continue
        avg = sum(taken) / len(taken)
        tie_closer = avg == best_r and abs(thr - t[knob]) < abs(best - t[knob])
        if best_r is None or avg > best_r or tie_closer:
            best, best_r = thr, avg
    if best_r is not None:
        cur, step = t[knob], t["tune_step"]
        new = cur + max(-step, min(step, best - cur))
        note = f"best {knob} {best} (avg {best_r:+.2f}R); moved {cur} -> {new}"
        t[knob] = new
    return t, note
