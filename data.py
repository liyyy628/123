"""
数据层 - 通过 Binance REST API 获取行情数据
基于 urllib 实现，零外部依赖
"""
import json
import logging
import os
import time
from datetime import datetime
from urllib.request import Request, urlopen, build_opener, ProxyHandler
from urllib.error import URLError, HTTPError
from typing import Optional, Dict, List, Any

from config import BINANCE_BASE, BINANCE_FUTURES, COINGECKO_BASE

logger = logging.getLogger(__name__)


class APIError(Exception):
    pass


# -- Proxy config --
# Set via environment: export BINANCE_PROXY=http://127.0.0.1:7890
# Or set USE_PROXY=True directly
HTTP_PROXY = os.environ.get("BINANCE_PROXY", "http://127.0.0.1:7890")
USE_PROXY = os.environ.get("BINANCE_USE_PROXY", "").lower() in ("1", "true", "yes")

# Binance API fallback domains
_BINANCE_DOMAINS = [
    BINANCE_BASE,
    "https://api.binance.us",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]


def _try_fetch_json(url: str, timeout: int = 10, opener=None) -> Any:
    """Single attempt HTTP GET with the given opener."""
    if opener is None:
        opener = build_opener()
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _fetch_json(url: str, retries: int = 3, timeout: int = 10) -> Any:
    """HTTP GET -> JSON, with optional proxy and domain fallback.

    Tries: 1) direct connection, 2) proxy connection, 3) alternate domains.
    """
    openers = []
    if USE_PROXY and HTTP_PROXY:
        openers.append(build_opener(ProxyHandler({"http": HTTP_PROXY, "https": HTTP_PROXY})))
    openers.append(build_opener())

    # Try alternate domains if the URL is from binance.com
    urls_to_try = [url]
    if "api.binance.com" in url:
        for domain in _BINANCE_DOMAINS:
            alt_url = url.replace("api.binance.com", domain.replace("https://", "").rstrip("/"))
            if alt_url not in urls_to_try:
                urls_to_try.append(alt_url)

    last_error = None
    for opener in openers:
        for try_url in urls_to_try:
            for attempt in range(retries):
                try:
                    return _try_fetch_json(try_url, timeout, opener)
                except (HTTPError, URLError, OSError) as e:
                    last_error = e
                    if isinstance(e, HTTPError) and e.code == 451:
                        # Geo-blocked, don't retry this URL
                        break
                    if attempt < retries - 1:
                        wait = (attempt + 1) * 1.5
                        logger.debug("[retry %d/%d] %s -> %s, %.1fs" % (attempt+1, retries, try_url, e, wait))
                        time.sleep(wait)

    raise APIError("API fail: all methods exhausted for %s, last error: %s" % (url, last_error))


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
        logger.warning("无法获取多空比，跳过")
        return None


def fetch_fear_greed_index(limit: int = 1) -> Optional[int]:
    url = "https://api.alternative.me/fng/?limit=%d" % limit
    try:
        data = _fetch_json(url)
        return int(data["data"][0]["value"])
    except (APIError, KeyError, IndexError, ValueError):
        logger.warning("无法获取恐惧贪婪指数，跳过")
        return None


def fetch_exchange_netflow(asset: str = "BTC") -> Optional[float]:
    """Exchange netflow (deprecated - use onchain.py instead)."""
    try:
        url = f"{COINGECKO_BASE}/exchanges/binance/tickers?coin_ids=bitcoin"
        _fetch_json(url)
        logger.info("链上数据：CoinGecko 免费 API 不提供交易所净流量，返回 None")
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
    """Convert raw Binance kline list to list of dicts."""
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
