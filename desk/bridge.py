"""Desk Bridge: read-only public market data. No keys, no account, no order endpoints.

Prices: Coinbase SOL-USD / BTC-USD (real US dollars).
Sentiment: OKX SOL-USDT perpetual funding + open interest (no USD perp exists there; used as context only).
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

OKX = "https://www.okx.com/api/v5"
COINBASE = "https://api.exchange.coinbase.com"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-desk"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def product(pair):
    return pair.replace("/", "-")  # SOL/USD -> SOL-USD


def coinbase_candles(pair, start, end):
    """Closed 1h candles in [start, end), oldest first: (ts, open, high, low, close, volume)."""
    rows = _get(f"{COINBASE}/products/{product(pair)}/candles?" + urllib.parse.urlencode(
        {"granularity": 3600, "start": _iso(start), "end": _iso(end)}))
    now = time.time()
    # Coinbase row = [time, low, high, open, close, volume]; drop the candle still forming
    return sorted((r[0], r[3], r[2], r[1], r[4], r[5]) for r in rows if r[0] + 3600 <= now and start <= r[0] < end)


def candles(pair, n=600):
    """Last n CLOSED 1h candles, oldest first."""
    end = time.time() // 3600 * 3600 + 3600
    out = {}
    for k in range(0, n, 300):  # Coinbase returns at most 350 per call
        for c in coinbase_candles(pair, end - (k + 300) * 3600, end - k * 3600):
            out[c[0]] = c
        time.sleep(0.2)
    return sorted(out.values())[-n:]


def okx(path, **params):
    d = _get(f"{OKX}{path}?{urllib.parse.urlencode(params)}")
    if d.get("code") != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg')}")
    return d["data"]


def scout(pairs):
    """SCOUT: USD price + 24h move (Coinbase), perp funding + open interest (OKX)."""
    out = {}
    for p in pairs:
        st = _get(f"{COINBASE}/products/{product(p)}/stats")
        inst = p.split("/")[0] + "-USDT-SWAP"
        f = okx("/public/funding-rate", instId=inst)[0]
        oi = okx("/public/open-interest", instType="SWAP", instId=inst)[0]
        out[p] = {"last": float(st["last"]), "change_24h": float(st["last"]) / float(st["open"]) - 1,
                  "funding": float(f["fundingRate"]), "open_interest": float(oi["oiCcy"])}
    return {"ts": time.time(), "pairs": out}


def candles_after(pair, t_sec, hours=48):
    """1h candles (ts, high, low, close) from t_sec on, for grading past decisions."""
    start = t_sec - t_sec % 3600
    return [(c[0], c[2], c[3], c[4]) for c in coinbase_candles(pair, start, start + (hours + 1) * 3600)]
