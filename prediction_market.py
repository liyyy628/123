
"""币安预测市场API接入模块

Binance Prediction Market API launched 2026-06-08.
Requires: KYC + Prediction Account + SAS Authorization + API Key.

When API key is not configured, the module returns neutral/empty results
so the rest of the system degrades gracefully.

Documentation: https://www.binance.com/en/support/announcement/detail/1cfffee40a0d49c182e0b4366ea3f374
"""
import json
import logging
import os
import time
from typing import Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from config import BINANCE_BASE

logger = logging.getLogger(__name__)

# ---- Configuration ----
# Set via environment or edit directly:
#   export BINANCE_PREDICTION_API_KEY="your_key"
#   export BINANCE_PREDICTION_SECRET_KEY="your_secret"
PREDICTION_API_KEY = os.environ.get("BINANCE_PREDICTION_API_KEY", "")
PREDICTION_SECRET_KEY = os.environ.get("BINANCE_PREDICTION_SECRET_KEY", "")

# Prediction Markets API base URL (may differ from spot/futures)
PREDICTION_BASE = "https://api.binance.com"  # Adjust when official endpoint is known

# ---- Simple cache (2-minute TTL for market data) ----
_cache: Dict = {}
_CACHE_TTL = 120


def is_configured() -> bool:
    """Check if Prediction Market API credentials are available."""
    return bool(PREDICTION_API_KEY and PREDICTION_SECRET_KEY)


def _cached(key: str, fetcher, ttl: int = _CACHE_TTL):
    now = time.time()
    entry = _cache.get(key)
    if entry and now - entry["ts"] < ttl:
        return entry["data"]
    data = fetcher()
    if data is not None:
        _cache[key] = {"ts": now, "data": data}
    return data


def _fetch_json(url: str, timeout: int = 10, headers: dict = None) -> dict:
    """HTTP GET with optional auth headers."""
    hdrs = {"User-Agent": "Mozilla/5.0"}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ═══════════════════════════════════════════════════════════════════
# Market Data Endpoints
# ═══════════════════════════════════════════════════════════════════

def fetch_prediction_markets(category: str = "crypto",
                             status: str = "active",
                             limit: int = 20) -> Optional[List[Dict]]:
    """Fetch active prediction markets related to crypto/BTC.

    Returns list of markets with:
      - market_id, title, description
      - current prices (implied probabilities)
      - volume, liquidity
      - expiration time
    """
    if not is_configured():
        logger.debug("Prediction Market API not configured, skipping")
        return None

    def _get():
        url = (
            f"{PREDICTION_BASE}/sapi/v1/prediction/markets"
            f"?category={category}&status={status}&limit={limit}"
        )
        # Binance authenticated endpoints use X-MBX-APIKEY header
        data = _fetch_json(url, headers={"X-MBX-APIKEY": PREDICTION_API_KEY})
        return data if isinstance(data, list) else data.get("markets", [])

    return _cached("markets", _get)


def fetch_market_detail(market_id: str) -> Optional[Dict]:
    """Fetch details for a specific prediction market."""
    if not is_configured():
        return None

    def _get():
        url = f"{PREDICTION_BASE}/sapi/v1/prediction/market/detail?marketId={market_id}"
        return _fetch_json(url, headers={"X-MBX-APIKEY": PREDICTION_API_KEY})

    return _cached(f"market_{market_id}", _get)


def fetch_market_prices(market_id: str) -> Optional[List[Dict]]:
    """Fetch current prices (implied probabilities) for outcomes in a market."""
    if not is_configured():
        return None

    def _get():
        url = f"{PREDICTION_BASE}/sapi/v1/prediction/market/prices?marketId={market_id}"
        return _fetch_json(url, headers={"X-MBX-APIKEY": PREDICTION_API_KEY})

    return _cached(f"prices_{market_id}", _get, ttl=60)


# ═══════════════════════════════════════════════════════════════════
# Analysis & Integration
# ═══════════════════════════════════════════════════════════════════

