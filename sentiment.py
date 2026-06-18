
import logging
from datetime import datetime
from typing import Dict, Optional

from data import (fetch_funding_rate, fetch_long_short_ratio, fetch_fear_greed_index,
                  fetch_exchange_netflow, fetch_current_price)
from config import (FUNDING_RATE_BULLISH, FUNDING_RATE_BEARISH, LONG_SHORT_RATIO_BULLISH,
                    LONG_SHORT_RATIO_BEARISH, FEAR_GREED_BULLISH, FEAR_GREED_BEARISH,
                    SOCIAL_SURGE_THRESHOLD, NEWS_VOLATILITY_THRESHOLD, HIGH_IMPACT_EVENTS,
                    WHALE_TRANSFER_THRESHOLD, EXCHANGE_NETFLOW_WARN)

logger = logging.getLogger(__name__)


def score_sentiment(current_price: float) -> Dict:
    total = 0
    details = []
    try:
        fr = fetch_funding_rate()
        if fr < FUNDING_RATE_BULLISH:
            total += 1
            details.append({"indicator": "\u8d44\u91d1\u8d39\u7387", "value": f"{fr:.6f}", "score": 1,
                           "note": "\u8d39\u7387<\u9608\u503c\uff0c\u504f\u591a\uff08\u5e02\u573a\u8fc7\u5ea6\u770b\u7a7a\uff09"})
        elif fr > FUNDING_RATE_BEARISH:
            total -= 1
            details.append({"indicator": "\u8d44\u91d1\u8d39\u7387", "value": f"{fr:.6f}", "score": -1,
                           "note": "\u8d39\u7387>\u9608\u503c\uff0c\u504f\u7a7a\uff08\u591a\u5934\u62e5\u6324\uff09"})
        else:
            details.append({"indicator": "\u8d44\u91d1\u8d39\u7387", "value": f"{fr:.6f}", "score": 0, "note": "\u6b63\u5e38\u8303\u56f4"})
    except Exception as e:
        details.append({"indicator": "\u8d44\u91d1\u8d39\u7387", "value": "N/A", "score": 0, "note": f"\u83b7\u53d6\u5931\u8d25: {e}"})
    try:
        lsr = fetch_long_short_ratio()
        if lsr is not None:
            if lsr < LONG_SHORT_RATIO_BULLISH:
                total += 1
                details.append({"indicator": "\u591a\u7a7a\u6bd4", "value": f"{lsr:.2f}", "score": 1, "note": "\u591a\u7a7a\u6bd4<0.7\uff0c\u504f\u591a"})
            elif lsr > LONG_SHORT_RATIO_BEARISH:
                total -= 1
                details.append({"indicator": "\u591a\u7a7a\u6bd4", "value": f"{lsr:.2f}", "score": -1, "note": "\u591a\u7a7a\u6bd4>1.5\uff0c\u504f\u7a7a"})
            else:
                details.append({"indicator": "\u591a\u7a7a\u6bd4", "value": f"{lsr:.2f}", "score": 0, "note": "\u6b63\u5e38\u8303\u56f4"})
        else:
            details.append({"indicator": "\u591a\u7a7a\u6bd4", "value": "N/A", "score": 0, "note": "\u6570\u636e\u4e0d\u53ef\u7528"})
    except Exception as e:
        details.append({"indicator": "\u591a\u7a7a\u6bd4", "value": "N/A", "score": 0, "note": f"\u83b7\u53d6\u5931\u8d25: {e}"})
    try:
        fg = fetch_fear_greed_index()
        if fg is not None:
            if fg < FEAR_GREED_BULLISH:
                total += 1
                details.append({"indicator": "\u6050\u60e7\u8d2a\u5a2a", "value": str(fg), "score": 1, "note": f"\u6307\u6570{fg}<25\uff0c\u6781\u5ea6\u6050\u60e7"})
            elif fg > FEAR_GREED_BEARISH:
                total -= 1
                details.append({"indicator": "\u6050\u60e7\u8d2a\u5a2a", "value": str(fg), "score": -1, "note": f"\u6307\u6570{fg}>75\uff0c\u6781\u5ea6\u8d2a\u5a2a"})
            else:
                details.append({"indicator": "\u6050\u60e7\u8d2a\u5a2a", "value": str(fg), "score": 0, "note": "\u4e2d\u6027\u8303\u56f4"})
        else:
            details.append({"indicator": "\u6050\u60e7\u8d2a\u5a2a", "value": "N/A", "score": 0, "note": "\u6570\u636e\u4e0d\u53ef\u7528"})
    except Exception as e:
        details.append({"indicator": "\u6050\u60e7\u8d2a\u5a2a", "value": "N/A", "score": 0, "note": f"\u83b7\u53d6\u5931\u8d25: {e}"})
    details.append({"indicator": "\u793e\u4ea4\u5a92\u4f53\u70ed\u5ea6", "value": "N/A", "score": 0, "note": "\u9700\u63a5\u5165Twitter/LunarCrush API\uff08\u5f53\u524d\u4e3a\u5360\u4f4d\uff09"})
    if total >= 2:
        label = "\u504f\u591a \U0001f4c8"
    elif total <= -2:
        label = "\u504f\u7a7a \U0001f4c9"
    else:
        label = "\u4e2d\u6027 \u2696\ufe0f"
    return {"total": total, "details": details, "label": label}


