"""Desk Bridge: read-only public market data from OKX. No keys, no account, no order endpoints."""
import json
import time
import urllib.parse
import urllib.request

OKX = "https://www.okx.com/api/v5"


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-desk"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def okx_inst(pair):
    return pair.split("/")[0] + "-USDT-SWAP"  # BTC/USDT:USDT -> BTC-USDT-SWAP


def okx(path, **params):
    d = _get(f"{OKX}{path}?{urllib.parse.urlencode(params)}")
    if d.get("code") != "0":
        raise RuntimeError(f"OKX {path}: {d.get('msg')}")
    return d["data"]


def candles(pair, n=600):
    """Last n CLOSED 1h candles, oldest first: (ts_sec, open, high, low, close, volume)."""
    rows, after = [], None
    while len(rows) < n:
        params = {"instId": okx_inst(pair), "bar": "1H", "limit": 100, **({"after": after} if after else {})}
        page = okx("/market/history-candles", **params)
        if not page:
            break
        rows += [r for r in page if r[8] == "1"]  # confirm flag: closed candles only
        after = page[-1][0]
        time.sleep(0.12)  # OKX: 20 requests / 2s
    out = sorted({int(r[0]) // 1000: (int(r[0]) // 1000, *map(float, r[1:6])) for r in rows}.values())
    return out[-n:]


def scout(pairs):
    """SCOUT: funding, open interest, 24h move per pair."""
    out = {}
    for p in pairs:
        inst = okx_inst(p)
        f = okx("/public/funding-rate", instId=inst)[0]
        oi = okx("/public/open-interest", instType="SWAP", instId=inst)[0]
        tk = okx("/market/ticker", instId=inst)[0]
        out[p] = {"funding": float(f["fundingRate"]), "open_interest": float(oi["oiCcy"]),
                  "last": float(tk["last"]), "change_24h": float(tk["last"]) / float(tk["open24h"]) - 1}
    return {"ts": time.time(), "pairs": out}


def candles_after(pair, t_sec, hours=48):
    """1h candles (ts, high, low, close) from t_sec on, for grading past decisions."""
    data = okx("/market/history-candles", instId=okx_inst(pair), bar="1H",
               after=int((t_sec + (hours + 1) * 3600) * 1000), limit=100)
    rows = [(int(r[0]) / 1000, float(r[2]), float(r[3]), float(r[4])) for r in data]
    start = t_sec - t_sec % 3600
    return sorted(r for r in rows if r[0] >= start)
