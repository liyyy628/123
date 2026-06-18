# -*- coding: utf-8 -*-
"""BTC Trading Dashboard - Flask web server"""
import json, os, sys, logging, threading, time, urllib.request
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

_latest = None
_lock = threading.Lock()

def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def _ema(values, period):
    r = [None]*len(values)
    if len(values) < period: return r
    k = 2.0/(period+1); a = sum(values[:period])/period; r[period-1] = a
    for i in range(period, len(values)): r[i] = (values[i]-r[i-1])*k + r[i-1]
    return r

def _rsi(cl, p=14):
    r = [None]*len(cl)
    if len(cl) < p+1: return r
    g=l=0.0
    for i in range(1,p+1):
        d=cl[i]-cl[i-1]
        if d>0: g+=d
        else: l-=d
    ag=g/p; al=l/p or 0.0001; r[p]=100-100/(1+ag/al)
    for i in range(p+1,len(cl)):
        d=cl[i]-cl[i-1]
        ag=(ag*(p-1)+(d if d>0 else 0))/p
        al=(al*(p-1)+(-d if d<0 else 0))/p or 0.0001
        r[i]=100-100/(1+ag/al)
    return r

def _atr(highs,lows,cls,p=14):
    n=len(highs); trs=[0]*n; r=[None]*n
    for i in range(n):
        if i==0: trs[i]=highs[i]-lows[i]
        else: trs[i]=max(highs[i]-lows[i],abs(highs[i]-cls[i-1]),abs(lows[i]-cls[i-1]))
    if n<p: return r
    a=sum(trs[:p])/p; r[p-1]=a
    for i in range(p,n): r[i]=(r[i-1]*(p-1)+trs[i])/p
    return r

