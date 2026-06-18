
import json
import logging
import time
from datetime import datetime
from typing import Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from data import (fetch_funding_rate, fetch_long_short_ratio, fetch_fear_greed_index,
                  fetch_exchange_netflow, fetch_current_price)
from onchain import comprehensive_onchain_check
from config import (FUNDING_RATE_BULLISH, FUNDING_RATE_BEARISH, LONG_SHORT_RATIO_BULLISH,
                    LONG_SHORT_RATIO_BEARISH, FEAR_GREED_BULLISH, FEAR_GREED_BEARISH,
                    SOCIAL_SURGE_THRESHOLD, NEWS_VOLATILITY_THRESHOLD, HIGH_IMPACT_EVENTS,
                    WHALE_TRANSFER_THRESHOLD, EXCHANGE_NETFLOW_WARN)

logger = logging.getLogger(__name__)

# ---- TradingEconomics API ----
# Free tier: 100 requests/day, requires key from https://tradingeconomics.com/api
TE_API_KEY = ""        # Set your key or env var
TE_CALENDAR_URL = "https://api.tradingeconomics.com/calendar"
# Simple in-memory cache for calendar data (1 hour TTL)
_calendar_cache = None
_calendar_ts = 0.0
_CALENDAR_TTL = 3600


def _fetch_json(url: str, timeout: int = 10) -> dict:
    """Minimal HTTP GET helper."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ═══════════════════════════════════════════════════════════════════
# Market Sentiment Scoring
# ═══════════════════════════════════════════════════════════════════

def score_sentiment(current_price: float) -> Dict:
    total = 0
    details = []
    try:
        fr = fetch_funding_rate()
        if fr < FUNDING_RATE_BULLISH:
            total += 1
            details.append({"indicator": "资金费率", "value": f"{fr:.6f}", "score": 1,
                           "note": "费率<阈值，偏多（市场过度看空）"})
        elif fr > FUNDING_RATE_BEARISH:
            total -= 1
            details.append({"indicator": "资金费率", "value": f"{fr:.6f}", "score": -1,
                           "note": "费率>阈值，偏空（多头拥挤）"})
        else:
            details.append({"indicator": "资金费率", "value": f"{fr:.6f}", "score": 0, "note": "正常范围"})
    except Exception as e:
        details.append({"indicator": "资金费率", "value": "N/A", "score": 0, "note": f"获取失败: {e}"})
    try:
        lsr = fetch_long_short_ratio()
        if lsr is not None:
            if lsr < LONG_SHORT_RATIO_BULLISH:
                total += 1
                details.append({"indicator": "多空比", "value": f"{lsr:.2f}", "score": 1, "note": "多空比<0.7，偏多"})
            elif lsr > LONG_SHORT_RATIO_BEARISH:
                total -= 1
                details.append({"indicator": "多空比", "value": f"{lsr:.2f}", "score": -1, "note": "多空比>1.5，偏空"})
            else:
                details.append({"indicator": "多空比", "value": f"{lsr:.2f}", "score": 0, "note": "正常范围"})
        else:
            details.append({"indicator": "多空比", "value": "N/A", "score": 0, "note": "数据不可用"})
    except Exception as e:
        details.append({"indicator": "多空比", "value": "N/A", "score": 0, "note": f"获取失败: {e}"})
    try:
        fg = fetch_fear_greed_index()
        if fg is not None:
            if fg < FEAR_GREED_BULLISH:
                total += 1
                details.append({"indicator": "恐惧贪婪", "value": str(fg), "score": 1, "note": f"指数{fg}<25，极度恐惧"})
            elif fg > FEAR_GREED_BEARISH:
                total -= 1
                details.append({"indicator": "恐惧贪婪", "value": str(fg), "score": -1, "note": f"指数{fg}>75，极度贪婪"})
            else:
                details.append({"indicator": "恐惧贪婪", "value": str(fg), "score": 0, "note": "中性范围"})
        else:
            details.append({"indicator": "恐惧贪婪", "value": "N/A", "score": 0, "note": "数据不可用"})
    except Exception as e:
        details.append({"indicator": "恐惧贪婪", "value": "N/A", "score": 0, "note": f"获取失败: {e}"})
    details.append({"indicator": "社交媒体热度", "value": "N/A", "score": 0, "note": "需接入Twitter/LunarCrush API（当前为占位）"})
    if total >= 2:
        label = "偏多 📈"
    elif total <= -2:
        label = "偏空 📉"
    else:
        label = "中性 ⚖️"
    return {"total": total, "details": details, "label": label}


# ═══════════════════════════════════════════════════════════════════
# Macro Event Detection
# ═══════════════════════════════════════════════════════════════════

def fetch_calendar_events(country: str = "united states",
                          importance: str = "3",
                          limit: int = 10) -> Optional[list]:
    """Fetch upcoming economic events from TradingEconomics Calendar API.

    importance: '3' = high impact only, '2,3' = medium+high
    """
    global _calendar_cache, _calendar_ts
    now = time.time()
    if _calendar_cache is not None and now - _calendar_ts < _CALENDAR_TTL:
        return _calendar_cache

    if not TE_API_KEY:
        logger.debug("TradingEconomics API key not set, using date heuristics")
        return None

    try:
        url = (
            f"{TE_CALENDAR_URL}"
            f"?c={TE_API_KEY}"
            f"&country={country}"
            f"&importance={importance}"
            f"&limit={limit}"
            f"&format=json"
        )
        data = _fetch_json(url)
        events = []
        for evt in data[:limit]:
            events.append({
                "title": evt.get("Event", ""),
                "country": evt.get("Country", ""),
                "date": evt.get("Date", ""),
                "time": evt.get("Time", ""),
                "importance": evt.get("Importance", ""),
                "actual": evt.get("Actual", ""),
                "forecast": evt.get("Forecast", ""),
                "previous": evt.get("Previous", ""),
            })
        _calendar_cache = events
        _calendar_ts = now
        return events
    except Exception as e:
        logger.warning(f"TradingEconomics fetch failed: {e}")
        return None


# Predefined high-impact keywords for fallback + filtering
_HIGH_IMPACT_KEYWORDS = [
    "CPI", "非农", "FOMC", "GDP", "PCE", "美联储", "interest rate",
    "NFP", "unemployment", "PPI", "retail sales", "PMI", "ISM",
    "consumer confidence", "initial jobless claims", "durable goods",
]

_IMPORTANCE_LEVELS = {
    "3": "高",
    "2": "中",
    "1": "低",
}


def check_high_impact_events() -> Dict:
    """Detect upcoming high-impact macro events within 24-48 hours.

    Uses TradingEconomics API if key is set, falls back to date heuristics.
    """
    now = datetime.utcnow()
    hour = now.hour
    day = now.day
    month = now.month
    weekday = now.weekday()

    events = []
    has_event = False
    warning = ""
    source = "date_heuristics"

    # Try API first
    api_events = fetch_calendar_events(importance="3", limit=8)

    if api_events:
        source = "TradingEconomics"
        for evt in api_events:
            title = evt["title"]
            imp = _IMPORTANCE_LEVELS.get(evt["importance"], "?")
            events.append(f"{title} (重要性: {imp})")
            has_event = True
        if has_event:
            warning = (
                f"⚠️ 近期有{len(events)}个高重要度经济事件: "
                f"{'; '.join(events[:4])}，建议降低仓位或暂停交易"
            )
    else:
        # ---- Fallback: date-based heuristics ----
        # NFP: first Friday of the month, 8:30 AM ET (~12:30-13:30 UTC)
        if weekday == 4 and day <= 7 and 12 <= hour <= 14:
            events.append(f"非农(NFP)公布日 ({month}/{day})")
        # CPI: around 10th-15th of each month, ~12:30 UTC
        if 10 <= day <= 15 and 12 <= hour <= 14:
            events.append(f"CPI公布日 ({month}/{day})")
        # FOMC: 8 times/year, roughly every 6 weeks
        if month in [1, 3, 5, 6, 7, 9, 11, 12] and 20 <= day <= 27 and 18 <= hour <= 20:
            events.append(f"FOMC决议窗口 ({month}/{day})")
        # PPI: usually day after CPI
        if 11 <= day <= 16 and 12 <= hour <= 14:
            events.append(f"PPI公布窗口 ({month}/{day})")
        # GDP: quarterly, roughly end of Jan/Apr/Jul/Oct
        if month in [1, 4, 7, 10] and 25 <= day <= 31 and 12 <= hour <= 14:
            events.append(f"GDP公布窗口 ({month}/{day})")

        if events:
            has_event = True
            warning = (
                f"⚠️ 当前时段可能有宏观事件: {'; '.join(events)}"
                f"，建议停止15分钟级别操作"
            )

    if not has_event:
        warning = "无重大宏观事件（未来24小时）"

    return {
        "has_event": has_event,
        "events": events,
        "warning": warning,
        "source": source,
    }


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
            "note": "标记新闻驱动" if is_driven else "无异常波动"}


# ═══════════════════════════════════════════════════════════════════
# On-Chain Check (delegates to onchain.py)
# ═══════════════════════════════════════════════════════════════════

def check_onchain() -> Dict:
    """Run comprehensive on-chain analysis via onchain.py module.

    Returns status, details list, and position_factor multiplier.
    """
    try:
        result = comprehensive_onchain_check()
        return result
    except Exception as e:
        logger.warning(f"On-chain check failed: {e}")
        return {
            "status": "链上数据暂时不可用 ⚠️",
            "score": 0,
            "details": [f"获取失败: {e}"],
            "position_factor": 1.0,
        }
