"""
链上数据模块 - 接入 Blockchain.com / Whale Alert / CoinGecko API
"""
import json
import logging
import time
from typing import Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from config import (
    WHALE_TRANSFER_THRESHOLD,
    EXCHANGE_NETFLOW_WARN,
)

logger = logging.getLogger(__name__)

# ---- API base URLs ----
BLOCKCHAIN_CHARTS = "https://api.blockchain.info/charts"
WHALE_ALERT_API = "https://api.whale-alert.io/v1"
COINGECKO_API = "https://api.coingecko.com/api/v3"

# ---- Optional API key for Whale Alert (free tier: 10 calls/min) ----
WHALE_ALERT_KEY = ""  # Set your key in config or env

# ---- Simple in-memory cache (5-minute TTL) ----
_cache: Dict = {}
_CACHE_TTL = 300


def _cached(key: str, fetcher, ttl: int = _CACHE_TTL):
    now = time.time()
    entry = _cache.get(key)
    if entry and now - entry["ts"] < ttl:
        return entry["data"]
    try:
        data = fetcher()
        _cache[key] = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.warning(f"On-chain fetch failed [{key}]: {e}")
        # Return stale cache if available
        if entry:
            return entry["data"]
        return None


def _fetch_json(url: str, timeout: int = 10) -> dict:
    """Minimal HTTP GET → JSON helper."""
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ═══════════════════════════════════════════════════════════════════
# Blockchain.com Charts API (free, no key required)
# ═══════════════════════════════════════════════════════════════════

def fetch_hashrate(timespan: str = "7d") -> Optional[float]:
    """Estimated hash rate (TH/s)."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/hash-rate?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return float(values[-1]["y"]) if values else None
    return _cached("hashrate", _get)


def fetch_difficulty(timespan: str = "1d") -> Optional[float]:
    """Mining difficulty."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/difficulty?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return float(values[-1]["y"]) if values else None
    return _cached("difficulty", _get)


def fetch_active_addresses(timespan: str = "7d") -> Optional[int]:
    """Number of unique addresses used per day."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/n-unique-addresses?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return int(values[-1]["y"]) if values else None
    return _cached("active_addresses", _get)


def fetch_transaction_count(timespan: str = "7d") -> Optional[int]:
    """Total confirmed transactions per day."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/n-transactions?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return int(values[-1]["y"]) if values else None
    return _cached("tx_count", _get)


def fetch_avg_fee(timespan: str = "7d") -> Optional[float]:
    """Average transaction fee in USD."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/transaction-fees-usd?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return float(values[-1]["y"]) if values else None
    return _cached("avg_fee", _get)


def fetch_market_price(timespan: str = "7d") -> Optional[float]:
    """BTC market price from Blockchain.com (cross-reference)."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/market-price?timespan={timespan}&format=json"
        )
        values = data.get("values", [])
        return float(values[-1]["y"]) if values else None
    return _cached("market_price_bc", _get)


