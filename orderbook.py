
import logging
from typing import Dict, List, Optional, Tuple

from data import fetch_order_book, fetch_trades, fetch_current_price
from config import ORDER_IMBALANCE_RATIO, TAKER_VOLUME_SURGE, TRAILING_STOP_TRIGGER, TRAILING_STOP_MOVE, MODE_CONFIGS, SENTIMENT_CONFLICT_FACTOR, CHAIN_PRESSURE_FACTOR

logger = logging.getLogger(__name__)


def analyze_order_book(symbol: str = "BTCUSDT") -> Dict:
    try:
        ob = fetch_order_book(symbol, 20)
    except Exception as e:
        return {"imbalance": "\u6570\u636e\u4e0d\u53ef\u7528", "bid_vol": 0, "ask_vol": 0, "ratio": 1.0, "details": [f"\u8ba2\u5355\u7c3f\u83b7\u53d6\u5931\u8d25: {e}"]}
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])
    details = []
    ratio = 1.0
    imbalance = "\u5747\u8861 \u2696\ufe0f"
    if ask_vol > 0:
        ratio = bid_vol / ask_vol
        if ratio > ORDER_IMBALANCE_RATIO:
            imbalance = "\u4e70\u76d8\u5360\u4f18 \U0001f7e2"
            details.append(f"\u4e70/\u5356\u6bd4 = {ratio:.2f} > 1.2\uff0c\u4e70\u76d8\u652f\u6491\u8f83\u5f3a")
        elif 1.0 / ratio > ORDER_IMBALANCE_RATIO:
            imbalance = "\u5356\u76d8\u5360\u4f18 \U0001f534"
            details.append(f"\u5356/\u4e70\u6bd4 = {1/ratio:.2f} > 1.2\uff0c\u5356\u76d8\u629b\u538b\u8f83\u5927")
        else:
            details.append(f"\u4e70/\u5356\u6bd4 = {ratio:.2f}\uff0c\u8ba2\u5355\u7c3f\u5747\u8861")
    details.append(f"\u524d10\u6863\u4e70\u76d8\u603b\u91cf: {bid_vol:.4f} BTC | \u524d10\u6863\u5356\u76d8\u603b\u91cf: {ask_vol:.4f} BTC")
    return {"imbalance": imbalance, "bid_vol": round(bid_vol, 4), "ask_vol": round(ask_vol, 4), "ratio": round(ratio, 2), "details": details}


def analyze_taker_volumes(symbol: str = "BTCUSDT") -> Dict:
    try:
        trades = fetch_trades(symbol, 100)
    except Exception as e:
        return {"panic": False, "taker_sell_ratio": 0.5, "details": [f"\u6210\u4ea4\u6570\u636e\u83b7\u53d6\u5931\u8d25: {e}"]}
    if len(trades) < 20:
        return {"panic": False, "taker_sell_ratio": 0.5, "details": ["\u6210\u4ea4\u6570\u636e\u4e0d\u8db3"]}
    recent_20 = trades[-20:]
    recent_100 = trades
    sell_count_20 = sum(1 for t in recent_20 if t.get("isBuyerMaker", True))
    sell_count_100 = sum(1 for t in recent_100 if t.get("isBuyerMaker", True))
    sell_ratio_20 = sell_count_20 / len(recent_20)
    sell_ratio_100 = sell_count_100 / len(recent_100)
    details = []
    panic = False
    if sell_ratio_100 > 0:
        surge = sell_ratio_20 / sell_ratio_100
        if surge > TAKER_VOLUME_SURGE and sell_ratio_20 > 0.65:
            panic = True
            details.append(f"\u26a0\ufe0f \u4e3b\u52a8\u5356\u51fa\u5360\u6bd4 {sell_ratio_20:.0%}\uff0c\u7a81\u589e {surge:.1f}x\uff0c\u6050\u614c\u4fe1\u53f7\uff01")
        else:
            details.append(f"\u4e3b\u52a8\u5356\u51fa\u5360\u6bd4 {sell_ratio_20:.0%}\uff08\u57fa\u7ebf {sell_ratio_100:.0%}\uff09\uff0c\u6b63\u5e38\u8303\u56f4")
    return {"panic": panic, "taker_sell_ratio": round(sell_ratio_20, 4), "details": details}


class RiskManager:
    def __init__(self, mode: str, current_atr: float, current_price: float):
        self.config = MODE_CONFIGS[mode]
        self.atr = current_atr
        self.price = current_price
        self.mode_label = "\u6fc0\u8fdb" if mode == "aggressive" else "\u7a33\u5065"

    def calculate_stop_loss(self, direction: str) -> float:
        offset = self.atr * self.config.atr_multiplier
        return round(self.price - offset, 1) if direction == "long" else round(self.price + offset, 1)

    def calculate_take_profit_1(self, direction: str, stop_loss: float) -> float:
        risk = abs(self.price - stop_loss)
        return round(self.price + risk, 1) if direction == "long" else round(self.price - risk, 1)

    def calculate_take_profit_2_entry(self, direction: str, stop_loss: float) -> Dict:
        return {"trigger_step": self.atr * TRAILING_STOP_TRIGGER, "move_step": self.atr * TRAILING_STOP_MOVE,
                "initial_stop": stop_loss,
                "description": f"\u6bcf\u6da8{TRAILING_STOP_TRIGGER}\u500dATR({self.atr * TRAILING_STOP_TRIGGER:.1f})\uff0c\u6b62\u635f\u4e0a\u79fb{TRAILING_STOP_MOVE}\u500dATR({self.atr * TRAILING_STOP_MOVE:.1f})"}

    def calculate_position_size(self, account_balance: float, sentiment_conflict: bool = False, chain_pressure: bool = False) -> Dict:
        base_pct = self.config.position_pct
        final_factor = 1.0
        factor_desc = []
        if sentiment_conflict:
            final_factor *= SENTIMENT_CONFLICT_FACTOR
            factor_desc.append(f"\u60c5\u7eea\u51b2\u7a81\u00d7{SENTIMENT_CONFLICT_FACTOR}")
        if chain_pressure:
            final_factor *= CHAIN_PRESSURE_FACTOR
            factor_desc.append(f"\u94fe\u4e0a\u629b\u538b\u00d7{CHAIN_PRESSURE_FACTOR}")
        final_pct = base_pct * final_factor
        position_value = account_balance * final_pct
        return {"base_pct": base_pct, "final_pct": round(final_pct, 4), "position_value": round(position_value, 2),
                "position_btc": round(position_value / self.price, 6) if self.price > 0 else 0,
                "factor_breakdown": " \u00d7 ".join(factor_desc) if factor_desc else "\u6807\u51c6\u4ed3\u4f4d"}

    def summary(self, direction: str, entry_price: float, sentiment_conflict: bool, chain_pressure: bool) -> Dict:
        sl = self.calculate_stop_loss(direction)
        tp1 = self.calculate_take_profit_1(direction, sl)
        tp2 = self.calculate_take_profit_2_entry(direction, sl)
        pos = self.calculate_position_size(10000, sentiment_conflict, chain_pressure)
        return {"direction": direction, "entry": entry_price, "stop_loss": sl, "take_profit_1": tp1,
                "take_profit_2_params": tp2, "atr": round(self.atr, 1), "position": pos,
                "risk_per_trade": round(abs(entry_price - sl) / entry_price * 100, 2),
                "reward_risk": round(abs(tp1 - entry_price) / abs(sl - entry_price), 2)}
