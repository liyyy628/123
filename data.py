"""
????? - ? Binance REST API ??????
????? urllib???????????????
"""
import json
import time
import logging
from datetime import datetime
from urllib.request import Request, urlopen, build_opener, ProxyHandler
from urllib.error import URLError, HTTPError
from typing import Optional, Dict, List, Any

from config import BINANCE_BASE, BINANCE_FUTURES, COINGECKO_BASE

logger = logging.getLogger(__name__)


class APIError(Exception):
    pass


# -- Proxy config --
HTTP_PROXY = "http://127.0.0.1:7890"
USE_PROXY = False  # set True to enable

def _fetch_json(url: str, retries: int = 3, timeout: int = 10) -> Any:
    """http get -> json, with optional proxy"""
    opener = build_opener(ProxyHandler({"http": HTTP_PROXY, "https": HTTP_PROXY})) if USE_PROXY else build_opener()
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (HTTPError, URLError, OSError) as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 1.5
                logger.warning("[retry %d/%d] %s -> %s, %.1fs" % (attempt+1, retries, url, e, wait))
                time.sleep(wait)
            else:
                raise APIError("API fail: %s -> %s" % (url, e)) from e
def kline_time_to_dt(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000)


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 200) -> List[List]:
    url = f"{BINANCE_BASE}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    return _fetch_json(url)


def fetch_ticker(symbol: str = "BTCUSDT") -> Dict:
    url = f"{BINANCE_BASE}/api/v3/ticker/24hr?symbol={symbol}"
    return _fetch_json(url)


def fetch_current_price(symbol: str = "BTCUSDT") -> float:
    url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol={symbol}"
    data = _fetch_json(url)
    return float(data["price"])


def fetch_order_book(symbol: str = "BTCUSDT", limit: int = 20) -> Dict[str, List]:
    url = f"{BINANCE_BASE}/api/v3/depth?symbol={symbol}&limit={limit}"
    return _fetch_json(url)


def fetch_trades(symbol: str = "BTCUSDT", limit: int = 100) -> List[Dict]:
    url = f"{BINANCE_BASE}/api/v3/trades?symbol={symbol}&limit={limit}"
    return _fetch_json(url)


def fetch_funding_rate(symbol: str = "BTCUSDT", limit: int = 1) -> float:
    url = f"{BINANCE_FUTURES}/fapi/v1/fundingRate?symbol={symbol}&limit={limit}"
    data = _fetch_json(url)
    return float(data[0]["fundingRate"]) if data else 0.0


def fetch_open_interest(symbol: str = "BTCUSDT") -> float:
    url = f"{BINANCE_FUTURES}/fapi/v1/openInterest?symbol={symbol}"
    data = _fetch_json(url)
    return float(data["openInterest"])


def fetch_long_short_ratio(symbol: str = "BTCUSDT", period: str = "5m", limit: int = 1) -> Optional[float]:
    url = f"{BINANCE_FUTURES}/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={period}&limit={limit}"
    try:
        data = _fetch_json(url)
        return float(data[0]["longShortRatio"]) if data else None
    except APIError:
        logger.warning("\u65e0\u6cd5\u83b7\u53d6\u591a\u7a7a\u6bd4\uff0c\u8df3\u8fc7")
        return None


def fetch_fear_greed_index(limit: int = 1) -> Optional[int]:
    url = f"https://api.alternative.me/fng/?limit={limit}"
    try:
        data = _fetch_json(url)
        return int(data["data"][0]["value"])
    except (APIError, KeyError, IndexError, ValueError):
        logger.warning("\u65e0\u6cd5\u83b7\u53d6\u6050\u60e7\u8d2a\u5a2a\u6307\u6570\uff0c\u8df3\u8fc7")
        return None


def fetch_exchange_netflow(asset: str = "BTC") -> Optional[float]:
    try:
        url = f"{COINGECKO_BASE}/exchanges/binance/tickers?coin_ids=bitcoin"
        _fetch_json(url)
        logger.info("\u94fe\u4e0a\u6570\u636e\uff1aCoinGecko \u514d\u8d39 API \u4e0d\u63d0\u4f9b\u4ea4\u6613\u6240\u51c0\u6d41\u91cf\uff0c\u8fd4\u56de None")
        return None
    except APIError:
        return None


def fetch_multi_tf_klines(symbol: str = "BTCUSDT") -> Dict[str, List]:
    return {
        "4h": fetch_klines(symbol, "4h", 100),
        "1h": fetch_klines(symbol, "1h", 100),
        "15m": fetch_klines(symbol, "15m", 200),
    }


def parse_klines_to_dicts(raw: List[List]) -> List[Dict]:
    keys = ["time", "open", "high", "low", "close", "volume", "close_time",
            "quote_vol", "count", "taker_buy_vol", "taker_buy_quote", "ignore"]
    result = []
    for row in raw:
        d = dict(zip(keys, row))
        d["time"] = int(d["time"])
        for k in ["open", "high", "low", "close", "volume"]:
            d[k] = float(d[k])
        result.append(d)
    return result