def fetch_mempool_size() -> Optional[int]:
    """Current mempool transaction count."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/mempool-count?timespan=1d&format=json"
        )
        values = data.get("values", [])
        return int(values[-1]["y"]) if values else None
    return _cached("mempool", _get, ttl=60)


def fetch_mempool_growth() -> Optional[float]:
    """Mempool size change ratio (last vs average)."""
    def _get():
        data = _fetch_json(
            f"{BLOCKCHAIN_CHARTS}/mempool-count?timespan=1d&format=json"
        )
        values = data.get("values", [])
        if len(values) < 6:
            return None
        recent = sum(v["y"] for v in values[-3:]) / 3
        earlier = sum(v["y"] for v in values[-6:-3]) / 3
        return recent / earlier if earlier > 0 else 1.0
    return _cached("mempool_growth", _get, ttl=60)


# ═══════════════════════════════════════════════════════════════════
# Whale Alert API (free tier)
# ═══════════════════════════════════════════════════════════════════

def fetch_whale_transactions(min_value: int = None) -> Optional[list]:
    """Fetch recent large BTC transactions (>= threshold in USD)."""
    if min_value is None:
        min_value = WHALE_TRANSFER_THRESHOLD * 1000000  # config is in BTC, API uses USD

    def _get():
        url = (
            f"{WHALE_ALERT_API}/transactions"
            f"?api_key={WHALE_ALERT_KEY}"
            f"&min_value={min_value}"
            f"&start=0&limit=20&currency=btc"
        )
        data = _fetch_json(url)
        return data.get("transactions", [])
    return _cached("whale_tx", _get, ttl=120)


def check_whale_activity() -> Dict:
    """Analyze recent whale activity for trading signals."""
    txs = fetch_whale_transactions()

    if txs is None:
        return {
            "active": False,
            "tx_count": 0,
            "total_value_btc": 0,
            "exchange_related": 0,
            "signal": "数据不可用（需要Whale Alert API key）",
            "details": [],
        }

    exchange_keywords = ["binance", "coinbase", "kraken", "okx", "bybit",
                         "kucoin", "bitfinex", "huobi", "gate.io", "crypto.com"]

    total_btc = 0
    exchange_count = 0
    details = []

    for tx in txs[:10]:
        amount_btc = float(tx.get("amount", 0))
        total_btc += amount_btc
        from_owner = (tx.get("from", {}) or {}).get("owner", "").lower()
        to_owner = (tx.get("to", {}) or {}).get("owner", "").lower()
        is_exchange = any(kw in from_owner or kw in to_owner for kw in exchange_keywords)
        if is_exchange:
            exchange_count += 1
            direction = "转入交易所" if any(kw in to_owner for kw in exchange_keywords) else "转出交易所"
            details.append(
                f"{direction}: {amount_btc:.0f} BTC ({from_owner or 'unknown'} → {to_owner or 'unknown'})"
            )

    # Signal interpretation
    if total_btc > 50000 and exchange_count > 3:
        signal = "⚠️ 巨鲸活跃+交易所异动，注意剧烈波动"
    elif total_btc > 10000:
        signal = "巨鲸活跃度较高"
    elif exchange_count > 2:
        signal = "交易所大额转账增多"
    else:
        signal = "巨鲸活动正常"

    return {
        "active": total_btc > 5000,
        "tx_count": len(txs),
        "total_value_btc": round(total_btc, 1),
        "exchange_related": exchange_count,
        "signal": signal,
        "details": details,
    }


# ═══════════════════════════════════════════════════════════════════
# CoinGecko API (free, no key for basic tier)
# ═══════════════════════════════════════════════════════════════════

def fetch_coingecko_btc_data() -> Optional[Dict]:
    """Fetch BTC market overview from CoinGecko."""
    def _get():
        url = (
            f"{COINGECKO_API}/coins/bitcoin"
            "?localization=false&tickers=false&community_data=false"
            "&developer_data=false&sparkline=false"
        )
        return _fetch_json(url)
    return _cached("coingecko_btc", _get, ttl=180)


def get_market_cap_dominance() -> Optional[Dict]:
    """Get BTC market cap and dominance."""
    data = fetch_coingecko_btc_data()
    if not data or "market_data" not in data:
        return None
    md = data["market_data"]
    return {
        "market_cap": md.get("market_cap", {}).get("usd"),
        "market_cap_rank": md.get("market_cap_rank"),
        "total_volume": md.get("total_volume", {}).get("usd"),
        "price_change_24h_pct": md.get("price_change_percentage_24h"),
        "price_change_7d_pct": md.get("price_change_percentage_7d"),
        "market_cap_change_24h_pct": md.get("market_cap_change_percentage_24h"),
    }


# ═══════════════════════════════════════════════════════════════════
# Composite on-chain check (replaces stubs in sentiment.py)
# ═══════════════════════════════════════════════════════════════════

def comprehensive_onchain_check() -> Dict:
    """Run all available on-chain checks and return a unified summary.

    This replaces the stub in sentiment.py's check_onchain().
    """
    details = []
    score = 0      # positive = bullish, negative = bearish
    status = "正常 ✅"

    # 1. Hash rate trend
    hr = fetch_hashrate()
    if hr is not None:
        details.append(f"算力: {hr/1e6:.1f} EH/s")
        # Declining hash rate can be bearish (miners capitulating)
        # Stable/rising = bullish
    else:
        details.append("算力: 数据不可用")

    # 2. Active addresses
    aa = fetch_active_addresses()
    if aa is not None:
        details.append(f"活跃地址: {aa:,}")
        if aa < 500000:
            score -= 1
            details[-1] += " (偏低 ⚠️)"
    else:
        details.append("活跃地址: 数据不可用")

    # 3. Transaction count
    tc = fetch_transaction_count()
    if tc is not None:
        details.append(f"日交易数: {tc:,}")
    else:
        details.append("日交易数: 数据不可用")

    # 4. Mempool pressure
    mg = fetch_mempool_growth()
    if mg is not None:
        if mg > 1.5:
            details.append(f"内存池增长: {mg:.1f}x (拥堵 ↑)")
            score += 0.5  # High demand = potential bullish
        elif mg < 0.5:
            details.append(f"内存池缩减: {mg:.1f}x (低活跃度)")
            score -= 0.5
        else:
            details.append(f"内存池: 正常 ({mg:.1f}x)")

    # 5. Whale activity
    whale = check_whale_activity()
    if whale["active"]:
        details.append(f"巨鲸: {whale['tx_count']}笔大额, {whale['total_value_btc']:.0f} BTC")
        if whale["exchange_related"] > 2:
            details.append(whale["signal"])
    for d in whale.get("details", [])[:3]:
        details.append(f"  {d}")

    # 6. Market cap / dominance (CoinGecko)
    mcd = get_market_cap_dominance()
    if mcd:
        details.append(f"市值: ${mcd.get('market_cap', 0)/1e9:.1f}B")
        details.append(f"24h涨跌: {mcd.get('price_change_24h_pct', 0):+.1f}%")

    # 7. Average transaction fee
    fee = fetch_avg_fee()
    if fee is not None:
        details.append(f"平均手续费: ${fee:.2f}")
        if fee > 20:
            details[-1] += " (网络拥堵) "

    # Determine overall status
    if score >= 2:
        status = "链上偏多 📗"
    elif score <= -2:
        status = "链上偏空 📕"
    elif score == 1:
        status = "链上略多"
    elif score == -1:
        status = "链上略空"

    return {
        "status": status,
        "score": score,
        "details": details,
        "position_factor": max(0.5, min(1.5, 1.0 + score * 0.05)),
    }
