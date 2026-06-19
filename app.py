# -*- coding: utf-8 -*-
"""BTC 5M/15M Direction Predictor

Synchronous data fetch on demand + 200ms cache.
No background threads. No WebSocket complexity.
Every API request gets fresh Binance data guaranteed.
"""
import json
import threading
import time
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, Response

import data as d
from indicators import ema, rsi_current, find_support_resistance

app = Flask(__name__)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ---- 200ms cache ----
_cache = None
_cache_ts = 0.0
_cache_lock = threading.Lock()
CACHE_MS = 0.2  # 200ms


def fetch_all():
    """Fetch all data from Binance synchronously. Returns raw data dict."""
    result = {"klines_5m": [], "klines_15m": [], "klines_1h": [],
              "ticker": {}, "supports": [], "resistances": [], "error": None}

    # Klines
    for tf, key in [("5m", "klines_5m"), ("15m", "klines_15m"), ("1h", "klines_1h")]:
        try:
            raw = d.fetch_klines("BTCUSDT", tf, 60)
            parsed = d.parse_klines_to_dicts(raw)
            if parsed:
                for k in parsed:
                    k["_tf"] = tf
                result[key] = parsed
        except Exception as e:
            if not result["error"]:
                result["error"] = f"Klines {tf}: {e}"

    # Ticker
    try:
        tk = d.fetch_ticker("BTCUSDT")
        if tk:
            result["ticker"] = {
                "lastPrice": str(tk.get("lastPrice", "0")),
                "priceChangePercent": str(tk.get("priceChangePercent", "0")),
                "highPrice": str(tk.get("highPrice", "0")),
                "lowPrice": str(tk.get("lowPrice", "0")),
                "volume": str(tk.get("volume", "0")),
            }
    except Exception as e:
        result["ticker"] = {"lastPrice": "0", "priceChangePercent": "0",
                            "highPrice": "0", "lowPrice": "0", "volume": "0"}
        if not result["error"]:
            result["error"] = f"Ticker: {e}"

    # Support/Resistance from 1H
    if result["klines_1h"] and len(result["klines_1h"]) >= 20:
        try:
            sr = find_support_resistance(result["klines_1h"], 50)
            result["supports"] = sr.get("support", [])
            result["resistances"] = sr.get("resistance", [])
        except Exception:
            pass

    return result


def get_data():
    """Get fresh or cached data. Guaranteed to return valid data dict."""
    global _cache, _cache_ts
    now = time.time()
    with _cache_lock:
        if _cache is not None and (now - _cache_ts) < CACHE_MS:
            return _cache
    # Fetch fresh
    data = fetch_all()
    with _cache_lock:
        _cache = data
        _cache_ts = now
    return data


# ═══════════════════════════════════════════════════
# Prediction engine
# ═══════════════════════════════════════════════════

