"""Desk Bridge: read-only.

Reads the Freqtrade bot (GET only, fixed endpoint list) and OKX public market data.
There is no function here that places, changes or cancels an order. Orders leave from the bot only.
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request

FT_URL = os.environ.get("FREQTRADE_API_URL", "http://127.0.0.1:8080")
READ_ENDPOINTS = {"ping", "status", "profit", "balance", "whitelist", "trades", "show_config"}
OKX = "https://www.okx.com/api/v5"


def _get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-desk", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def bot(endpoint, **params):
    """GET /api/v1/<endpoint> on the bot. Anything outside READ_ENDPOINTS is refused."""
    if endpoint not in READ_ENDPOINTS:
        raise ValueError(f"'{endpoint}' is not a read endpoint")
    creds = f"{os.environ['FREQTRADE__API_SERVER__USERNAME']}:{os.environ['FREQTRADE__API_SERVER__PASSWORD']}"  # same .env as the bot
    auth = {"Authorization": "Basic " + base64.b64encode(creds.encode()).decode()}
    query = "?" + urllib.parse.urlencode(params) if params else ""
    return _get(f"{FT_URL}/api/v1/{endpoint}{query}", auth)


def okx_inst(pair):
    return pair.split("/")[0] + "-USDT-SWAP"  # BTC/USDT:USDT -> BTC-USDT-SWAP


def okx(path, **params):
    d = _get(f"{OKX}{path}?{urllib.parse.urlencode(params)}")
    if d.get("code") != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg')}")
    return d["data"]


def scout(pairs):
    """SCOUT: funding, open interest, 24h move per pair. Public data, no key."""
    out = {}
    for p in pairs:
        inst = okx_inst(p)
        f = okx("/public/funding-rate", instId=inst)[0]
        oi = okx("/public/open-interest", instType="SWAP", instId=inst)[0]
        tk = okx("/market/ticker", instId=inst)[0]
        out[p] = {
            "funding": float(f["fundingRate"]),
            "open_interest": float(oi["oiCcy"]),
            "last": float(tk["last"]),
            "change_24h": float(tk["last"]) / float(tk["open24h"]) - 1,
        }
    return {"ts": time.time(), "pairs": out}


def candles_after(pair, t_sec, hours=48):
    """1h candles (ts, high, low, close) starting at t_sec, for grading past decisions."""
    data = okx("/market/history-candles", instId=okx_inst(pair), bar="1H",
               after=int((t_sec + (hours + 1) * 3600) * 1000), limit=100)
    rows = [(int(r[0]) / 1000, float(r[2]), float(r[3]), float(r[4])) for r in data]
    start = t_sec - t_sec % 3600  # the candle that opened at the decision hour
    return sorted(r for r in rows if r[0] >= start)