def check_high_impact_events() -> Dict:
    now = datetime.utcnow()
    weekday = now.weekday()
    day = now.day
    hour = now.hour
    month = now.month
    events = []
    if weekday == 4 and day <= 7 and hour in range(8, 10):
        events.append(f"\u975e\u519c(NFP)\u516c\u5e03\u65e5 ({month}/{day})")
    if 10 <= day <= 15 and hour in range(8, 10):
        events.append(f"CPI\u516c\u5e03\u65e5 ({month}/{day})")
    if month in [1, 3, 5, 7, 9, 11] and 20 <= day <= 30 and hour in range(12, 16):
        if day % 7 < 3:
            events.append(f"FOMC\u4f1a\u8bae\u7a97\u53e3 ({month}/{day})")
    if events:
        return {"has_event": True, "events": events,
                "warning": f"\u26a0\ufe0f \u672a\u67651\u5c0f\u65f6\u5185\u6709\u5b8f\u89c2\u4e8b\u4ef6: {'; '.join(events)}\uff0c\u5efa\u8bae\u505c\u6b6215\u5206\u949f\u7ea7\u522b\u64cd\u4f5c"}
    return {"has_event": False, "events": [], "warning": "\u65e0\u91cd\u5927\u5b8f\u89c2\u4e8b\u4ef6"}


def check_news_volatility(klines: list) -> Dict:
    if not klines or len(klines) < 2:
        return {"is_news_driven": False, "max_move_pct": 0.0}
    recent = klines[-4:] if len(klines) > 4 else klines
    max_move = 0.0
    for k in recent:
        move = max(abs(k["high"] - k["open"]) / k["open"], abs(k["low"] - k["open"]) / k["open"])
        max_move = max(max_move, move)
    is_driven = max_move >= NEWS_VOLATILITY_THRESHOLD
    return {"is_news_driven": is_driven, "max_move_pct": round(max_move * 100, 2),
            "note": "\u6807\u8bb0\u65b0\u95fb\u9a71\u52a8" if is_driven else "\u65e0\u5f02\u5e38\u6ce2\u52a8"}


def check_onchain() -> Dict:
    details = []
    position_factor = 1.0
    status = "\u6b63\u5e38 \u2705"
    try:
        netflow = fetch_exchange_netflow()
        if netflow is not None:
            if abs(netflow) > EXCHANGE_NETFLOW_WARN and netflow > 0:
                status = "\u26a0\ufe0f \u629b\u538b\u8b66\u544a"
                position_factor *= 0.7
                details.append(f"\u4ea4\u6613\u6240\u51c0\u6d41\u5165 {netflow:.0f} BTC/\u5c0f\u65f6 > \u9608\u503c\uff0c\u6f5c\u5728\u629b\u538b")
            elif abs(netflow) > EXCHANGE_NETFLOW_WARN and netflow < 0:
                details.append(f"\u4ea4\u6613\u6240\u51c0\u6d41\u51fa {abs(netflow):.0f} BTC/\u5c0f\u65f6\uff0c\u504f\u591a\u4fe1\u53f7")
            else:
                details.append(f"\u4ea4\u6613\u6240\u51c0\u6d41\u91cf {netflow if netflow else 0:.0f} BTC/\u5c0f\u65f6\uff0c\u6b63\u5e38")
        else:
            details.append("\u51c0\u6d41\u91cf\u6570\u636e: \u672a\u83b7\u53d6\uff08\u9700CryptoQuant/Glassnode API\uff09")
    except Exception as e:
        details.append(f"\u51c0\u6d41\u91cf\u68c0\u67e5\u5931\u8d25: {e}")
    details.append("\u5de8\u9cb8\u5f02\u52a8: \u672a\u63a5\u5165Whale Alert API\uff08\u5360\u4f4d\uff09")
    return {"status": status, "details": details, "position_factor": position_factor}