def predict(klines, supports=None, resistances=None):
    """Optimized 7-factor model — volatility-adaptive, non-collinear weights.

    Factor weights are balanced (~15 pts each) and normalized by ATR.
    Direction thresholds based on score distribution (percentile-grounded).
    """
    if len(klines) < 30:
        return {"direction": "neutral", "confidence": 0, "factors": [],
                "price": 0, "open_price": 0, "change_pct": 0,
                "high": 0, "low": 0, "candle_status": "N/A",
                "candle_pct": 0, "total_score": 0, "volatility": "normal"}

    cl = [k["close"] for k in klines]
    op = [k["open"] for k in klines]
    hi = [k["high"] for k in klines]
    lo = [k["low"] for k in klines]
    vl = [k["volume"] for k in klines]
    cur = cl[-1]
    opn = op[-1]
    bull = cur > opn
    score = 0.0  # Use float for smoother accumulation
    factors = []

    # ── Volatility estimation (ATR as % of price) ──
    tr_list = []
    for i in range(1, min(15, len(hi))):
        tr_list.append(max(hi[-i] - lo[-i],
                           abs(hi[-i] - cl[-i-1]),
                           abs(lo[-i] - cl[-i-1])))
    atr = sum(tr_list) / len(tr_list) if tr_list else cur * 0.002
    vol_pct = atr / cur * 100  # ATR as % of price

    # Volatility regime classification
    if vol_pct < 0.15:
        vol_regime = "low"
    elif vol_pct > 0.50:
        vol_regime = "high"
    else:
        vol_regime = "normal"

    # Adaptive RSI thresholds per volatility regime
    rsi_thresholds = {
        "low":    (35, 65),    # Narrower range in low vol
        "normal": (30, 70),
        "high":   (22, 78),    # Wider range in high vol
    }
    rsi_os, rsi_ob = rsi_thresholds[vol_regime]

    # ── F1: Price Momentum (exponential-weighted, ATR-normalized) ──
    # Replaces old F1(current K-line) + F2(equal-weight momentum).
    # Exponential weighting: recent moves count more, normalized by volatility.
    weights = [0.40, 0.30, 0.20, 0.10]  # 4-period decay
    mom_score = 0.0
    for w, lag in zip(weights, [1, 2, 3, 4]):
        if len(cl) > lag:
            ret = (cl[-lag] - cl[-lag-1]) / cl[-lag-1] * 100
            mom_score += ret * w
    # Normalize by volatility: 1 ATR move ≈ 10 points
    if vol_pct > 0:
        mom_score = mom_score / vol_pct * 2.5
    mp = max(-25, min(25, round(mom_score)))
    score += mp
    factors.append({
        "name": "价格动能",
        "score": mp,
        "detail": f"{'上攻' if mp>=3 else ('下压' if mp<=-3 else '平')} {abs(mp)}分 "
                  f"(ATR {vol_pct:.2f}% | {'高波动' if vol_regime=='high' else ('低波动' if vol_regime=='low' else '正常')})"
    })

    # ── F2: RSI (volatility-adaptive thresholds) ──
    rv = rsi_current(cl)
    if rv is not None:
        if rv < rsi_os:
            rp = 12
            lab = f"超卖({rsi_os})"
        elif rv > rsi_ob:
            rp = -12
            lab = f"超买({rsi_ob})"
        elif rv < rsi_os + 8:
            rp = 6
            lab = f"接近超卖"
        elif rv > rsi_ob - 8:
            rp = -6
            lab = f"接近超买"
        else:
            rp = 0
            lab = "中性"
        score += rp
        factors.append({"name": "RSI", "score": rp, "detail": f"{lab} {rv:.0f} (阈值{int(rsi_os)}/{int(rsi_ob)})"})
    else:
        factors.append({"name": "RSI", "score": 0, "detail": "N/A"})

    # ── F3: Volume Confirmation ──
    if len(vl) >= 10:
        rv3 = sum(vl[-3:]) / 3
        bv7 = sum(vl[-10:-3]) / 7 if len(vl) >= 10 else 1
        vr = rv3 / bv7 if bv7 > 0 else 1
        if vr > 2.5:
            vp = 15 if bull else -15
            tier = "巨量"
        elif vr > 1.8:
            vp = 10 if bull else -10
            tier = "放量"
        elif vr > 1.2:
            vp = 5 if bull else -5
            tier = "温和放量"
        elif vr < 0.5:
            vp = -3 if bull else 3
            tier = "缩量"
        else:
            vp = 0
            tier = "平量"
        score += vp
        factors.append({"name": "成交量", "score": vp, "detail": f"{tier} {vr:.1f}x"})
    else:
        factors.append({"name": "成交量", "score": 0, "detail": "N/A"})

    # ── F4: EMA Triple Alignment (5/20/50) ──
    e5 = ema(cl, 5)
    e20 = ema(cl, 20)
    e50 = ema(cl, 50)
    if e5[-1] and e20[-1] and e50[-1]:
        if e5[-1] > e20[-1] > e50[-1]:
            ep = 12  # Perfect bullish fan
            detail = "EMA5>20>50 多头排列"
        elif e5[-1] < e20[-1] < e50[-1]:
            ep = -12  # Perfect bearish fan
            detail = "EMA5<20<50 空头排列"
        elif e5[-1] > e20[-1]:
            ep = 6  # Short-term bullish
            detail = "EMA5>20 短期偏多"
        elif e5[-1] < e20[-1]:
            ep = -6
            detail = "EMA5<20 短期偏空"
        else:
            ep = 0
            detail = "EMA缠绕"
        score += ep
        factors.append({"name": "EMA排列", "score": ep, "detail": detail})
    else:
        factors.append({"name": "EMA排列", "score": 0, "detail": "N/A"})

    # ── F5: Support/Resistance (ATR-normalized distance) ──
    sr_score = 0
    sr_detail = "N/A"
    if supports or resistances:
        ns = min(supports, key=lambda s: abs(opn - s)) if supports else None
        nr = min(resistances, key=lambda r: abs(opn - r)) if resistances else None
        atr_dist = atr  # 1 ATR as distance unit
        if ns and nr:
            # Position within S-R channel (0=at support, 1=at resistance)
            channel_pos = (opn - ns) / (nr - ns) if nr != ns else 0.5
            dist_to_s = (opn - ns) / atr_dist if atr_dist > 0 else 99
            dist_to_r = (nr - opn) / atr_dist if atr_dist > 0 else 99
            if channel_pos < 0.25 and dist_to_s < 1.5:
                sr_score = 12
                sr_detail = f"紧贴支撑{ns:.0f}(距{dist_to_s:.1f}ATR) 偏多"
            elif channel_pos > 0.75 and dist_to_r < 1.5:
                sr_score = -12
                sr_detail = f"紧贴阻力{nr:.0f}(距{dist_to_r:.1f}ATR) 偏空"
            else:
                sr_detail = f"区间中部 S={ns:.0f} R={nr:.0f} (位置{channel_pos:.0%})"
        elif ns:
            dist_s = (opn - ns) / atr_dist if atr_dist > 0 else 99
            if dist_s < 1.5:
                sr_score = 10
                sr_detail = f"接近支撑{ns:.0f}(距{dist_s:.1f}ATR) 偏多"
            else:
                sr_detail = f"距支撑{ns:.0f} {dist_s:.1f}ATR"
        elif nr:
            dist_r = (nr - opn) / atr_dist if atr_dist > 0 else 99
            if dist_r < 1.5:
                sr_score = -10
                sr_detail = f"接近阻力{nr:.0f}(距{dist_r:.1f}ATR) 偏空"
            else:
                sr_detail = f"距阻力{nr:.0f} {dist_r:.1f}ATR"
    score += sr_score
    factors.append({"name": "关键价位", "score": sr_score, "detail": sr_detail})

    # ── F6: Open vs VWAP (volume-weighted average price) ──
    vwap_score = 0
    vwap_detail = "N/A"
    if len(cl) >= 20 and len(vl) >= 20:
        typical_prices = [(hi[i] + lo[i] + cl[i]) / 3 for i in range(-20, 0)]
        vols = vl[-20:]
        vwap = sum(tp * v for tp, v in zip(typical_prices, vols)) / sum(vols) if sum(vols) > 0 else cur
        dev = (opn - vwap) / vwap * 100
        # Normalize by volatility
        dev_z = dev / vol_pct if vol_pct > 0 else 0
        if abs(dev_z) > 1.5:
            vwap_score = 8 if dev_z > 0 else -8
            vwap_detail = f"开盘{'高于' if dev>0 else '低于'}VWAP {abs(dev):.2f}% ({abs(dev_z):.1f}σ) {'偏多' if dev>0 else '偏空'}"
        elif abs(dev_z) > 0.5:
            vwap_score = 4 if dev_z > 0 else -4
            vwap_detail = f"开盘略{'高' if dev>0 else '低'}于VWAP ({abs(dev_z):.1f}σ)"
        else:
            vwap_detail = f"开盘紧贴VWAP (VWAP={vwap:.0f})"
    score += vwap_score
    factors.append({"name": "开盘vs VWAP", "score": vwap_score, "detail": vwap_detail})

    # ── F7: Volatility Environment (adjusts confidence, not direction) ──
    # Compare current vol to longer-term vol
    if len(cl) >= 30:
        tr_long = []
        for i in range(1, min(30, len(hi))):
            tr_long.append(max(hi[-i] - lo[-i],
                               abs(hi[-i] - cl[-i-1]),
                               abs(lo[-i] - cl[-i-1])))
        atr_long = sum(tr_long) / len(tr_long)
        vol_long = atr_long / cur * 100
        vol_ratio = vol_pct / vol_long if vol_long > 0 else 1.0

        if vol_ratio > 2.0:
            ve_score = -5
            ve_detail = f"波动率飙升({vol_ratio:.1f}x) 信号可靠性降低"
        elif vol_ratio > 1.4:
            ve_score = -2
            ve_detail = f"波动率扩大({vol_ratio:.1f}x)"
        elif vol_ratio < 0.5:
            ve_score = 3
            ve_detail = f"波动率收缩({vol_ratio:.1f}x) 可能酝酿突破"
        elif vol_ratio < 0.7:
            ve_score = 1
            ve_detail = f"低波动({vol_ratio:.1f}x)"
        else:
            ve_score = 0
            ve_detail = f"波动率正常({vol_ratio:.1f}x)"
        score += ve_score
        factors.append({"name": "波动率环境", "score": ve_score, "detail": ve_detail})
    else:
        factors.append({"name": "波动率环境", "score": 0, "detail": "N/A"})

    # ── Direction: statistical thresholds ──
    # With 7 balanced factors (each ~12-15 max), total range ~±90.
    # Thresholds based on score percentile estimates:
    #   |score| > 14 → strong signal (top ~15%)
    #   |score| > 7  → leaning (top ~35%)
    #   else        → neutral
    if score > 14:
        d = "up"
    elif score > 7:
        d = "leaning_up"
    elif score >= -7:
        d = "neutral"
    elif score >= -14:
        d = "leaning_down"
    else:
        d = "down"

    # ── Confidence: normalized to 0-100 ──
    abs_score = abs(score)
    conf = round(min(abs_score / 90 * 100, 95), 1)
    if d == "neutral":
        conf = max(conf, 40 + (7 - abs_score) / 7 * 30)  # Higher conf near boundary
    elif d in ("leaning_up", "leaning_down"):
        conf = round(50 + abs_score / 14 * 30, 1)  # 50-80 range

    # ── Candle progress ──
    now = datetime.now(timezone.utc)
    secs = 300 if klines[0].get("_tf") == "5m" else 900
    if klines[0].get("_tf") == "15m":
        secs = 900
    c_start = now.replace(second=0, microsecond=0)
    off = c_start.minute % (5 if secs == 300 else 15)
    c_start = c_start.replace(minute=c_start.minute - off)
    cp = min(round((now - c_start).total_seconds() / secs * 100, 0), 99)

    return {"direction": d, "confidence": conf, "total_score": round(score),
            "factors": factors, "price": cur, "open_price": opn,
            "change_pct": round((cur - opn) / opn * 100, 4), "high": round(hi[-1], 1),
            "low": round(lo[-1], 1),
            "candle_status": "阳线" if bull else "阴线", "candle_pct": cp,
            "volatility": vol_regime}


