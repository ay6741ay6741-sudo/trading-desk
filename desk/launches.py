"""Launch desk: brand-new Solana tokens, rug gate, paper ledger. Paper money only.

Every desk run (3x/hour): discover tokens on DexScreener, pull safety data
(mint/freeze authority from Solana RPC, RugCheck report), run gates.check_launch,
open/close paper positions at observed prices minus a 1%/side cost.

Honest limit: runs every ~20 min, so this follows tokens that survived their first
minutes; it cannot snipe launches, and stops fill at the next observed price (gaps happen).
Every decided token is price-tracked for 24h and graded, so the weekly review can tune
launch_accept from real outcomes.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

from desk import bridge, gates

LIM = {**gates.LIMITS_ALL["launches"], "paper_only": gates.LIMITS_ALL["paper_only"]}
DEX = "https://api.dexscreener.com"
RPC = "https://api.mainnet-beta.solana.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report/summary"
MAX_SAFETY_CHECKS = 12  # per run, keeps RugCheck/RPC polite


def new_state():
    return {"wallet": LIM["wallet"], "next_id": 1, "open": {}, "closed": [], "watch": {}}


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, body, {"Content-Type": "application/json", "User-Agent": "trading-desk"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["result"]


def discover():
    mints = {}
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        try:
            for x in bridge._get(DEX + path):
                if x.get("chainId") == "solana":
                    mints[x["tokenAddress"]] = 1
        except Exception:
            pass  # one feed down is fine
    return list(mints)


def best_pairs(mints):
    """Highest-liquidity pair per mint where the mint is the base token."""
    out = {}
    for i in range(0, len(mints), 30):
        for p in bridge._get(f"{DEX}/tokens/v1/solana/{','.join(mints[i:i + 30])}"):
            m = p["baseToken"]["address"]
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if m in mints and liq >= ((out.get(m) or {}).get("liquidity") or {}).get("usd", -1):
                out[m] = p
        time.sleep(0.2)
    return out


def authorities(mints):
    out = {}
    try:
        for m, acc in zip(mints, rpc("getMultipleAccounts", [mints, {"encoding": "jsonParsed"}])["value"]):
            info = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") if acc else None
            out[m] = (bool(info.get("mintAuthority")), bool(info.get("freezeAuthority"))) if info else (None, None)
    except Exception:
        out = {m: (None, None) for m in mints}
    return out


def rugcheck(mint):
    try:
        r = bridge._get(RUG.format(mint))
        return {"score": r.get("score_normalised"), "lp_locked_pct": r.get("lpLockedPct"),
                "danger": [x["name"] for x in r.get("risks", []) if x.get("level") == "danger"]}
    except Exception:
        return {"score": None, "lp_locked_pct": None, "danger": []}


def evidence(p, now):
    liq = (p.get("liquidity") or {}).get("usd") or 0
    tx = (p.get("txns") or {}).get("h1") or {}
    ch = p.get("priceChange") or {}
    return {"mint": p["baseToken"]["address"], "symbol": p["baseToken"].get("symbol", "?"), "dex": p.get("dexId"),
            "price": float(p.get("priceUsd") or 0), "liquidity": liq, "fdv": p.get("fdv") or 0,
            "vol_h1": (p.get("volume") or {}).get("h1") or 0, "buys_h1": tx.get("buys", 0), "sells_h1": tx.get("sells", 0),
            "chg_h1": ch.get("h1") or 0, "chg_h6": ch.get("h6") or 0,
            "age_h": (now - (p.get("pairCreatedAt") or now * 1000) / 1000) / 3600, "url": p.get("url")}


def _close(st, mint, pos, price, now, reason):
    fill = price * (1 - LIM["cost"])
    ratio = fill / pos["open_rate"] - 1
    st["wallet"] += pos["stake"] * (1 + ratio)
    trade = {**pos, "is_open": False, "close_rate": fill, "close_timestamp": now * 1000,
             "close_date": datetime.fromtimestamp(now, timezone.utc).isoformat(), "close_profit": round(ratio, 6),
             "profit_abs": round(pos["stake"] * ratio, 4), "exit_reason": reason,
             "orders": [{"status": "closed", "filled": pos["amount"]}, {"status": "closed", "filled": pos["amount"]}]}
    st["closed"].append(trade)
    del st["open"][mint]
    return trade


def grade_path(entry, path):
    """+2 if +target seen before -stop, -1 if stop first, 0 neither (20-min snapshots)."""
    for _, px in path:
        if px <= entry * (1 + LIM["stoploss"]):
            return -1
        if px >= entry * (1 + LIM["target"]):
            return 2
    return 0


def run(st, now=None, t=None):
    """One pass. Returns events [(kind, payload)]."""
    now, t = now or time.time(), t or gates.load_thresholds()
    events = []
    watch = st["watch"]
    mints = list(dict.fromkeys(discover() + list(watch) + list(st["open"])))
    pairs = best_pairs(mints) if mints else {}

    # 1) exits on open paper positions
    for mint, pos in list(st["open"].items()):
        p = pairs.get(mint)
        price = float(p["priceUsd"]) if p and p.get("priceUsd") else None
        liq = ((p or {}).get("liquidity") or {}).get("usd") or 0
        pos["last_price"], pos["last_liquidity"] = price, liq
        if price is None or liq < 0.3 * pos["entry_liquidity"]:
            exit_px = (price or pos["open_rate"]) * 0.85 if price else pos["open_rate"] * 0.1
            events.append(("close", _close(st, mint, pos, exit_px, now, "rug_exit")))
        elif price <= pos["stop"]:
            events.append(("close", _close(st, mint, pos, price, now, "stop_loss")))
        elif price >= pos["target"]:
            events.append(("close", _close(st, mint, pos, price, now, "roi")))
        elif now - pos["open_ts"] > LIM["max_hold_h"] * 3600:
            events.append(("close", _close(st, mint, pos, price, now, "time_exit")))

    # 2) price-track decided tokens for 24h, then grade them for self-tuning
    for mint, w in list(watch.items()):
        p = pairs.get(mint)
        if w.get("first_score") is not None and p and p.get("priceUsd"):
            w["path"].append([int(now), float(p["priceUsd"])])
        if now - w["first_seen"] > 24 * 3600:
            if w.get("first_score") is not None and w.get("entry_price"):
                with open(gates.STATE / "launch_graded.jsonl", "a") as f:
                    f.write(json.dumps({"ts": now, "symbol": w["symbol"], "score": w["first_score"], "choice": w["first_choice"],
                                        "r": grade_path(w["entry_price"], w["path"])}) + "\n")
            del watch[mint]

    # 3) new decisions: cheap filters first, safety data only for survivors
    cands = []
    for mint, p in pairs.items():
        w = watch.get(mint, {})
        if mint in st["open"] or w.get("final") or now - w.get("last_check", 0) < 3600:
            continue
        ev = evidence(p, now)
        if ev["age_h"] > LIM["max_age_h"] or not ev["price"]:
            continue
        cands.append(ev)
    cands.sort(key=lambda e: -e["liquidity"])
    cands = cands[:MAX_SAFETY_CHECKS]
    auth = authorities([e["mint"] for e in cands]) if cands else {}
    for ev in cands:
        ev["mint_authority"], ev["freeze_authority"] = auth.get(ev["mint"], (None, None))
        rc = rugcheck(ev["mint"]) if ev["dex"] in LIM["dexes"] and ev["liquidity"] >= LIM["min_liquidity"] else {"score": 0, "lp_locked_pct": None, "danger": []}
        ev["rug_score"], ev["lp_locked_pct"], ev["rug_danger"] = rc["score"], rc["lp_locked_pct"], rc["danger"]
        res = gates.check_launch(ev, t, LIM)
        w = watch.setdefault(ev["mint"], {"first_seen": now, "symbol": ev["symbol"], "path": []})
        events.append(("decision", {"inputs": ev, **res, "changed": w.get("last_choice") != res["choice"]}))
        w["last_check"], w["last_choice"] = now, res["choice"]
        if res["score"] is not None and w.get("first_score") is None:
            w.update(first_score=res["score"], first_choice=res["choice"], entry_price=ev["price"])
        w["final"] = res.get("final", False)  # safety rejects are final; thin/early tokens get re-checked hourly
        if res["choice"] != "accept" or len(st["open"]) >= LIM["max_open_trades"] or st["wallet"] < LIM["stake"]:
            continue
        if gates.guard_action("open_trade", dry_run=LIM["paper_only"])["choice"] != "allow":
            continue
        fill = ev["price"] * (1 + LIM["cost"])
        pos = {"trade_id": st["next_id"], "pair": ev["symbol"] + "/USD", "mint": ev["mint"], "url": ev["url"],
               "is_short": False, "open_rate": fill, "open_ts": now, "stake": LIM["stake"], "amount": LIM["stake"] / fill,
               "open_date": datetime.fromtimestamp(now, timezone.utc).isoformat(), "entry_liquidity": ev["liquidity"],
               "stop": fill * (1 + LIM["stoploss"]), "target": fill * (1 + LIM["target"]), "last_price": ev["price"]}
        st["wallet"] -= LIM["stake"]
        st["next_id"] += 1
        st["open"][ev["mint"]] = pos
        events.append(("open", pos))
    return events