def analyze_btc_prediction_markets() -> Dict:
    """Analyze BTC-related prediction markets for sentiment signals.

    Returns:
        dict with 'bullish_probability', 'bearish_probability',
        'consensus_direction', 'details', 'available'
    """
    if not is_configured():
        return {
            "available": False,
            "note": "币安预测市场API未配置（需要API Key + 预测账户）",
            "bullish_probability": 0.5,
            "bearish_probability": 0.5,
            "consensus_direction": "neutral",
            "details": [],
        }

    try:
        markets = fetch_prediction_markets(category="crypto", limit=10)
        if not markets:
            return {
                "available": False,
                "note": "未找到BTC相关预测市场",
                "bullish_probability": 0.5,
                "bearish_probability": 0.5,
                "consensus_direction": "neutral",
                "details": [],
            }

        # Find BTC-specific markets
        btc_markets = []
        for m in markets:
            title = m.get("title", "").lower()
            if "btc" in title or "bitcoin" in title:
                btc_markets.append(m)

        if not btc_markets:
            return {
                "available": True,
                "note": "无BTC专项预测市场，仅有通用加密市场",
                "bullish_probability": 0.5,
                "bearish_probability": 0.5,
                "consensus_direction": "neutral",
                "details": [],
            }

        # Aggregate implied probabilities across markets
        details = []
        bullish_sum = 0.0
        bearish_sum = 0.0
        count = 0

        for m in btc_markets[:5]:
            title = m.get("title", "Unknown")
            # Try to get price data
            prices = fetch_market_prices(m.get("marketId", ""))
            if prices:
                for outcome in prices:
                    label = outcome.get("label", "").lower()
                    prob = float(outcome.get("price", 0))  # price = probability in [0,1]
                    if any(w in label for w in ["up", "bull", "yes", "above", "higher"]):
                        bullish_sum += prob
                        count += 1
                    elif any(w in label for w in ["down", "bear", "no", "below", "lower"]):
                        bearish_sum += prob

                details.append(f"{title}: 有{len(prices)}个结果选项")
            else:
                details.append(f"{title}: (价格数据暂不可用)")

        if count > 0:
            avg_bullish = bullish_sum / max(count, 1)
            avg_bearish = bearish_sum / max(count, 1)
            total = avg_bullish + avg_bearish
            if total > 0:
                avg_bullish /= total
                avg_bearish /= total

            if avg_bullish > 0.6:
                direction = "bullish"
            elif avg_bearish > 0.6:
                direction = "bearish"
            else:
                direction = "neutral"

            return {
                "available": True,
                "note": f"基于{len(btc_markets)}个BTC预测市场",
                "bullish_probability": round(avg_bullish, 3),
                "bearish_probability": round(avg_bearish, 3),
                "consensus_direction": direction,
                "details": details,
            }

        return {
            "available": True,
            "note": "BTC预测市场数据有限",
            "bullish_probability": 0.5,
            "bearish_probability": 0.5,
            "consensus_direction": "neutral",
            "details": details,
        }

    except Exception as e:
        logger.warning(f"Prediction market analysis failed: {e}")
        return {
            "available": True,
            "note": f"获取失败: {e}",
            "bullish_probability": 0.5,
            "bearish_probability": 0.5,
            "consensus_direction": "neutral",
            "details": [],
        }


def get_prediction_market_signal() -> Dict:
    """Public interface: get prediction market sentiment signal.

    Integrates into the BTCAnalyzer scoring pipeline.
    Returns a signal dict compatible with the analyzer's sentiment module.
    """
    analysis = analyze_btc_prediction_markets()

    if not analysis["available"] or analysis["consensus_direction"] == "neutral":
        return {
            "indicator": "预测市场",
            "value": "N/A",
            "score": 0,
            "note": analysis.get("note", "未配置或数据不可用"),
            "raw": analysis,
        }

    direction = analysis["consensus_direction"]
    bull_prob = analysis["bullish_probability"]
    bear_prob = analysis["bearish_probability"]

    if direction == "bullish":
        score = 1
        note = f"预测市场偏多 (看涨概率{bull_prob:.0%})"
    elif direction == "bearish":
        score = -1
        note = f"预测市场偏空 (看跌概率{bear_prob:.0%})"
    else:
        score = 0
        note = f"预测市场中性 ({bull_prob:.0%}/{bear_prob:.0%})"

    return {
        "indicator": "预测市场",
        "value": f"看涨{bull_prob:.0%}/看跌{bear_prob:.0%}",
        "score": score,
        "note": note,
        "raw": analysis,
    }
