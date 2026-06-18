"""
高速数据层 — REST 轮询 Binance API，延迟 <200ms

Railway US 服务器到 Binance API 延迟约 30-50ms，
REST 轮询 200ms 可达到与 WebSocket 相当的实时性，且更稳定。
"""
import json
import logging
import threading
import time
from collections import deque
from typing import Dict

import data as d
from indicators import find_support_resistance

logger = logging.getLogger(__name__)

MAX_KLINES = 120
POLL_INTERVAL = 0.2  # 200ms polling = 5 updates/second

_lock = threading.RLock()
_state = {
    "klines_5m": deque(maxlen=MAX_KLINES),
    "klines_15m": deque(maxlen=MAX_KLINES),
    "ticker": {},
    "supports": [],
    "resistances": [],
    "last_update_ts": 0.0,
    "update_count": 0,
    "error_count": 0,
    "error_msg": "",
}
_running = True
_thread = None
_data_ready = threading.Event()


def _poll_once():
    """Single REST poll cycle — fetch klines + ticker."""
    global _state
    now = time.time()

    # Fetch klines (parallel would be better, but sequential keeps it simple)
    for tf, key in [("5m", "klines_5m"), ("15m", "klines_15m")]:
        try:
            raw = d.fetch_klines("BTCUSDT", tf, 60)
            klines = d.parse_klines_to_dicts(raw)
            if klines:
                with _lock:
                    _state[key] = deque(klines[-MAX_KLINES:], maxlen=MAX_KLINES)
        except Exception as e:
            with _lock:
                _state["error_count"] += 1
                _state["error_msg"] = str(e)[:100]

    # Fetch ticker
    try:
        tk = d.fetch_ticker("BTCUSDT")
        if tk and tk.get("lastPrice"):
            with _lock:
                _state["ticker"] = {
                    "lastPrice": str(tk["lastPrice"]),
                    "priceChangePercent": str(tk.get("priceChangePercent", "0")),
                    "highPrice": str(tk["highPrice"]),
                    "lowPrice": str(tk["lowPrice"]),
                    "volume": str(tk["volume"]),
                }
                _state["last_update_ts"] = now
                _state["update_count"] += 1
    except Exception as e:
        with _lock:
            _state["error_count"] += 1
            _state["error_msg"] = str(e)[:100]

    # Refresh S/R every 30 poll cycles (~6s)
    if _state["update_count"] % 30 == 0:
        try:
            raw = d.fetch_klines("BTCUSDT", "1h", 60)
            k1h = d.parse_klines_to_dicts(raw)
            sr = find_support_resistance(k1h, 50) if k1h else {}
            with _lock:
                _state["supports"] = sr.get("support", [])
                _state["resistances"] = sr.get("resistance", [])
        except Exception:
            pass

    _data_ready.set()


def _poll_loop():
    """Background thread: poll Binance REST at 200ms intervals."""
    global _state, _running
    logger.info("High-speed REST polling started (200ms interval)")

    while _running:
        try:
            _poll_once()
        except Exception as e:
            logger.error(f"Poll cycle error: {e}")
        time.sleep(POLL_INTERVAL)


def start():
    """Initialize data layer and start polling thread."""
    global _thread, _running
    _running = True

    # Initial data load (block until we have data)
    logger.info("Initial data load...")
    for attempt in range(10):
        try:
            _poll_once()
            with _lock:
                if _state["ticker"] and _state["klines_5m"]:
                    logger.info(f"Initial data loaded: price={_state['ticker'].get('lastPrice')}, "
                                f"5m candles={len(_state['klines_5m'])}, "
                                f"15m candles={len(_state['klines_15m'])}")
                    break
        except Exception as e:
            logger.warning(f"Initial load attempt {attempt+1}: {e}")
        time.sleep(0.5)
    else:
        logger.error("Failed to load initial data after 10 attempts")

    # Start background polling
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="data-poller")
    _thread.start()


def stop():
    global _running
    _running = False


def get_snapshot() -> dict:
    """Thread-safe snapshot of current data.

    Returns dict with: klines_5m, klines_15m, ticker, supports, resistances,
    age_ms, update_count, error_count, error_msg
    """
    with _lock:
        k5 = list(_state["klines_5m"])
        k15 = list(_state["klines_15m"])
        ticker = dict(_state["ticker"])
        age = (time.time() - _state["last_update_ts"]) * 1000 if _state["last_update_ts"] else 999999
        supports = list(_state["supports"])
        resistances = list(_state["resistances"])
        count = _state["update_count"]
        errs = _state["error_count"]
        err_msg = _state["error_msg"]

    for k in k5:
        k["_tf"] = "5m"
    for k in k15:
        k["_tf"] = "15m"

    return {
        "klines_5m": k5,
        "klines_15m": k15,
        "ticker": ticker,
        "supports": supports,
        "resistances": resistances,
        "age_ms": round(age, 1),
        "update_count": count,
        "error_count": errs,
        "error_msg": err_msg,
        "has_data": bool(ticker and k5),
    }
