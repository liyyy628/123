# -*- coding: utf-8 -*-
"""BTC 5M/15M Direction Predictor — High-speed REST + SSE

Binance REST API polled at 200ms → SSE push to browser at 200ms.
Data latency: <300ms (network + compute).
"""
import json
import threading
import time
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, Response

import live_feed as lf
from indicators import ema, rsi_current

app = Flask(__name__)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_started = False
_start_lock = threading.Lock()


def predict(klines, supports=None, resistances=None):
    """7-factor direction prediction."""
    if len(klines) < 30:
        return {
            "direction": "neutral", "confidence": 0, "factors": [],
            "price": 0, "open_price": 0, "change_pct": 0,
            "high": 0, "low": 0, "candle_status": "N/A",
            "candle_pct": 0, "total_score": 0,
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

    # F1: Current K-line
    chg = (cur - opn) / opn * 100
    bull = cur > opn
    pts = min(int(abs(chg) * 20), 30) * (1 if bull else -1)
    score += pts
    factors.append({"name": "当前K线", "score": pts, "detail": ("阳线 +" if bull else "阴线 ") + f"{chg:+.3f}%"})

    # F2: Momentum
    mom = 0
    for i in range(3, 0, -1):
        if len(cl) > i:
            mom += (cl[-i] - cl[-i - 1]) / cl[-i - 1] * 100 * i
    mp = max(min(int(mom * 5), 25), -25)
    score += mp
    factors.append({"name": "动量", "score": mp, "detail": ("上攻 " if mp > 0 else ("下压 " if mp < 0 else "平 ")) + str(abs(mp)) + "分"})

    # F3: RSI
    rv = rsi_current(cl)
    if rv is not None:
        rp = 10 if rv < 30 else (-10 if rv > 70 else 0)
        score += rp
        lab = "超卖" if rv < 30 else ("超买" if rv > 70 else "中性")
        factors.append({"name": "RSI", "score": rp, "detail": f"{lab} {rv:.0f}"})
    else:
        factors.append({"name": "RSI", "score": 0, "detail": "N/A"})

    # F4: Volume
    if len(vl) >= 10:
        rv3 = sum(vl[-3:]) / 3
        bv7 = sum(vl[-10:-3]) / 7 if len(vl) >= 10 else 1
        vr = rv3 / bv7 if bv7 > 0 else 1
        vp = 15 if vr > 2 and bull else (-15 if vr > 2 and not bull else (8 if vr > 1.3 and bull else (-8 if vr > 1.3 and not bull else 0)))
        score += vp
        factors.append({"name": "成交量", "score": vp, "detail": ("放量" if vr > 2 else ("温和" if vr > 1.3 else "平量")) + f" {vr:.1f}x"})
    else:
        factors.append({"name": "成交量", "score": 0, "detail": "N/A"})

    # F5: EMA
    e5 = ema(cl, 5)
    e20 = ema(cl, 20)
    if e5[-1] and e20[-1]:
        ep = 10 if e5[-1] > e20[-1] else -10
        score += ep
        factors.append({"name": "EMA", "score": ep, "detail": "EMA5" + (">EMA20 短多" if ep > 0 else "<EMA20 短空")})
    else:
        factors.append({"name": "EMA", "score": 0, "detail": "N/A"})

    # F6: Support/Resistance
    sr_score = 0
    sr_detail = "N/A"
    if supports or resistances:
        ns = min(supports, key=lambda s: abs(opn - s)) if supports else None
        nr = min(resistances, key=lambda r: abs(opn - r)) if resistances else None
        if ns and nr:
            gp = (nr - opn) / (nr - ns)
            if gp < 0.3:
                sr_score = 10
                sr_detail = f"开于支撑{ns:.0f}附近 偏多"
            elif gp > 0.7:
                sr_score = -10
                sr_detail = f"开于阻力{nr:.0f}附近 偏空"
            else:
                sr_detail = f"开于区间中部 S={ns:.0f} R={nr:.0f}"
        elif ns:
            gp = (opn - ns) / opn * 100
            if gp < 1.0:
                sr_score = 8
                sr_detail = f"接近支撑{ns:.0f}({gp:.1f}%) 偏多"
            else:
                sr_detail = f"距支撑{ns:.0f} {gp:.1f}%"
        elif nr:
            gp = (nr - opn) / opn * 100
            if gp < 1.0:
                sr_score = -8
                sr_detail = f"接近阻力{nr:.0f}({gp:.1f}%) 偏空"
            else:
                sr_detail = f"距阻力{nr:.0f} {gp:.1f}%"
    score += sr_score
    factors.append({"name": "关键位", "score": sr_score, "detail": sr_detail})

    # F7: Open vs EMA
    ema20_val = e20[-1] if e20 and e20[-1] else None
    ema50_arr = ema(cl, 50)
    ema50_val = ema50_arr[-1] if ema50_arr and ema50_arr[-1] else None
    o2e_score = 0
    o2e_detail = "N/A"
    if ema20_val and ema50_val:
        ov20 = (opn - ema20_val) / ema20_val * 100
        ov50 = (opn - ema50_val) / ema50_val * 100
        if ov20 > 0 and ov50 > 0:
            o2e_score = 8
            o2e_detail = f"开盘高于EMA20/50 偏多({ov20:+.1f}%/{ov50:+.1f}%)"
        elif ov20 < 0 and ov50 < 0:
            o2e_score = -8
            o2e_detail = f"开盘低于EMA20/50 偏空({ov20:+.1f}%/{ov50:+.1f}%)"
        else:
            o2e_detail = f"EMA间震荡({ov20:+.1f}%/{ov50:+.1f}%)"
    elif ema20_val:
        ov20 = (opn - ema20_val) / ema20_val * 100
        if abs(ov20) > 1:
            o2e_score = 5 if ov20 > 0 else -5
            o2e_detail = f"开盘vs EMA20 {ov20:+.1f}%"
        else:
            o2e_detail = f"开盘紧贴EMA20 ({ov20:+.1f}%)"
    score += o2e_score
    factors.append({"name": "开盘vs EMA", "score": o2e_score, "detail": o2e_detail})

    # Direction
    if score > 5:
        d = "up"
    elif score > 2:
        d = "leaning_up"
    elif score >= -2:
        d = "neutral"
    elif score >= -5:
        d = "leaning_down"
    else:
        d = "down"

    abs_score = abs(score)
    conf = round(min(abs_score / 100 * 100, 95), 1)
    if d == "neutral":
        conf = max(conf, 50 - abs_score)
    elif d in ("leaning_up", "leaning_down"):
        conf = round(min(abs_score / 7 * 60 + 20, 70), 1)

    # Candle progress
    now = datetime.now(timezone.utc)
    secs = 300 if klines[0].get("_tf") == "5m" else 900
    if klines[0].get("_tf") == "15m":
        secs = 900
    c_start = now.replace(second=0, microsecond=0)
    off = c_start.minute % (5 if secs == 300 else 15)
    c_start = c_start.replace(minute=c_start.minute - off)
    cp = min(round((now - c_start).total_seconds() / secs * 100, 0), 99)

    return {
        "direction": d, "confidence": conf, "total_score": score,
        "factors": factors, "price": cur, "open_price": opn,
        "change_pct": round(chg, 4), "high": round(hi[-1], 1),
        "low": round(lo[-1], 1),
        "candle_status": "阳线" if bull else "阴线", "candle_pct": cp,
    }


def build_analysis():
    """Build analysis payload from live feed snapshot."""
    snap = lf.get_snapshot()
    ticker = snap["ticker"]
    k5 = snap["klines_5m"]
    k15 = snap["klines_15m"]

    price = float(ticker.get("lastPrice", 0) or 0)
    chg24 = round(float(ticker.get("priceChangePercent", 0) or 0), 2)
    h24 = float(ticker.get("highPrice", 0) or 0)
    l24 = float(ticker.get("lowPrice", 0) or 0)
    v24 = float(ticker.get("volume", 0) or 0)

    chart = [{"t": k["time"], "o": k["open"], "h": k["high"], "l": k["low"], "c": k["close"]} for k in k15[-40:]]

    return {
        "ok": bool(price > 0),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": price,
        "change24": chg24,
        "high24": h24,
        "low24": l24,
        "vol24": v24,
        "pred_5m": predict(k5, snap["supports"], snap["resistances"]),
        "pred_15m": predict(k15, snap["supports"], snap["resistances"]),
        "chart": chart,
        "age_ms": snap["age_ms"],
        "has_data": snap["has_data"],
    }


# ═══════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/analysis")
def api_analysis():
    return jsonify(build_analysis())


@app.route("/api/stream")
def api_stream():
    """SSE endpoint — pushes updates at 200ms intervals."""
    def event_stream():
        last_hash = None
        while True:
            data = build_analysis()
            h = hash(json.dumps({
                "p": data["price"], "c24": data["change24"],
                "p5d": data["pred_5m"]["direction"], "p5s": data["pred_5m"]["total_score"],
                "p15d": data["pred_15m"]["direction"], "p15s": data["pred_15m"]["total_score"],
            }, sort_keys=True))
            if h != last_hash:
                last_hash = h
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            time.sleep(0.2)  # 200ms push interval

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


# ═══════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════

def _ensure_live_feed():
    global _started
    with _start_lock:
        if not _started:
            _started = True
            lf.start()


_ensure_live_feed()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