def build_analysis(raw):
    """Build full analysis payload from raw fetched data."""
    ticker = raw["ticker"]
    k5 = raw["klines_5m"]
    k15 = raw["klines_15m"]
    supports = raw["supports"]
    resistances = raw["resistances"]

    price = float(ticker.get("lastPrice", 0) or 0)
    chg24 = round(float(ticker.get("priceChangePercent", 0) or 0), 2)
    h24 = float(ticker.get("highPrice", 0) or 0)
    l24 = float(ticker.get("lowPrice", 0) or 0)
    v24 = float(ticker.get("volume", 0) or 0)

    chart = [{"t": k["time"], "o": k["open"], "h": k["high"],
              "l": k["low"], "c": k["close"]} for k in k15[-40:]] if k15 else []

    pred_5m = predict(k5, supports, resistances) if k5 else None
    pred_15m = predict(k15, supports, resistances) if k15 else None

    return {
        "ok": bool(price > 0),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": price, "change24": chg24,
        "high24": h24, "low24": l24, "vol24": v24,
        "pred_5m": pred_5m or {"direction": "neutral", "confidence": 0, "factors": [],
                                "price": 0, "open_price": 0, "change_pct": 0,
                                "high": 0, "low": 0, "candle_status": "N/A",
                                "candle_pct": 0, "total_score": 0},
        "pred_15m": pred_15m or {"direction": "neutral", "confidence": 0, "factors": [],
                                  "price": 0, "open_price": 0, "change_pct": 0,
                                  "high": 0, "low": 0, "candle_status": "N/A",
                                  "candle_pct": 0, "total_score": 0},
        "chart": chart,
        "error": raw.get("error"),
    }


