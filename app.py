# -*- coding: utf-8 -*-
"""BTC 5M/15M Direction Predictor"""
import json, threading, time, urllib.request
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify

app = Flask(__name__)
import logging; logging.getLogger("werkzeug").setLevel(logging.ERROR)

_lock = threading.Lock()
_cache = None
_last_refresh = 0.0

def fetch(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def ema(vals, p):
    r=[None]*len(vals)
    if len(vals)<p: return r
    k=2.0/(p+1); a=sum(vals[:p])/p; r[p-1]=a
    for i in range(p,len(vals)): r[i]=(vals[i]-r[i-1])*k+r[i-1]
    return r

def rsi_val(cl, p=14):
    if len(cl) < p+1: return None
    g=l=0.0
    for i in range(1,p+1):
        d=cl[i]-cl[i-1]
        if d>0: g+=d
        else: l-=d
    ag=g/p; al=l/p or 0.0001
    result=100-100/(1+ag/al)
    for i in range(p+1,len(cl)):
        d=cl[i]-cl[i-1]
        ag=(ag*(p-1)+(d if d>0 else 0))/p
        al=(al*(p-1)+(-d if d<0 else 0))/p or 0.0001
        result=100-100/(1+ag/al)
    return result

def predict(klines, tf):
    if len(klines) < 30:
        return {"direction":"neutral","confidence":0,"factors":[],"price":0,"open_price":0,"change_pct":0,"high":0,"low":0,"candle_status":"N/A","candle_pct":0,"total_score":0}

    cl=[k["close"] for k in klines]; op=[k["open"] for k in klines]
    hi=[k["high"] for k in klines]; lo=[k["low"] for k in klines]
    vl=[k["volume"] for k in klines]
    cur=cl[-1]; opn=op[-1]; score=0; factors=[]

    # 1. candle
    chg=(cur-opn)/opn*100; bull=cur>opn
    pts=min(int(abs(chg)*20),30)*(1 if bull else -1)
    score+=pts
    factors.append({"name":"当前K线","score":pts,"detail":("阳线 +" if bull else "阴线 ")+f"{chg:+.3f}%"})

    # 2. momentum
    mom=0
    for i in range(3,0,-1):
        if len(cl)>i: mom+=(cl[-i]-cl[-i-1])/cl[-i-1]*100*i
    mp=max(min(int(mom*5),25),-25); score+=mp
    factors.append({"name":"动量","score":mp,"detail":("上攻 " if mp>0 else ("下压 " if mp<0 else "平 "))+str(abs(mp))+"分"})

    # 3. RSI
    rv=rsi_val(cl)
    if rv is not None:
        rp=10 if rv<30 else (-10 if rv>70 else 0)
        score+=rp
        lab="超卖" if rv<30 else ("超买" if rv>70 else "中性")
        factors.append({"name":"RSI","score":rp,"detail":f"{lab} {rv:.0f}"})
    else:
        factors.append({"name":"RSI","score":0,"detail":"N/A"})

    # 4. volume
    if len(vl)>=10:
        rv3=sum(vl[-3:])/3; bv7=sum(vl[-10:-3])/7 if len(vl)>=10 else 1
        vr=rv3/bv7 if bv7>0 else 1
        vp=15 if vr>2 and bull else (-15 if vr>2 and not bull else (8 if vr>1.3 and bull else (-8 if vr>1.3 and not bull else 0)))
        score+=vp
        factors.append({"name":"成交量","score":vp,"detail":("放量" if vr>2 else ("温和" if vr>1.3 else "平量"))+f" {vr:.1f}x"})
    else:
        factors.append({"name":"成交量","score":0,"detail":"N/A"})

    # 5. EMA
    e5=ema(cl,5); e20=ema(cl,20)
    if e5[-1] and e20[-1]:
        ep=10 if e5[-1]>e20[-1] else -10; score+=ep
        factors.append({"name":"EMA","score":ep,"detail":"EMA5"+((">EMA20 短多") if ep>0 else "<EMA20 短空")})
    else:
        factors.append({"name":"EMA","score":0,"detail":"N/A"})

    conf=round(min(abs(score)/100*100,95),1)
    d="up" if score>5 else ("down" if score<-5 else "neutral")
    if d=="neutral": conf=max(conf,50-abs(score)*2)

    now=datetime.now(timezone.utc)
    secs=300 if tf=="5m" else 900
    c_start=now.replace(second=0,microsecond=0)
    off=c_start.minute%(5 if tf=="5m" else 15)
    c_start=c_start.replace(minute=c_start.minute-off)
    elapsed=(now-c_start).total_seconds()
    cp=min(round(elapsed/secs*100,0),99)

    return {"direction":d,"confidence":conf,"total_score":score,"factors":factors,
            "price":cur,"open_price":opn,"change_pct":round(chg,4),"high":round(hi[-1],1),
            "low":round(lo[-1],1),"candle_status":"阳线" if bull else "阴线","candle_pct":cp}

def analyze():
    r5 = fetch("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=5m&limit=60")["data"]; r5.reverse()
    r15 = fetch("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=15m&limit=60")["data"]; r15.reverse()
    tk = fetch("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]

    def parse(raw):
        return [{"time":int(k[0]),"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"volume":float(k[5])} for k in raw]

    k5=parse(r5); k15=parse(r15)
    price=float(tk["last"]); chg24=round((price/float(tk["open24h"])-1)*100,2)
    h24=float(tk["high24h"]); l24=float(tk["low24h"]); v24=float(tk["vol24h"])
    chart=[{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"]} for k in k15[-40:]]

    return {"ok":True,"ts":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "price":price,"change24":chg24,"high24":h24,"low24":l24,"vol24":v24,
            "pred_5m":predict(k5,"5m"),"pred_15m":predict(k15,"15m"),"chart":chart}

def refresh():
    global _cache, _last_refresh
    try:
        d=analyze()
        with _lock: _cache=d; _last_refresh=time.time()
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
        t=threading.Thread(target=refresh, daemon=True)
        t.start()
    with _lock:
        if _cache is None: return jsonify({"error":"loading"}), 503
        return jsonify(_cache)

if __name__ == "__main__":
    refresh()
    app.run(host="0.0.0.0", port=5000, debug=False)
