"""
Binance WebSocket 实时数据推送模块

使用 Binance WebSocket combined stream 获取实时 K 线和 Ticker，
延迟 < 200ms，替代 REST API 轮询。

Streams used:
  - btcusdt@kline_5m  → 5分钟K线实时更新
  - btcusdt@kline_15m → 15分钟K线实时更新
  - btcusdt@ticker    → 24hr Ticker实时更新
"""
import json
import logging
import threading
import time
from collections import deque
from typing import Dict, List, Optional

import data as d
from indicators import find_support_resistance

logger = logging.getLogger(__name__)

# ---- Configuration ----
WS_URL = "wss://stream.binance.com:9443/stream?streams=btcusdt@kline_5m/btcusdt@kline_15m/btcusdt@ticker"
MAX_KLINES = 120  # Keep up to 120 candles per timeframe (enough for all indicators)
REST_RETRY_INTERVAL = 30  # Seconds between REST fallback attempts if WS disconnected

# ---- Global state (thread-safe via lock) ----
_lock = threading.RLock()
_state = {
    "klines_5m": deque(maxlen=MAX_KLINES),   # list of kline dicts
    "klines_15m": deque(maxlen=MAX_KLINES),
    "ticker": {},                              # latest ticker dict
    "last_ws_msg_ts": 0.0,                    # timestamp of last WS message
    "ws_connected": False,
    "klines_1h": [],                           # 1H klines (updated via REST periodically)
    "supports": [],
    "resistances": [],
    "last_1h_update": 0.0,
}
_initialized = threading.Event()
_ws_thread = None


def _parse_kline(kline_data: dict) -> dict:
    """Convert Binance WS kline to our standard dict format."""
    k = kline_data["k"]
    return {
        "time": k["t"],
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "is_final": k["x"],  # True if candle is closed
    }


def _parse_ticker(ticker_data: dict) -> dict:
    """Extract relevant fields from WS ticker."""
    return {
        "lastPrice": ticker_data["c"],
        "priceChangePercent": ticker_data["P"],
        "highPrice": ticker_data["h"],
        "lowPrice": ticker_data["l"],
        "volume": ticker_data["v"],
    }


def _update_1h_klines():
    """Periodically refresh 1H klines for support/resistance."""
    global _state
    now = time.time()
    if now - _state["last_1h_update"] < 60:
        return  # Only refresh every 60s (1H candles don't change fast)

    try:
        raw = d.fetch_klines("BTCUSDT", "1h", 60)
        k1h = d.parse_klines_to_dicts(raw)
        sr = find_support_resistance(k1h, 50) if k1h else {}
        with _lock:
            _state["klines_1h"] = k1h
            _state["supports"] = sr.get("support", [])
            _state["resistances"] = sr.get("resistance", [])
            _state["last_1h_update"] = now
        logger.debug("1H klines updated, S/R refreshed")
    except Exception as e:
        logger.warning(f"1H kline update failed: {e}")


def _ws_connect():
    """Connect to Binance WebSocket and process messages in a loop."""
    global _state

    # Import websocket-client (lazy import so module can load without it installed)
    try:
        from websocket import create_connection, WebSocketException
    except ImportError:
        logger.error("websocket-client not installed. Run: pip install websocket-client")
        return

    logger.info(f"Connecting to Binance WebSocket...")
    ws = None

    while True:
        try:
            ws = create_connection(
                WS_URL,
                timeout=10,
                sockopt=((6, 1, 1),),  # TCP_NODELAY for lower latency
            )
            with _lock:
                _state["ws_connected"] = True
            logger.info("Binance WebSocket connected ✓")

            while True:
                raw = ws.recv()
                now = time.time()
                msg = json.loads(raw)

                with _lock:
                    _state["last_ws_msg_ts"] = now

                stream = msg.get("stream", "")
                data = msg.get("data", {})

                if "@kline" in stream:
                    kline = _parse_kline(data)
                    tf = "5m" if "kline_5m" in stream else "15m"
                    key = f"klines_{tf}"

                    with _lock:
                        klines = _state[key]
                        # Update existing candle or append new one
                        if klines and klines[-1]["time"] == kline["time"]:
                            klines[-1] = kline
                        else:
                            klines.append(kline)
                            # Trigger 1H refresh when candle closes
                            if kline.get("is_final") and tf == "15m":
                                threading.Thread(target=_update_1h_klines, daemon=True).start()

                elif "@ticker" in stream:
                    with _lock:
                        _state["ticker"] = _parse_ticker(data)

        except Exception as e:
            logger.warning(f"WebSocket disconnected: {e}. Reconnecting in 2s...")
            with _lock:
                _state["ws_connected"] = False
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            time.sleep(2)
            # Fallback: use REST to refresh data while disconnected
            try:
                _rest_refresh_all()
            except Exception:
                pass
            wait = min(REST_RETRY_INTERVAL, 5)
            time.sleep(wait)


