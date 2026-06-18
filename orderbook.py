
import logging
from typing import Dict, List, Optional, Tuple

from data import fetch_order_book, fetch_trades, fetch_current_price
from config import ORDER_IMBALANCE_RATIO, TAKER_VOLUME_SURGE, TRAILING_STOP_TRIGGER, TRAILING_STOP_MOVE, MODE_CONFIGS, SENTIMENT_CONFLICT_FACTOR, CHAIN_PRESSURE_FACTOR

logger = logging.getLogger(__name__)


def analyze_order_book(symbol: str = "BTCUSDT") -> Dict:
    try:
        ob = fetch_order_book(symbol, 20)
    except Exception as e:
        return {"imbalance": "数据不可用", "bid_vol": 0, "ask_vol": 0, "ratio": 1.0, "details": [f"订单簿获取失败: {e}"]}
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])
    details = []
    ratio = 1.0
    imbalance = "均衡 ⚖️"
    if ask_vol > 0:
        ratio = bid_vol / ask_vol
        if ratio > ORDER_IMBALANCE_RATIO:
            imbalance = "买盘占优 🟢"
            details.append(f"买/卖比 = {ratio:.2f} > 1.2，买盘支撑较强")
        elif 1.0 / ratio > ORDER_IMBALANCE_RATIO:
            imbalance = "卖盘占优 🔴"
            details.append(f"卖/买比 = {1/ratio:.2f} > 1.2，卖盘抛压较大")
        else:
            details.append(f"买/卖比 = {ratio:.2f}，订单簿均衡")
    details.append(f"前10档买盘总量: {bid_vol:.4f} BTC | 前10档卖盘总量: {ask_vol:.4f} BTC")
    return {"imbalance": imbalance, "bid_vol": round(bid_vol, 4), "ask_vol": round(ask_vol, 4), "ratio": round(ratio, 2), "details": details}


def analyze_taker_volumes(symbol: str = "BTCUSDT") -> Dict:
    try:
        trades = fetch_trades(symbol, 100)
    except Exception as e:
        return {"panic": False, "taker_sell_ratio": 0.5, "details": [f"成交数据获取失败: {e}"]}
    if len(trades) < 20:
        return {"panic": False, "taker_sell_ratio": 0.5, "details": ["成交数据不足"]}
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
            details.append(f"⚠️ 主动卖出占比 {sell_ratio_20:.0%}，突增 {surge:.1f}x，恐慌信号！")
        else:
            details.append(f"主动卖出占比 {sell_ratio_20:.0%}（基线 {sell_ratio_100:.0%}），正常范围")
    return {"panic": panic, "taker_sell_ratio": round(sell_ratio_20, 4), "details": details}


class RiskManager:
    def __init__(self, mode: str, current_atr: float, current_price: float):
        self.config = MODE_CONFIGS[mode]
        self.atr = current_atr
        self.price = current_price
        self.mode_label = "激进" if mode == "aggressive" else "稳健"

    def calculate_stop_loss(self, direction: str) -> float:
        offset = self.atr * self.config.atr_multiplier
        return round(self.price - offset, 1) if direction == "long" else round(self.price + offset, 1)

    def calculate_take_profit_1(self, direction: str, stop_loss: float) -> float:
        risk = abs(self.price - stop_loss)
        return round(self.price + risk, 1) if direction == "long" else round(self.price - risk, 1)

    def calculate_take_profit_2_entry(self, direction: str, stop_loss: float) -> Dict:
        return {"trigger_step": self.atr * TRAILING_STOP_TRIGGER, "move_step": self.atr * TRAILING_STOP_MOVE,
                "initial_stop": stop_loss,
                "description": f"每涨{TRAILING_STOP_TRIGGER}倍ATR({self.atr * TRAILING_STOP_TRIGGER:.1f})，止损上移{TRAILING_STOP_MOVE}倍ATR({self.atr * TRAILING_STOP_MOVE:.1f})"}

    def calculate_position_size(self, account_balance: float, sentiment_conflict: bool = False, chain_pressure: bool = False) -> Dict:
        base_pct = self.config.position_pct
        final_factor = 1.0
        factor_desc = []
        if sentiment_conflict:
            final_factor *= SENTIMENT_CONFLICT_FACTOR
            factor_desc.append(f"情绪冲突×{SENTIMENT_CONFLICT_FACTOR}")
        if chain_pressure:
            final_factor *= CHAIN_PRESSURE_FACTOR
            factor_desc.append(f"链上抛压×{CHAIN_PRESSURE_FACTOR}")
        final_pct = base_pct * final_factor
        position_value = account_balance * final_pct
        return {"base_pct": base_pct, "final_pct": round(final_pct, 4), "position_value": round(position_value, 2),
                "position_btc": round(position_value / self.price, 6) if self.price > 0 else 0,
                "factor_breakdown": " × ".join(factor_desc) if factor_desc else "标准仓位"}

    def summary(self, direction: str, entry_price: float, sentiment_conflict: bool, chain_pressure: bool) -> Dict:
        sl = self.calculate_stop_loss(direction)
        tp1 = self.calculate_take_profit_1(direction, sl)
        tp2 = self.calculate_take_profit_2_entry(direction, sl)
        pos = self.calculate_position_size(10000, sentiment_conflict, chain_pressure)
        return {"direction": direction, "entry": entry_price, "stop_loss": sl, "take_profit_1": tp1,
                "take_profit_2_params": tp2, "atr": round(self.atr, 1), "position": pos,
                "risk_per_trade": round(abs(entry_price - sl) / entry_price * 100, 2),
                "reward_risk": round(abs(tp1 - entry_price) / abs(sl - entry_price), 2)}