def analyze():
    try:
        # OKX data
        k15 = _get("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=15m&limit=200")["data"]
        k15.reverse()
        k1h = _get("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1H&limit=100")["data"]
        k1h.reverse()
        k4h = _get("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=4H&limit=100")["data"]
        k4h.reverse()
        ob = _get("https://www.okx.com/api/v5/market/books?instId=BTC-USDT&sz=20")["data"][0]
        tk = _get("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]
        try: fr = float(_get("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP")["data"][0]["fundingRate"])
        except: fr = 0
        try: fg = int(_get("https://api.alternative.me/fng/?limit=1")["data"][0]["value"])
        except: fg = None

        def parse(raw):
            return [{"time":int(k[0]),"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"volume":float(k[5])} for k in raw]

        k15d = parse(k15); k1hd = parse(k1h); k4hd = parse(k4h)
        price = float(tk["last"])
        chg = round((price/float(tk["open24h"])-1)*100,2)
        h24 = float(tk["high24h"]); l24 = float(tk["low24h"]); v24 = float(tk["vol24h"])

        c4 = [k["close"] for k in k4hd]
        c15 = [k["close"] for k in k15d]; h15=[k["high"] for k in k15d]; l15=[k["low"] for k in k15d]
        e20=_ema(c4,20); e50=_ema(c4,50)
        trend = "sideways"
        if e20[-1] and e50[-1]:
            if e20[-1]>e50[-1]: trend="bullish"
            elif e20[-1]<e50[-1]: trend="bearish"
        rv=_rsi(c15)[-1] or 0; av=_atr(h15,l15,c15)[-1] or 0

        # SR
        h1=[k["high"] for k in k1hd]; l1=[k["low"] for k in k1hd]
        rs=[]; ss=[]
        for i in range(2,len(k1hd)-2):
            if h1[i]>h1[i-1] and h1[i]>h1[i-2] and h1[i]>h1[i+1] and h1[i]>h1[i+2]: rs.append(h1[i])
            if l1[i]<l1[i-1] and l1[i]<l1[i-2] and l1[i]<l1[i+1] and l1[i]<l1[i+2]: ss.append(l1[i])
        def cluster(pts,cur):
            if not pts: return []
            sl=sorted(pts); g=[[sl[0]]]
            for v in sl[1:]:
                if abs(v-g[-1][0])/g[-1][0]<0.002: g[-1].append(v)
                else: g.append([v])
            return [(round(sum(gg)/len(gg),1),len(gg)) for gg in g if abs(sum(gg)/len(gg)-cur)/cur<0.05]
        sups=[p for p,_ in sorted(cluster(ss,price))[:3]]
        res=[p for p,_ in sorted(cluster(rs,price),reverse=True)[:3]]

        # Signals
        sigs=[]; sc=0
        k_last=k15d[-1]; k_prev=k15d[-2]
        o,hi,lo,c = k_last["open"],k_last["high"],k_last["low"],k_last["close"]
        body=abs(c-o)
        if body>0 and hi>lo:
            u=hi-max(c,o); lw=min(c,o)-lo
            ptn = "hammer" if lw>=2*body and u<=body*0.3 else ("shooting_star" if u>=2*body and lw<=body*0.3 else None)
        else: ptn=None
        # engulfing
        po,pc=k_prev["open"],k_prev["close"]; co2,cc2=k_last["open"],k_last["close"]
        pb=abs(pc-po); cb=abs(cc2-co2)
        if pb>0 and cb>0:
            if pc<po and cc2>co2 and co2<=pc and cc2>=po: ptn="bull_engulf"
            if pc>po and cc2<co2 and co2>=pc and cc2<=po: ptn="bear_engulf"
        sigs.append({"name":"K线形态","passed":bool(ptn),"detail":ptn or "无"})
        if ptn: sc+=1

        ef=_ema(c15,12); es=_ema(c15,26)
        ml=[None]*len(c15)
        for i in range(len(c15)):
            if ef[i] and es[i]: ml[i]=ef[i]-es[i]
        vm=[v for v in ml if v is not None]
        if len(vm)>=9:
            se=_ema(vm,9); mc=ml[-1]or 0; mp=ml[-2]or 0
            sk=se[-1]if se[-1]else 0; sp=se[-2]if len(se)>1 and se[-2]else 0
            ok=(mp<sp and mc>sk)or(mp>sp and mc<sk)
            sigs.append({"name":"MACD","passed":ok,"detail":f"MACD={mc:.0f}/Signal={sk:.0f}"})
            if ok: sc+=1
        else: sigs.append({"name":"MACD","passed":False,"detail":"N/A"})

        ro=rv<30 or rv>70
        sigs.append({"name":"RSI","passed":ro,"detail":f"RSI={rv:.1f}"})
        if ro: sc+=1

        rv5=sum(k["volume"]for k in k15d[-5:])/5
        bv5=sum(k["volume"]for k in k15d[-25:-5])/20 if len(k15d)>=25 else 1
        vr=rv5/bv5 if bv5>0 else 1
        vo=vr>=1.5
        sigs.append({"name":"成交量","passed":vo,"detail":f"量比={vr:.1f}x"})
        if vo: sc+=1

        bids=ob.get("bids",[]); asks=ob.get("asks",[])
        bvv=sum(float(b[1])for b in bids[:10]); avv=sum(float(a[1])for a in asks[:10])
        obr=bvv/avv if avv>0 else 1
        obo=obr>1.2 or (obr>0 and 1/obr>1.2)
        obl="买盘占优" if obr>1.2 else ("卖盘占优" if obr>0 and 1/obr>1.2 else "均衡")
        sigs.append({"name":"订单簿","passed":obo,"detail":f"{obl} ({obr:.2f})"})
        if obo: sc+=1

        ob_bids=[{"price":float(b[0]),"cum_qty":sum(float(bb[1])for bb in bids[:i+1])} for i,b in enumerate(bids[:15])]
        ob_asks=[{"price":float(a[0]),"cum_qty":sum(float(aa[1])for aa in asks[:i+1])} for i,a in enumerate(asks[:15])]

        # Sentiment
        st=0; si=[]
        if fr<-0.00005: st+=1; si.append({"name":"资金费率","val":f"{fr*100:.4f}%","score":1,"note":"偏多"})
        elif fr>0.0001: st-=1; si.append({"name":"资金费率","val":f"{fr*100:.4f}%","score":-1,"note":"偏空"})
        else: si.append({"name":"资金费率","val":f"{fr*100:.4f}%","score":0,"note":"正常"})
        si.append({"name":"多空比","val":"N/A","score":0,"note":"N/A"})
        if fg:
            if fg<25: st+=1; si.append({"name":"恐惧贪婪","val":str(fg),"score":1,"note":"极度恐惧"})
            elif fg>75: st-=1; si.append({"name":"恐惧贪婪","val":str(fg),"score":-1,"note":"极度贪婪"})
            else: si.append({"name":"恐惧贪婪","val":str(fg),"score":0,"note":"中性"})
        else: si.append({"name":"恐惧贪婪","val":"N/A","score":0,"note":"N/A"})
        si.append({"name":"社交媒体","val":"N/A","score":0,"note":"N/A"})
        slb="偏多" if st>=2 else ("偏空" if st<=-2 else "中性")

        # Risk
        direction="long" if trend=="bullish" else ("short" if trend=="bearish" else "wait")
        scf=(st>=2 and direction=="short")or(st<=-2 and direction=="long")
        now=datetime.now(timezone.utc)
        wk=now.weekday()>=5
        lv=rv5<50
        sm=3; am=1.8; pp=0.01
        slp=0; tp=0
        if direction=="long" and av>0: slp=price-av*am; tp=price+av*am
        elif direction=="short" and av>0: slp=price+av*am; tp=price-av*am
        pf=1.0
        if scf: pf*=0.5
        fp=pp*pf
        can=sc>=sm and trend!="sideways" and not wk and not lv and not scf
        blk=[]
        if sc<sm: blk.append(f"信号不足({sc}/{sm})")
        if trend=="sideways": blk.append("4H方向不明")
        if wk: blk.append("周末降级")
        if lv: blk.append("低流动性")
        if scf: blk.append("情绪冲突")

        return {
            "ok": True,
            "ts": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "price":price,"change24":chg,"high24":h24,"low24":l24,"vol24":v24,
            "chart_15m":[{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k15d[-80:]],
            "chart_1h":[{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k1hd[-36:]],
            "chart_4h":[{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k4hd[-36:]],
            "trend_4h":trend,"rsi":round(rv,1),"atr":round(av,1),
            "supports":sups,"resistances":res,
            "signals":sigs,"sig_count":sc,"sig_required":sm,
            "sent_score":st,"sent_label":slb,"sent_items":si,
            "fear_greed":fg,"funding_rate":fr,
            "ob_ratio":round(obr,4),"ob_label":obl,"ob_bids":ob_bids,"ob_asks":ob_asks,
            "spread":round((float(asks[0][0])-float(bids[0][0]))/float(bids[0][0])*100,4) if bids and asks else 0,
            "direction":direction,"entry":price,"stop_loss":round(slp,1),"take_profit_1":round(tp,1),
            "position_pct":round(fp*100,2),"risk_pct":round(abs(price-slp)/price*100,2) if slp else 0,
            "can_trade":can,"blocks":blk,"ema_20":round(e20[-1],1)if e20[-1]else 0,"ema_50":round(e50[-1],1)if e50[-1]else 0,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

def refresh_cache():
    global _latest
    try:
        data = analyze()
        with _lock: _latest = data
    except Exception as e:
        print(f"Cache refresh error: {e}")

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/analysis")
def api():
    global _latest
    if _latest is None or time.time() - getattr(_latest,"_ts",0) > 30:
        refresh_cache()
    with _lock:
        if _latest is None: return jsonify({"error":"no data"}), 503
        return jsonify(_latest)

if __name__ == "__main__":
    refresh_cache()
    app.run(host="0.0.0.0", port=5000, debug=False)
