# -*- coding: utf-8 -*-
"""BTC 5M/15M Direction Predictor - Binance data source"""
import json
import threading
import time
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify

import data as d
from indicators import ema, rsi_current, find_support_resistance

app = Flask(__name__)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_lock = threading.Lock()
_cache = None
_last_refresh = 0.0


def predict(klines, supports=None, resistances=None):
    """7-factor direction prediction based on opening price.

    Factors: K-line, momentum, RSI, volume, EMA, support/resistance gap,
             opening-price vs EMA deviation.
    """
    if len(klines) < 30:
        return {
            "direction": "neutral",
            "confidence": 0,
            "factors": [],
            "price": 0,
            "open_price": 0,
            "change_pct": 0,
            "high": 0,
            "low": 0,
            "candle_status": "N/A",
            "candle_pct": 0,
            "total_score": 0,
        }

    cl = [k["close"] for k in klines]
    op = [k["open"] for k in klines]
    hi = [k["high"] for k in klines]
    lo = [k["low"] for k in klines]
    vl = [k["volume"] for k in klines]
    cur = cl[-1]
    opn = op[-1]
    score = 0
    factors = []

    # ---- Factor 1: Current K-line ----
    chg = (cur - opn) / opn * 100
    bull = cur > opn
    pts = min(int(abs(chg) * 20), 30) * (1 if bull else -1)
    score += pts
    factors.append({
        "name": "当前K线",
        "score": pts,
        "detail": ("阳线 +" if bull else "阴线 ") + f"{chg:+.3f}%",
    })

    # ---- Factor 2: Momentum (weighted 3-period) ----
    mom = 0
    for i in range(3, 0, -1):
        if len(cl) > i:
            mom += (cl[-i] - cl[-i - 1]) / cl[-i - 1] * 100 * i
    mp = max(min(int(mom * 5), 25), -25)
    score += mp
    factors.append({
        "name": "动量",
        "score": mp,
        "detail": ("上攻 " if mp > 0 else ("下压 " if mp < 0 else "平 ")) + str(abs(mp)) + "分",
    })

    # ---- Factor 3: RSI ----
    rv = rsi_current(cl)
    if rv is not None:
        rp = 10 if rv < 30 else (-10 if rv > 70 else 0)
        score += rp
        lab = "超卖" if rv < 30 else ("超买" if rv > 70 else "中性")
        factors.append({
            "name": "RSI",
            "score": rp,
            "detail": f"{lab} {rv:.0f}",
        })
    else:
        factors.append({"name": "RSI", "score": 0, "detail": "N/A"})

    # ---- Factor 4: Volume surge ----
    if len(vl) >= 10:
        rv3 = sum(vl[-3:]) / 3
        bv7 = sum(vl[-10:-3]) / 7 if len(vl) >= 10 else 1
        vr = rv3 / bv7 if bv7 > 0 else 1
        vp = (
            15
            if vr > 2 and bull
            else (
                -15
                if vr > 2 and not bull
                else (8 if vr > 1.3 and bull else (-8 if vr > 1.3 and not bull else 0))
            )
        )
        score += vp
        factors.append({
            "name": "成交量",
            "score": vp,
            "detail": ("放量" if vr > 2 else ("温和" if vr > 1.3 else "平量")) + f" {vr:.1f}x",
        })
    else:
        factors.append({"name": "成交量", "score": 0, "detail": "N/A"})

    # ---- Factor 5: EMA alignment (5/20) ----
    e5 = ema(cl, 5)
    e20 = ema(cl, 20)
    if e5[-1] and e20[-1]:
        ep = 10 if e5[-1] > e20[-1] else -10
        score += ep
        factors.append({
            "name": "EMA",
            "score": ep,
            "detail": "EMA5" + (">EMA20 短多" if ep > 0 else "<EMA20 短空"),
        })
    else:
        factors.append({"name": "EMA", "score": 0, "detail": "N/A"})

    # ---- Factor 6: Support/Resistance gap from opening price ----
    sr_score = 0
    sr_detail = "N/A"
    if supports or resistances:
        nearest_support = None
        nearest_resistance = None
        if supports:
            nearest_support = min(supports, key=lambda s: abs(opn - s))
        if resistances:
            nearest_resistance = min(resistances, key=lambda r: abs(opn - r))
        gap_pct = 0
        if nearest_support and nearest_resistance:
            gap_pct = (nearest_resistance - opn) / (nearest_resistance - nearest_support)
            if gap_pct < 0.3:
                sr_score = 10
                sr_detail = f"开于支撑{nearest_support:.0f}附近 偏多"
            elif gap_pct > 0.7:
                sr_score = -10
                sr_detail = f"开于阻力{nearest_resistance:.0f}附近 偏空"
            else:
                sr_detail = f"开于区间中部 S={nearest_support:.0f} R={nearest_resistance:.0f}"
        elif nearest_support:
            gap_pct = (opn - nearest_support) / opn * 100
            if gap_pct < 1.0:
                sr_score = 8
                sr_detail = f"接近支撑{nearest_support:.0f}({gap_pct:.1f}%) 偏多"
            else:
                sr_detail = f"距支撑{nearest_support:.0f} {gap_pct:.1f}%"
        elif nearest_resistance:
            gap_pct = (nearest_resistance - opn) / opn * 100
            if gap_pct < 1.0:
                sr_score = -8
                sr_detail = f"接近阻力{nearest_resistance:.0f}({gap_pct:.1f}%) 偏空"
            else:
                sr_detail = f"距阻力{nearest_resistance:.0f} {gap_pct:.1f}%"
    score += sr_score
    factors.append({
        "name": "关键位",
        "score": sr_score,
        "detail": sr_detail,
    })

    # ---- Factor 7: Opening-price vs EMA deviation ----
    ema20_val = e20[-1] if e20 and e20[-1] else None
    ema50_arr = ema(cl, 50)
    ema50_val = ema50_arr[-1] if ema50_arr and ema50_arr[-1] else None
    o2e_score = 0
    o2e_detail = "N/A"
    if ema20_val and ema50_val:
        o_vs_20 = (opn - ema20_val) / ema20_val * 100
        o_vs_50 = (opn - ema50_val) / ema50_val * 100
        if o_vs_20 > 0 and o_vs_50 > 0:
            o2e_score = 8
            o2e_detail = f"开盘高于EMA20/50 偏多({o_vs_20:+.1f}%/{o_vs_50:+.1f}%)"
        elif o_vs_20 < 0 and o_vs_50 < 0:
            o2e_score = -8
            o2e_detail = f"开盘低于EMA20/50 偏空({o_vs_20:+.1f}%/{o_vs_50:+.1f}%)"
        else:
            o2e_detail = f"EMA间震荡({o_vs_20:+.1f}%/{o_vs_50:+.1f}%)"
    elif ema20_val:
        o_vs_20 = (opn - ema20_val) / ema20_val * 100
        if abs(o_vs_20) > 1:
            o2e_score = 5 if o_vs_20 > 0 else -5
            o2e_detail = f"开盘vs EMA20 {o_vs_20:+.1f}%"
        else:
            o2e_detail = f"开盘紧贴EMA20 ({o_vs_20:+.1f}%)"
    score += o2e_score
    factors.append({
        "name": "开盘vs EMA",
        "score": o2e_score,
        "detail": o2e_detail,
    })

    # ---- Confidence / Direction ----
    conf = round(min(abs(score) / 120 * 100, 95), 1)
    d = "up" if score > 5 else ("down" if score < -5 else "neutral")
    if d == "neutral":
        conf = max(conf, 50 - abs(score) * 2)

    # ---- Candle progress ----
    now = datetime.now(timezone.utc)
    secs = 300 if klines[0].get("_tf") == "5m" else 900
    # Default to 5m candle progress if not specified
    if klines[0].get("_tf") == "15m":
        secs = 900
    c_start = now.replace(second=0, microsecond=0)
    off = c_start.minute % (5 if secs == 300 else 15)
    c_start = c_start.replace(minute=c_start.minute - off)
    elapsed = (now - c_start).total_seconds()
    cp = min(round(elapsed / secs * 100, 0), 99)

    return {
        "direction": d,
        "confidence": conf,
        "total_score": score,
        "factors": factors,
        "price": cur,
        "open_price": opn,
        "change_pct": round(chg, 4),
        "high": round(hi[-1], 1),
        "low": round(lo[-1], 1),
        "candle_status": "阳线" if bull else "阴线",
        "candle_pct": cp,
    }