def _rest_refresh_all():
    """Fallback: refresh all data via REST when WS is down."""
    global _state
    logger.info("REST data refresh...")

    for tf, key in [("5m", "klines_5m"), ("15m", "klines_15m")]:
        try:
            raw = d.fetch_klines("BTCUSDT", tf, 60)
            klines = d.parse_klines_to_dicts(raw)
            if klines:
                with _lock:
                    _state[key] = deque(klines[-MAX_KLINES:], maxlen=MAX_KLINES)
                    _state["last_ws_msg_ts"] = time.time()
                logger.info(f"REST: {tf} klines loaded ({len(klines)} candles)")
        except Exception as e:
            logger.error(f"REST klines {tf} failed: {e}")

    try:
        tk = d.fetch_ticker("BTCUSDT")
        if tk and tk.get("lastPrice"):
            with _lock:
                _state["ticker"] = {
                    "lastPrice": str(tk.get("lastPrice", "0")),
                    "priceChangePercent": str(tk.get("priceChangePercent", "0")),
                    "highPrice": str(tk.get("highPrice", "0")),
                    "lowPrice": str(tk.get("lowPrice", "0")),
                    "volume": str(tk.get("volume", "0")),
                }
            logger.info(f"REST: ticker loaded, price={tk.get('lastPrice')}")
    except Exception as e:
        logger.error(f"REST ticker failed: {e}")


def start():
    """Initialize data and start WebSocket thread.

    Call once at app startup. Blocks until initial data is loaded.
    """
    global _ws_thread

    # Initial data load via REST (fast bootstrap)
    logger.info("Bootstrapping initial data via REST...")
    _rest_refresh_all()
    _update_1h_klines()

    # Start WebSocket in background thread
    _ws_thread = threading.Thread(target=_ws_connect, daemon=True, name="binance-ws")
    _ws_thread.start()

    # Wait for first WS message (or timeout after 5s)
    wait_start = time.time()
    while time.time() - wait_start < 5:
        with _lock:
            if _state["last_ws_msg_ts"] > 0:
                break
        time.sleep(0.1)

    if _state["ws_connected"]:
        logger.info("Live feed ready — WebSocket streaming active")
    else:
        logger.warning("Live feed ready — using REST fallback")
    _initialized.set()


def get_snapshot() -> dict:
    """Thread-safe snapshot of the current live data.

    Returns dict with keys: klines_5m, klines_15m, ticker,
    supports, resistances, ws_age_ms
    """
    with _lock:
        k5 = list(_state["klines_5m"])
        k15 = list(_state["klines_15m"])
        ticker = dict(_state["ticker"])
        ws_age = (time.time() - _state["last_ws_msg_ts"]) * 1000 if _state["last_ws_msg_ts"] else 999999
        supports = list(_state["supports"])
        resistances = list(_state["resistances"])
        connected = _state["ws_connected"]

    # Add iframe tags for candle progress
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
        "ws_age_ms": round(ws_age, 1),
        "ws_connected": connected,
    }
