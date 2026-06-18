"""
Flask web server for BTC Trading Dashboard
Run: python app.py  ->  http://localhost:5000
"""
import json, os, sys, logging, threading, time
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Global cache
_latest = None
_last_update = 0
_lock = threading.Lock()

def run_analysis():
    """Full analysis - same logic as standalone script"""
    from urllib.request import Request, urlopen
    
    def fetch(url, timeout=15):
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    
    def ema(values, period):
        result = [None] * len(values)
        if len(values) < period: return result
        k = 2.0/(period+1)
        avg = sum(values[:period])/period
        result[period-1] = avg
        for i in range(period, len(values)):
            result[i] = (values[i]-result[i-1])*k + result[i-1]
        return result
    
    def rsi_calc(closes, period=14):
        result = [None] * len(closes)
        if len(closes) < period+1: return result
        gains = losses = 0.0
        for i in range(1, period+1):
            d = closes[i]-closes[i-1]
            if d>0: gains+=d
            else: losses-=d
        avg_gain = gains/period
        avg_loss = losses/period
        if avg_loss==0: avg_loss=0.0001
        result[period] = 100-100/(1+avg_gain/avg_loss)
        for i in range(period+1, len(closes)):
            d = closes[i]-closes[i-1]
            avg_gain = (avg_gain*(period-1)+(d if d>0 else 0))/period
            avg_loss = (avg_loss*(period-1)+(-d if d<0 else 0))/period
            if avg_loss==0: avg_loss=0.0001
            result[i] = 100-100/(1+avg_gain/avg_loss)
        return result
    
    def atr_calc(highs, lows, closes, period=14):
        n = len(highs)
        trs = [0]*n
        result = [None]*n
        for i in range(n):
            if i==0: trs[i]=highs[i]-lows[i]
            else: trs[i]=max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        if n < period: return result
        avg = sum(trs[:period])/period
        result[period-1] = avg
        for i in range(period, n):
            result[i] = (result[i-1]*(period-1)+trs[i])/period
        return result
    
    # Fetch all data
    k15_raw = fetch("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=15m&limit=200")["data"]
    k15_raw.reverse()
    k1h_raw = fetch("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1H&limit=100")["data"]
    k1h_raw.reverse()
    k4h_raw = fetch("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=4H&limit=100")["data"]
    k4h_raw.reverse()
    ob_data = fetch("https://www.okx.com/api/v5/market/books?instId=BTC-USDT&sz=20")["data"][0]
    ticker = fetch("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]
    try:
        fr = float(fetch("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP")["data"][0]["fundingRate"])
    except:
        fr = 0
    try:
        fg = int(fetch("https://api.alternative.me/fng/?limit=1")["data"][0]["value"])
    except:
        fg = None
    
    def parse(raw):
        return [{"time":int(k[0]),"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"volume":float(k[5])} for k in raw]
    
    k15 = parse(k15_raw); k1h = parse(k1h_raw); k4h = parse(k4h_raw)
    price = float(ticker["last"])
    high24 = float(ticker["high24h"]); low24 = float(ticker["low24h"]); vol24 = float(ticker["vol24h"])
    change24 = round((price/float(ticker["open24h"])-1)*100,2)
    
    # Charts
    chart_15m = [{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k15[-80:]]
    chart_1h = [{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k1h[-36:]]
    chart_4h = [{"t":k["time"],"o":k["open"],"h":k["high"],"l":k["low"],"c":k["close"],"v":k["volume"]} for k in k4h[-36:]]
    
    # Technical
    closes_4h = [k["close"] for k in k4h]
    closes_15 = [k["close"] for k in k15]
    highs_15 = [k["high"] for k in k15]; lows_15 = [k["low"] for k in k15]
    e20 = ema(closes_4h, 20); e50 = ema(closes_4h, 50)
    trend_4h = "sideways"
    if e20[-1] and e50[-1]:
        if e20[-1] > e50[-1]: trend_4h = "bullish"
        elif e20[-1] < e50[-1]: trend_4h = "bearish"
    
    rsi_v = rsi_calc(closes_15)[-1] or 0
    atr_v = atr_calc(highs_15, lows_15, closes_15)[-1] or 0
    
    # SR levels
    h1 = [k["high"] for k in k1h]; l1 = [k["low"] for k in k1h]
    rs = []; ss = []
    for i in range(2,len(k1h)-2):
        if h1[i]>h1[i-1] and h1[i]>h1[i-2] and h1[i]>h1[i+1] and h1[i]>h1[i+2]: rs.append(h1[i])
        if l1[i]<l1[i-1] and l1[i]<l1[i-2] and l1[i]<l1[i+1] and l1[i]<l1[i+2]: ss.append(l1[i])
    def cluster(pts, cur):
        if not pts: return []
        sl = sorted(pts); groups = [[sl[0]]]
        for v in sl[1:]:
            if abs(v-groups[-1][0])/groups[-1][0]<0.002: groups[-1].append(v)
            else: groups.append([v])
        return [(round(sum(g)/len(g),1),len(g)) for g in groups if abs(sum(g)/len(g)-cur)/cur<0.05]
    supports = [p for p,_ in sorted(cluster(ss,price))[:3]]
    resistances = [p for p,_ in sorted(cluster(rs,price),reverse=True)[:3]]
    
    # Signals
    def pat(k):
        o,h,l,c = k["open"],k["high"],k["low"],k["close"]
        b=abs(c-o)
        if b==0 or h==l: return None
        u=h-max(c,o); lw=min(c,o)-l
        if lw>=2*b and u<=b*0.3: return "hammer"
        if u>=2*b and lw<=b*0.3: return "shooting_star"
        return None
    def eng(pr,cu):
        po,pc=pr["open"],pr["close"]; co,cc=cu["open"],cu["close"]
        pb=abs(pc-po); cb=abs(cc-co)
        if pb==0 or cb==0: return None
        if pc<po and cc>co and co<=pc and cc>=po: return "bull_engulf"
        if pc>po and cc<co and co>=pc and cc<=po: return "bear_engulf"
        return None
    
    sigs = []; sc = 0
    ptn = pat(k15[-1]) or eng(k15[-2],k15[-1])
    sigs.append({"name":"K线形态","passed":bool(ptn),"detail":ptn or "无"})
    if ptn: sc+=1
    
    ef = ema(closes_15,12); es = ema(closes_15,26)
    ml=[None]*len(closes_15)
    for i in range(len(closes_15)):
        if ef[i] and es[i]: ml[i]=ef[i]-es[i]
    vm=[v for v in ml if v is not None]
    if len(vm)>=9:
        se=ema(vm,9)
        mc=ml[-1]or 0; mp=ml[-2]or 0
        sk=se[-1]if se[-1]else 0; sp=se[-2]if len(se)>1 and se[-2]else 0
        ok=(mp<sp and mc>sk)or(mp>sp and mc<sk)
        sigs.append({"name":"MACD","passed":ok,"detail":f"MACD={mc:.0f}/Signal={sk:.0f}"})
        if ok: sc+=1
    else:
        sigs.append({"name":"MACD","passed":False,"detail":"N/A"})
    
    ro = rsi_v<30 or rsi_v>70
    sigs.append({"name":"RSI","passed":ro,"detail":f"RSI={rsi_v:.1f}"})
    if ro: sc+=1
    
    rv = sum(k["volume"]for k in k15[-5:])/5
    bv = sum(k["volume"]for k in k15[-25:-5])/20 if len(k15)>=25 else 1
    vr = rv/bv if bv>0 else 1
    vo = vr>=1.5
    sigs.append({"name":"成交量","passed":vo,"detail":f"量比={vr:.1f}x"})
    if vo: sc+=1
    
    bids=ob_data.get("bids",[]); asks=ob_data.get("asks",[])
    bvv=sum(float(b[1])for b in bids[:10])
    avv=sum(float(a[1])for a in asks[:10])
    obr=bvv/avv if avv>0 else 1
    obo=obr>1.2 or (obr>0 and 1/obr>1.2)
    obl="买盘占优" if obr>1.2 else ("卖盘占优" if obr>0 and 1/obr>1.2 else "均衡")
    sigs.append({"name":"订单簿","passed":obo,"detail":f"{obl} ({obr:.2f})"})
    if obo: sc+=1
    
    # Depth
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
    else:
        si.append({"name":"恐惧贪婪","val":"N/A","score":0,"note":"N/A"})
    si.append({"name":"社交媒体","val":"N/A","score":0,"note":"N/A"})
    slb="偏多" if st>=2 else ("偏空" if st<=-2 else "中性")
    
    # Risk
    direction="long" if trend_4h=="bullish" else ("short" if trend_4h=="bearish" else "wait")
    sc_conflict=(st>=2 and direction=="short")or(st<=-2 and direction=="long")
    now=datetime.now(timezone.utc)
    is_wk=now.weekday()>=5
    lv=rv<50
    sm=3; atrm=1.8; ppct=0.01
    sl=0; tp1=0
    if direction=="long" and atr_v>0:
        sl=price-atr_v*atrm; tp1=price+atr_v*atrm
    elif direction=="short" and atr_v>0:
        sl=price+atr_v*atrm; tp1=price-atr_v*atrm
    pf=1.0
    if sc_conflict: pf*=0.5
    fp=ppct*pf
    can=sc>=sm and trend_4h!="sideways" and not is_wk and not lv and not sc_conflict
    blk=[]
    if sc<sm: blk.append(f"信号不足({sc}/{sm})")
    if trend_4h=="sideways": blk.append("4H方向不明")
    if is_wk: blk.append("周末降级")
    if lv: blk.append(f"低流动性")
    if sc_conflict: blk.append(f"情绪冲突")
    
    result = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "price": price, "change24": change24, "high24": high24, "low24": low24, "vol24": vol24,
        "chart_15m": chart_15m, "chart_1h": chart_1h, "chart_4h": chart_4h,
        "trend_4h": trend_4h, "rsi": round(rsi_v,1), "atr": round(atr_v,1),
        "supports": supports, "resistances": resistances,
        "signals": sigs, "sig_count": sc, "sig_required": sm,
        "sent_score": st, "sent_label": slb, "sent_items": si,
        "fear_greed": fg, "funding_rate": fr,
        "ob_ratio": round(obr,4), "ob_label": obl,
        "ob_bids": ob_bids, "ob_asks": ob_asks,
        "spread": round((float(asks[0][0])-float(bids[0][0]))/float(bids[0][0])*100,4) if bids and asks else 0,
        "direction": direction, "entry": price,
        "stop_loss": round(sl,1), "take_profit_1": round(tp1,1),
        "position_pct": round(fp*100,2), "risk_pct": round(abs(price-sl)/price*100,2) if sl else 0,
        "can_trade": can, "blocks": blk,
        "ema_20": round(e20[-1],1) if e20[-1] else 0,
        "ema_50": round(e50[-1],1) if e50[-1] else 0,
    }
    return result

def update_cache():
    global _latest, _last_update
    try:
        data = run_analysis()
        with _lock:
            _latest = data
            _last_update = time.time()
    except Exception as e:
        print(f"Update failed: {e}")

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/analysis")
def api_analysis():
    global _latest, _last_update
    # Refresh if older than 30s
    if _latest is None or time.time() - _last_update > 30:
        update_cache()
    with _lock:
        if _latest is None:
            return jsonify({"error": "no data"}), 503
        return jsonify(_latest)

# Initial load
update_cache()

if __name__ == "__main__":
    update_cache()
    app.run(host="0.0.0.0", port=5000, debug=False)
