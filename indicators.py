"""
?????? - ????????
EMA / MACD / RSI / ATR / K??? / ?????
"""
import logging
from typing import List, Dict, Optional, Tuple

from config import RSI_OVERSOLD, RSI_OVERBOUGHT, VOLUME_SURGE_RATIO, EMA_4H, EMA_1H

logger = logging.getLogger(__name__)


def ema(values: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    avg = sum(values[:period]) / period
    result[period - 1] = avg
    for i in range(period, len(values)):
        avg = (values[i] - avg) * k + avg
        result[i] = avg
    return result


def ema_value(values: List[float], period: int) -> Optional[float]:
    result = ema(values, period)
    return result[-1] if result else None


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return result
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    result[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss) if avg_loss != 0 else 100.0)
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 999
        result[i] = 100.0 - (100.0 / (1.0 + rs))
    return result


def rsi_current(closes: List[float], period: int = 14) -> Optional[float]:
    vals = rsi(closes, period)
    return vals[-1] if vals else None


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    n = len(closes)
    macd_line: List[Optional[float]] = [None] * n
    signal_line: List[Optional[float]] = [None] * n
    histogram: List[Optional[float]] = [None] * n
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) >= signal:
        ema_sig = ema(valid_macd, signal)
        sig_idx = 0
        for i in range(n):
            if macd_line[i] is not None:
                signal_line[i] = ema_sig[sig_idx]
                sig_idx += 1
    for i in range(n):
        if macd_line[i] is not None and signal_line[i] is not None:
            histogram[i] = macd_line[i] - signal_line[i]
    return macd_line, signal_line, histogram


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[Optional[float]]:
    n = len(highs)
    tr: List[float] = []
    result: List[Optional[float]] = [None] * n
    for i in range(n):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr.append(max(hl, hc, lc))
    if len(tr) < period:
        return result
    avg = sum(tr[:period]) / period
    result[period - 1] = avg
    for i in range(period, len(tr)):
        avg = (avg * (period - 1) + tr[i]) / period
        result[i] = avg
    return result


def atr_current(highs, lows, closes, period=14) -> Optional[float]:
    vals = atr(highs, lows, closes, period)
    return vals[-1] if vals else None


def detect_candlestick_pattern(kline: Dict) -> Optional[str]:
    o, h, l, c = kline["open"], kline["high"], kline["low"], kline["close"]
    body = abs(c - o)
    upper = h - max(c, o)
    lower = min(c, o) - l
    if body == 0 or (h - l) == 0:
        return None
    if lower >= 2 * body and upper <= body * 0.3:
        return "hammer" if c >= o else "inverted_hammer"
    if upper >= 2 * body and lower <= body * 0.3:
        return "shooting_star" if c <= o else "inverted_hammer"
    return None


def detect_engulfing(prev: Dict, curr: Dict) -> Optional[str]:
    p_o, p_c = prev["open"], prev["close"]
    c_o, c_c = curr["open"], curr["close"]
    p_body = abs(p_c - p_o)
    c_body = abs(c_c - c_o)
    if p_body == 0 or c_body == 0:
        return None
    if p_c < p_o and c_c > c_o and c_o <= p_c and c_c >= p_o:
        return "engulfing_bull"
    if p_c > p_o and c_c < c_o and c_o >= p_c and c_c <= p_o:
        return "engulfing_bear"
    return None


def analyze_volume(klines: List[Dict]) -> Dict:
    if len(klines) < 25:
        return {"surge": False, "ratio": 1.0, "avg_volume": 0, "recent_volume": 0}
    recent = [k["volume"] for k in klines[-5:]]
    baseline = [k["volume"] for k in klines[-25:-5]]
    avg_baseline = sum(baseline) / len(baseline) if baseline else 1
    avg_recent = sum(recent) / len(recent) if recent else 0
    ratio = avg_recent / avg_baseline if avg_baseline > 0 else 1
    return {"surge": ratio >= VOLUME_SURGE_RATIO, "ratio": round(ratio, 2),
            "avg_volume": round(avg_baseline, 2), "recent_volume": round(avg_recent, 2)}


def determine_trend(klines: List[Dict], ema_periods: List[int]) -> Dict:
    closes = [k["close"] for k in klines]
    if len(closes) < max(ema_periods):
        return {"trend": "sideways", "description": "\u6570\u636e\u4e0d\u8db3"}
    ema_vals = {}
    for p in ema_periods:
        v = ema_value(closes, p)
        if v is not None:
            ema_vals[p] = v
    if len(ema_vals) < 3:
        return {"trend": "sideways", "description": "EMA\u6570\u636e\u4e0d\u8db3"}
    sorted_periods = sorted(ema_vals.keys())
    sorted_vals = [ema_vals[p] for p in sorted_periods]
    if all(sorted_vals[i] > sorted_vals[i + 1] for i in range(len(sorted_vals) - 1)):
        return {"trend": "bullish", "description": f"EMA\u591a\u5934\u6392\u5217: {', '.join(f'{p}={v:.0f}' for p, v in zip(sorted_periods, sorted_vals))}"}
    if all(sorted_vals[i] < sorted_vals[i + 1] for i in range(len(sorted_vals) - 1)):
        return {"trend": "bearish", "description": f"EMA\u7a7a\u5934\u6392\u5217: {', '.join(f'{p}={v:.0f}' for p, v in zip(sorted_periods, sorted_vals))}"}
    return {"trend": "sideways", "description": f"EMA\u7f20\u7ed5/\u4ea4\u53c9: {', '.join(f'{p}={v:.0f}' for p, v in zip(sorted_periods, sorted_vals))}"}


def find_support_resistance(klines: List[Dict], lookback: int = 50) -> Dict:
    if len(klines) < lookback:
        lookback = len(klines)
    data = klines[-lookback:]
    highs = [k["high"] for k in data]
    lows = [k["low"] for k in data]
    closes = [k["close"] for k in data]
    current = closes[-1]
    resistance_levels = []
    support_levels = []
    for i in range(2, len(data) - 2):
        if highs[i] > highs[i-1] > highs[i-2] and highs[i] > highs[i+1] > highs[i+2]:
            resistance_levels.append(highs[i])
        if lows[i] < lows[i-1] < lows[i-2] and lows[i] < lows[i+1] < lows[i+2]:
            support_levels.append(lows[i])
    def cluster(levels, threshold=0.002):
        if not levels:
            return []
        sorted_l = sorted(levels)
        clusters = [[sorted_l[0]]]
        for v in sorted_l[1:]:
            if abs(v - clusters[-1][0]) / clusters[-1][0] < threshold:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [(round(sum(c)/len(c), 1), len(c)) for c in clusters]
    resistances = cluster(resistance_levels)
    supports = cluster(support_levels)
    resistances = [(p, w) for p, w in resistances if abs(p - current) / current < 0.05]
    supports = [(p, w) for p, w in supports if abs(p - current) / current < 0.05]
    resistances = sorted(resistances, key=lambda x: x[0])[-3:] if resistances else []
    supports = sorted(supports, key=lambda x: x[0])[:3] if supports else []
    if not supports:
        supports = [(round(min(lows[-20:]), 1), 1)]
    if not resistances:
        resistances = [(round(max(highs[-20:]), 1), 1)]
    return {"support": [p for p, _ in supports], "resistance": [p for p, _ in resistances], "current_price": current}
