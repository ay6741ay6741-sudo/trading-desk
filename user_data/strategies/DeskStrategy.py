"""Breakout strategy whose entries must pass the desk's signal gate.

The strategy only proposes (24h channel breakouts, both directions).
desk.gates.check_signal decides accept / verify_more / reject. Only "accept" trades.
Hard limits (stake, stop, max trades, dry_run) live in config.json, not here.
"""
import json
import sys
import time
from pathlib import Path

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desk import gates  # noqa: E402

BTC = "BTC/USDT:USDT"


class DeskStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    stoploss = -0.03
    minimal_roi = {"0": 0.06}
    startup_candle_count = 500
    process_only_new_candles = True
    use_exit_signal = True

    def informative_pairs(self):
        return [(BTC, self.timeframe)]

    def populate_indicators(self, df: DataFrame, metadata: dict) -> DataFrame:
        df["ema50"] = ta.EMA(df, timeperiod=50)
        df["ema200"] = ta.EMA(df, timeperiod=200)
        df["hi24"] = df["high"].rolling(24).max().shift(1)
        df["lo24"] = df["low"].rolling(24).min().shift(1)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(480).mean()  # vs 20-day average
        btc = df if metadata["pair"] == BTC else self.dp.get_pair_dataframe(BTC, self.timeframe)
        b = btc[["date", "open", "close"]].copy()
        b["btc_vs_open"] = b["close"] / b.groupby(b["date"].dt.floor("D"))["open"].transform("first") - 1
        return df.merge(b[["date", "btc_vs_open"]], on="date", how="left")

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[(df["close"] > df["hi24"]) & (df["volume"] > 0), "enter_long"] = 1
        df.loc[(df["close"] < df["lo24"]) & (df["volume"] > 0), "enter_short"] = 1
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["close"] < df["lo24"], "exit_long"] = 1
        df.loc[df["close"] > df["hi24"], "exit_short"] = 1
        return df

    def _funding(self, pair):
        """Funding rate from the scout's latest snapshot, None if older than 2h or missing."""
        try:
            scout = json.loads((ROOT / "state" / "scout.json").read_text())
            if time.time() - scout["ts"] < 7200:
                return scout["pairs"][pair]["funding"]
        except (OSError, KeyError, ValueError):
            pass
        return None

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time,
                            entry_tag, side, **kwargs) -> bool:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df.empty:
            return False
        last = df.iloc[-1]
        long = side == "long"
        btc = last["btc_vs_open"]
        ev = {
            "pair": pair,
            "direction": side,
            "volume_ratio": float(last["vol_ratio"]),
            "trend_ok": bool(last["ema50"] > last["ema200"]) == long,
            "btc_conflict": None if btc != btc else bool(btc < 0 if long else btc > 0),
            "funding": self._funding(pair),
            "candle_age_h": round((current_time - last["date"].to_pydatetime()).total_seconds() / 3600 - 1, 2),
            "whitelisted": pair in self.dp.current_whitelist(),
        }
        result = gates.check_signal(ev)
        if self.dp.runmode.value in ("dry_run", "live"):
            gates.log_decision("check_signal", ev | {"price": rate, "time": current_time.isoformat()}, result)
        return result["choice"] == "accept"