def analyze():
    """Fetch data from Binance and build full analysis payload."""
    # Fetch 5m and 15m klines, plus ticker
    r5_raw = d.fetch_klines("BTCUSDT", "5m", 60)
    r15_raw = d.fetch_klines("BTCUSDT", "15m", 60)
    tk = d.fetch_ticker("BTCUSDT")

    k5 = d.parse_klines_to_dicts(r5_raw)
    k15 = d.parse_klines_to_dicts(r15_raw)

    # Tag each dict with its timeframe for candle progress
    for k in k5:
        k["_tf"] = "5m"
    for k in k15:
        k["_tf"] = "15m"

    price = float(tk["lastPrice"])
    chg24 = round(float(tk["priceChangePercent"]), 2)
    h24 = float(tk["highPrice"])
    l24 = float(tk["lowPrice"])
    v24 = float(tk["volume"])

    # Compute support/resistance from 1h data for Factor 6
    r1h_raw = d.fetch_klines("BTCUSDT", "1h", 60)
    k1h = d.parse_klines_to_dicts(r1h_raw)
    sr = find_support_resistance(k1h, 50) if k1h else {}
    supports = sr.get("support", [])
    resistances = sr.get("resistance", [])

    chart = [
        {"t": k["time"], "o": k["open"], "h": k["high"], "l": k["low"], "c": k["close"]}
        for k in k15[-40:]
    ]

    return {
        "ok": True,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": price,
        "change24": chg24,
        "high24": h24,
        "low24": l24,
        "vol24": v24,
        "pred_5m": predict(k5, supports, resistances),
        "pred_15m": predict(k15, supports, resistances),
        "chart": chart,
    }


def refresh():
    global _cache, _last_refresh
    try:
        d = analyze()
        with _lock:
            _cache = d
            _last_refresh = time.time()
    except Exception as e:
        print(f"Refresh error: {e}")


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/analysis")
def api():
    global _cache, _last_refresh
    if _cache is None:
        refresh()
    elif time.time() - _last_refresh > 10:
        t = threading.Thread(target=refresh, daemon=True)
        t.start()
    with _lock:
        if _cache is None:
            return jsonify({"error": "loading"}), 503
        return jsonify(_cache)


if __name__ == "__main__":
    refresh()
    app.run(host="0.0.0.0", port=5000, debug=False)
