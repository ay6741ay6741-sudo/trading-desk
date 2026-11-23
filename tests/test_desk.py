"""Self-check for the gates, grading and tuning. Run: python tests/test_desk.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desk import bridge, gates  # noqa: E402
from desk.desk import grade  # noqa: E402

T = gates.load_thresholds()
GOOD = {"pair": "SOL/USDT:USDT", "direction": "long", "volume_ratio": 1.4, "trend_ok": True,
        "btc_conflict": False, "funding": 0.0001, "candle_age_h": 0.1, "whitelisted": True}

# check_signal
assert gates.check_signal(GOOD, T)["choice"] == "accept"
assert gates.check_signal(GOOD | {"trend_ok": False, "volume_ratio": 0.6}, T)["choice"] == "reject"
assert gates.check_signal(GOOD | {"candle_age_h": 5}, T)["choice"] == "verify_more"
assert gates.check_signal(GOOD | {"whitelisted": False}, T)["choice"] == "reject"
assert gates.check_signal(GOOD | {"funding": 0.002}, T)["choice"] == "reject"  # crowded long
assert gates.check_signal(GOOD | {"direction": "short", "funding": 0.002}, T)["choice"] == "accept"  # crowded longs help a short
r = gates.check_signal(GOOD, T)
assert abs(sum(r["probabilities"].values()) - 1) < 0.01 and r["confidence"] == max(r["probabilities"].values())

# review_trade: no fill, no done
fills = [{"status": "closed", "filled": 1}, {"status": "closed", "filled": 1}]
closed = {"is_open": False, "close_rate": 10, "close_date": "x", "orders": fills, "close_profit": -0.031, "exit_reason": "stop_loss"}
assert gates.review_trade(closed, T)["choice"] == "closed_as_planned"
assert gates.review_trade(closed | {"is_open": True}, T)["choice"] == "verify_more"
assert gates.review_trade(closed | {"orders": fills[:1]}, T)["choice"] == "verify_more"
assert gates.review_trade(closed | {"close_profit": -0.08}, T)["choice"] == "mismatch"
assert gates.review_trade(closed | {"exit_reason": "liquidation"}, T)["choice"] == "mismatch"

# guard_action
assert gates.guard_action("read")["choice"] == "allow"
assert gates.guard_action("open_trade", dry_run=True)["choice"] == "allow"
assert gates.guard_action("open_trade", dry_run=False)["choice"] == "human_review"
assert gates.guard_action("withdraw")["choice"] == "human_review"
assert gates.guard_action("rm -rf")["choice"] == "deny"

# route_task: only seats that exist
assert gates.route_task({"bot_reachable": False, "brief_fresh": True})["choice"] == "stand_down"
assert "trading_bot" not in gates.route_task({"bot_reachable": False})["probabilities"]
assert gates.route_task({"bot_reachable": True, "bot_state": "running", "brief_fresh": False})["choice"] == "SCOUT"
assert gates.route_task({"bot_reachable": True, "bot_state": "running", "brief_fresh": True})["choice"] == "trading_bot"
assert gates.route_task({"bot_reachable": True, "bot_state": "running", "money_action_pending": True})["choice"] == "human_review"

# grade: long 100, stop 97, target 106
assert grade("long", 100, [(0, 101, 99, 100), (1, 107, 100, 106)], T) == 2
assert grade("long", 100, [(0, 107, 96, 100)], T) == -1  # same candle -> stop
assert grade("short", 100, [(0, 101, 93, 94)], T) == 2
assert grade("long", 100, [(0, 101, 99, 100)], T) == 0

# tune: high scores win, low scores lose -> threshold moves up one step
recs = [{"score": 2.5, "r": 2}] * 12 + [{"score": 1.75, "r": -1}] * 12 + [{"score": 1.0, "r": -1}] * 30
new, note = gates.tune(recs, T)
assert new["accept_score"] == T["accept_score"] + T["tune_step"], note
assert gates.tune([], T)[0] == T

# bridge refuses anything that isn't a read
try:
    bridge.bot("forceenter")
    raise AssertionError("bridge allowed a write endpoint")
except ValueError:
    pass

print("all desk checks passed")