# ═══════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/analysis")
def api_analysis():
    """On-demand analysis: fetch data, compute, return."""
    raw = get_data()
    return jsonify(build_analysis(raw))


@app.route("/api/health")
def api_health():
    """Debug endpoint: show raw data status."""
    raw = get_data()
    tk = raw["ticker"]
    return jsonify({
        "price": tk.get("lastPrice"),
        "ticker_ok": bool(tk.get("lastPrice")),
        "klines_5m_count": len(raw["klines_5m"]),
        "klines_15m_count": len(raw["klines_15m"]),
        "klines_1h_count": len(raw["klines_1h"]),
        "supports": raw["supports"],
        "resistances": raw["resistances"],
        "error": raw.get("error"),
        "cache_age_ms": round((time.time() - _cache_ts) * 1000, 1) if _cache_ts else 0,
    })


@app.route("/api/stream")
def api_stream():
    """SSE stream — fresh data every 100ms."""
    def event_stream():
        last_hash = None
        while True:
            raw = get_data()
            data = build_analysis(raw)
            h = hash(json.dumps({
                "p": data["price"], "c": data["change24"],
                "d5": data["pred_5m"]["direction"], "s5": data["pred_5m"]["total_score"],
                "d15": data["pred_15m"]["direction"], "s15": data["pred_15m"]["total_score"],
            }, sort_keys=True))
            if h != last_hash:
                last_hash = h
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            time.sleep(0.1)  # 100ms push for lower latency

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


def warmup():
    """Pre-fetch data on startup so first request doesn't block."""
    try:
        get_data()
    except Exception:
        pass


# Pre-warm in background thread so gunicorn loads without delay
_warmup_thread = threading.Thread(target=warmup, daemon=True)
_warmup_thread.start()


if __name__ == "__main__":
    warmup()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
