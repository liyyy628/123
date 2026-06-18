
import logging
from datetime import datetime
from typing import Dict, List, Optional

import data as d
import indicators as ind
from sentiment import score_sentiment, check_high_impact_events, check_news_volatility, check_onchain
from orderbook import analyze_order_book, analyze_taker_volumes, RiskManager
from config import MODE_CONFIGS, DEFAULT_MODE, EMA_4H, EMA_1H, WEEKEND_DEGRADE, LOW_VOLUME_DEGRADE, MIN_15M_VOLUME_BTC, Mode, RSI_OVERSOLD, RSI_OVERBOUGHT

logger = logging.getLogger(__name__)


class BTCAnalyzer:
    def __init__(self, mode: Mode = DEFAULT_MODE):
        self.mode = mode
        self.mode_config = MODE_CONFIGS[mode]
        self.raw_data: Dict[str, List] = {}
        self.parsed: Dict[str, List[Dict]] = {}
        self.current_price = 0.0
        self.results: Dict = {}

    def fetch_all_data(self) -> None:
        logger.info("\U0001f310 \u6b63\u5728\u83b7\u53d6\u5e02\u573a\u6570\u636e...")
        self.raw_data = d.fetch_multi_tf_klines()
        for tf in ["4h", "1h", "15m"]:
            if tf in self.raw_data:
                self.parsed[tf] = d.parse_klines_to_dicts(self.raw_data[tf])
        self.current_price = self.parsed["15m"][-1]["close"] if self.parsed.get("15m") else 0
        logger.info(f"\u2705 \u6570\u636e\u83b7\u53d6\u5b8c\u6210\uff0c\u5f53\u524dBTC\u4ef7\u683c: {self.current_price:.2f}")

    def analyze_technical(self) -> Dict:
        logger.info("\U0001f4ca \u6280\u672f\u9762\u5206\u6790\u4e2d...")
        result = {"4h": {}, "1h": {}, "15m": {}, "support_resistance": {}, "overall_trend": "", "preferred_direction": "wait"}
        if "4h" in self.parsed and self.parsed["4h"]:
            trend_4h = ind.determine_trend(self.parsed["4h"], EMA_4H)
            result["4h"] = {"trend": trend_4h["trend"], "description": trend_4h["description"]}
        if "1h" in self.parsed and self.parsed["1h"]:
            trend_1h = ind.determine_trend(self.parsed["1h"], EMA_1H)
            sr = ind.find_support_resistance(self.parsed["1h"], 50)
            result["1h"] = {"trend": trend_1h["trend"], "description": trend_1h["description"],
                            "support": sr["support"], "resistance": sr["resistance"]}
            result["support_resistance"] = sr
        if "15m" in self.parsed and self.parsed["15m"]:
            k15 = self.parsed["15m"]
            closes = [k["close"] for k in k15]
            highs = [k["high"] for k in k15]
            lows = [k["low"] for k in k15]
            rsi_val = ind.rsi_current(closes)
            atr_val = ind.atr_current(highs, lows, closes)
            vol = ind.analyze_volume(k15)
            result["15m"] = {"current_price": self.current_price, "rsi": rsi_val, "atr": atr_val, "volume": vol}
        if result["4h"].get("trend") == "bullish" and result["1h"].get("trend") != "bearish":
            result["overall_trend"] = "\u591a\u5934 \U0001f4c8"
            result["preferred_direction"] = "long"
        elif result["4h"].get("trend") == "bearish" and result["1h"].get("trend") != "bullish":
            result["overall_trend"] = "\u7a7a\u5934 \U0001f4c9"
            result["preferred_direction"] = "short"
        else:
            result["overall_trend"] = "\u9707\u8361 \u2696\ufe0f"
            result["preferred_direction"] = "wait"
        return result

    def analyze_15m_signals(self) -> Dict:
        logger.info("\U0001f50d \u68c0\u6d4b15\u5206\u949f\u5165\u573a\u4fe1\u53f7...")
        result = {"signals_found": 0, "total_checked": 0, "signals": [], "has_entry": False}
        k15 = self.parsed.get("15m", [])
        if len(k15) < 30:
            result["error"] = "15\u5206\u949f\u6570\u636e\u4e0d\u8db3"
            return result
        closes = [k["close"] for k in k15]
        highs = [k["high"] for k in k15]
        lows = [k["low"] for k in k15]
        last = k15[-1]
        prev = k15[-2]
        # a) K\u7ebf\u5f62\u6001
        pattern = ind.detect_candlestick_pattern(last)
        engulfing = ind.detect_engulfing(prev, last)
        if pattern or engulfing:
            detail = f"{pattern or engulfing} @ {last['close']:.1f}"
            result["signals"].append({"name": "K\u7ebf\u5f62\u6001", "found": True, "detail": detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "K\u7ebf\u5f62\u6001", "found": False, "detail": "\u65e0\u663e\u8457\u53cd\u8f6c\u5f62\u6001"})
        result["total_checked"] += 1
        # b) MACD
        macd_line, sig_line, hist = ind.macd(closes)
        def safe_get(arr, idx):
            return arr[idx] if idx < len(arr) and arr[idx] is not None else 0
        m_curr = safe_get(macd_line, -1)
        s_curr = safe_get(sig_line, -1)
        m_prev = safe_get(macd_line, -2)
        s_prev = safe_get(sig_line, -2)
        h_curr = safe_get(hist, -1)
        h_prev = safe_get(hist, -2)
        macd_signal = False
        macd_detail = ""
        if m_prev < s_prev and m_curr > s_curr:
            macd_signal = True
            macd_detail = "MACD\u91d1\u53c9 \u2713"
        elif m_prev > s_prev and m_curr < s_curr:
            macd_signal = True
            macd_detail = "MACD\u6b7b\u53c9 \u2713"
        elif h_curr > 0 and h_prev < 0:
            macd_signal = True
            macd_detail = "MACD\u67f1\u72b6\u7ebf\u7ffb\u6b63 \u2713"
        elif h_curr < 0 and h_prev > 0:
            macd_signal = True
            macd_detail = "MACD\u67f1\u72b6\u7ebf\u7ffb\u8d1f \u2713"
        if macd_signal:
            result["signals"].append({"name": "MACD", "found": True, "detail": macd_detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "MACD", "found": False, "detail": f"MACD={m_curr:.0f}, Signal={s_curr:.0f}, Hist={h_curr:.0f}"})
        result["total_checked"] += 1
        # c) RSI
        rsi_val = ind.rsi_current(closes)
        rsi_signal = False
        rsi_detail = f"RSI={rsi_val:.1f}" if rsi_val else "RSI=N/A"
        if rsi_val is not None:
            if rsi_val < RSI_OVERSOLD:
                rsi_signal = True
                rsi_detail += " \u8d85\u5356\u533a\u62d0\u5934 \u2713"
            elif rsi_val > RSI_OVERBOUGHT:
                rsi_signal = True
                rsi_detail += " \u8d85\u4e70\u533a\u62d0\u5934 \u2713"
            rsi_vals = ind.rsi(closes)
            if len(rsi_vals) >= 3 and all(v is not None for v in rsi_vals[-3:]):
                p2, p1, cur = rsi_vals[-3], rsi_vals[-2], rsi_val
                if p2 > p1 and p1 < cur and cur < RSI_OVERSOLD + 10:
                    rsi_signal = True
                    rsi_detail += " (RSI\u5e95\u80cc\u79bb\u53cd\u5f39 \u2713)"
                elif p2 < p1 and p1 > cur and cur > RSI_OVERBOUGHT - 10:
                    rsi_signal = True
                    rsi_detail += " (RSI\u9876\u80cc\u79bb\u56de\u843d \u2713)"
        if rsi_signal:
            result["signals"].append({"name": "RSI", "found": True, "detail": rsi_detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "RSI", "found": False, "detail": rsi_detail})
        result["total_checked"] += 1
        # d) \u6210\u4ea4\u91cf
        vol = ind.analyze_volume(k15)
        if vol["surge"]:
            result["signals"].append({"name": "\u6210\u4ea4\u91cf", "found": True, "detail": f"\u653e\u91cf{vol['ratio']}x\uff08\u57fa\u7ebf{vol['avg_volume']:.0f} \u2192 \u8fd1\u671f{vol['recent_volume']:.0f}\uff09\u2713"})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "\u6210\u4ea4\u91cf", "found": False, "detail": f"\u91cf\u6bd4{vol['ratio']}x\uff08\u57fa\u7ebf{vol['avg_volume']:.0f} \u2192 \u8fd1\u671f{vol['recent_volume']:.0f}\uff09"})
        result["total_checked"] += 1
        # e) \u8ba2\u5355\u7c3f - \u5728\u5916\u90e8\u5206\u6790
        result["signals"].append({"name": "\u8ba2\u5355\u7c3f", "found": False, "detail": "\u8be6\u89c1\u3010\u8ba2\u5355\u7c3f\u6d41\u52a8\u6027\u5206\u6790\u3011"})
        result["has_entry"] = result["signals_found"] >= self.mode_config.min_signals
        result["required"] = self.mode_config.min_signals
        return result

    def analyze(self) -> Dict:
        logger.info(f"\n{'='*60}")
        logger.info(f"\U0001f680 BTC/USDT \u91cf\u5316\u4ea4\u6613\u5206\u6790 | \u6a21\u5f0f: {self.mode} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}\n")
        self.fetch_all_data()
        news = check_high_impact_events()
        tech = self.analyze_technical()
        signals_15m = self.analyze_15m_signals()
        sentiment = score_sentiment(self.current_price)
        news_vol = check_news_volatility(self.parsed.get("15m", []))
        onchain = check_onchain()
        ob = analyze_order_book()
        taker = analyze_taker_volumes()
        atr_val = tech.get("15m", {}).get("atr", 0)
        direction = tech["preferred_direction"]
        sentiment_conflict = (sentiment["total"] >= 2 and direction == "short") or (sentiment["total"] <= -2 and direction == "long")
        chain_pressure = "\u629b\u538b" in onchain["status"]
        rm = RiskManager(self.mode, atr_val, self.current_price) if atr_val > 0 else None
        risk_result = {}
        if rm and direction != "wait":
            risk_result = rm.summary(direction, self.current_price, sentiment_conflict, chain_pressure)
        now = datetime.utcnow()
        is_weekend = now.weekday() >= 5
        is_low_volume = tech.get("15m", {}).get("volume", {}).get("recent_volume", 0) < MIN_15M_VOLUME_BTC
        signal_grade = "\u6b63\u5e38"
        if is_weekend and WEEKEND_DEGRADE:
            signal_grade = "\u964d\u7ea7\uff08\u5468\u672b\uff09"
        elif is_low_volume and LOW_VOLUME_DEGRADE:
            signal_grade = "\u964d\u7ea7\uff08\u4f4e\u6d41\u52a8\u6027\uff09"
        if sentiment_conflict:
            signal_grade = f"\u964d\u7ea7\uff08\u60c5\u7eea\u51b2\u7a81\uff1a\u60c5\u7eea{sentiment['label']} vs \u65b9\u5411{direction}\uff09"
        can_trade = signals_15m["has_entry"] and not news["has_event"] and direction != "wait" and not is_weekend and not is_low_volume and not sentiment_conflict
        self.results = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": self.mode,
                        "price": self.current_price, "technical": tech, "signals_15m": signals_15m,
                        "sentiment": sentiment, "news": news, "news_volatility": news_vol,
                        "onchain": onchain, "orderbook": ob, "taker": taker, "risk": risk_result,
                        "signal_grade": signal_grade, "can_trade": can_trade}
        return self.results

    def generate_report(self) -> str:
        r = self.results
        mode_label = "\u6fc0\u8fdb" if self.mode == "aggressive" else "\u7a33\u5065"
        tech = r.get("technical", {})
        signals = r.get("signals_15m", {})
        sentiment = r.get("sentiment", {})
        news = r.get("news", {})
        onchain = r.get("onchain", {})
        ob = r.get("orderbook", {})
        taker = r.get("taker", {})
        risk = r.get("risk", {})
        news_vol = r.get("news_volatility", {})
        sr = tech.get("support_resistance", {})
        supports = sr.get("support", [])
        resistances = sr.get("resistance", [])
        lines = []
        lines.append("---")
        lines.append(f"**\u4ea4\u6613\u6a21\u5f0f**\uff1a{mode_label}")
        lines.append(f"**\u591a\u5468\u671f\u65b9\u5411**\uff1a{tech.get('overall_trend', 'N/A')}")
        lines.append(f"  - 4H: {tech.get('4h', {}).get('description', 'N/A')}")
        lines.append(f"  - 1H: {tech.get('1h', {}).get('description', 'N/A')}")
        lines.append(f"**\u5173\u952e\u4ef7\u4f4d**\uff1a")
        lines.append(f"  - \u652f\u6491: {' / '.join(f'{s:.1f}' for s in supports[:3]) if supports else 'N/A'}")
        lines.append(f"  - \u963b\u529b: {' / '.join(f'{r:.1f}' for r in resistances[:3]) if resistances else 'N/A'}")
        lines.append(f"**\u5f53\u524d\u4ef7\u683c**\uff1a{r.get('price', 0):,.2f}")
        lines.append(f"")
        lines.append(f"**\u60c5\u7eea\u8bc4\u5206**\uff1a{sentiment.get('total', 0)}\u5206 {sentiment.get('label', 'N/A')}")
        for d in sentiment.get("details", []):
            lines.append(f"  - {d.get('indicator')}: {d.get('value')} ({d.get('note')})")
        lines.append(f"")
        lines.append(f"**\u65b0\u95fb\u72b6\u6001**\uff1a{'\u26a0\ufe0f ' + '; '.join(news.get('events', [])) if news.get('has_event') else '\u65e0\u91cd\u5927\u4e8b\u4ef6 \u2705'}")
        if news.get("has_event"):
            lines.append(f"  > {news.get('warning')}")
        lines.append(f"**\u65b0\u95fb\u6ce2\u52a8**\uff1a{news_vol.get('note', 'N/A')} (\u6700\u5927\u6ce2\u52a8 {news_vol.get('max_move_pct', 0)}%)")
        lines.append(f"")
        lines.append(f"**\u94fe\u4e0a\u6570\u636e**\uff1a{onchain.get('status', 'N/A')}")
        for d in onchain.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"")
        lines.append(f"**\u8ba2\u5355\u7c3f**\uff1a{ob.get('imbalance', 'N/A')}")
        for d in ob.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"**\u5403\u5355\u5206\u6790**\uff1a{'\u26a0\ufe0f \u6050\u614c\u4fe1\u53f7!' if taker.get('panic') else '\u6b63\u5e38'}")
        for d in taker.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"")
        lines.append(f"**15\u5206\u949f\u4fe1\u53f7**\uff08\u9700\u8981\u2265{signals.get('required', 3)}\u4e2a\uff09\uff1a")
        for s in signals.get("signals", []):
            mark = "\u2705" if s.get("found") else "\u274c"
            lines.append(f"  {mark} {s['name']}: {s['detail']}")
        lines.append(f"  \u603b\u8ba1: {signals.get('signals_found', 0)}/{signals.get('total_checked', 5)}")
        lines.append(f"")
        lines.append(f"**\u4fe1\u53f7\u7b49\u7ea7**\uff1a{r.get('signal_grade', 'N/A')}")
        lines.append(f"")
        lines.append(f"**\u64cd\u4f5c\u5efa\u8bae**\uff1a")
        if r.get("can_trade"):
            lines.append(f"  - \u65b9\u5411\uff1a{risk.get('direction', 'N/A')}")
            lines.append(f"  - \u5165\u573a\u533a\u95f4\uff1a{risk.get('entry', 0):,.1f}")
            lines.append(f"  - \u6b62\u635f\uff1a{risk.get('stop_loss', 0):,.1f}")
            lines.append(f"  - \u6b62\u76c81\uff1a{risk.get('take_profit_1', 0):,.1f}")
            lines.append(f"  - \u6b62\u76c82\uff08\u79fb\u52a8\u6b62\u635f\uff09\uff1a{risk.get('take_profit_2_params', {}).get('description', 'N/A')}")
            lines.append(f"  - \u4ed3\u4f4d\uff1a{risk.get('position', {}).get('final_pct', 0)*100:.2f}% = {risk.get('position', {}).get('position_value', 0):,.2f} USDT ({risk.get('position', {}).get('position_btc', 0):.6f} BTC)")
            lines.append(f"  - \u4ed3\u4f4d\u7cfb\u6570\u660e\u7ec6\uff1a{risk.get('position', {}).get('factor_breakdown', 'N/A')}")
            lines.append(f"  - \u672c\u5355\u98ce\u9669\uff1a{risk.get('risk_per_trade', 0):.2f}% | \u76c8\u4e8f\u6bd4\uff1a{risk.get('reward_risk', 0):.2f}:1")
        else:
            lines.append(f"  - \u5f53\u524d\u4e0d\u6ee1\u8db3\u5165\u573a\u6761\u4ef6\uff0c\u5efa\u8bae\u89c2\u671b \U0001f440")
            reasons = []
            if not signals.get("has_entry"):
                reasons.append(f"15\u5206\u949f\u4fe1\u53f7\u4e0d\u8db3\uff08{signals.get('signals_found', 0)}/{signals.get('required', 3)}\uff09")
            if news.get("has_event"):
                reasons.append("\u5b8f\u89c2\u4e8b\u4ef6\u7a97\u53e3\u671f")
            if tech.get("preferred_direction") == "wait":
                reasons.append("\u591a\u5468\u671f\u65b9\u5411\u4e0d\u660e\u786e")
            if r.get("signal_grade") != "\u6b63\u5e38":
                reasons.append(f"\u4fe1\u53f7\u964d\u7ea7\uff08{r.get('signal_grade')}\uff09")
            for reason in reasons:
                lines.append(f"  - \u274c {reason}")
        lines.append(f"")
        lines.append(f"**\u98ce\u9669\u63d0\u793a**\uff1a")
        lines.append(f"  - \u6570\u636e\u6e90\u4e3aBinance\u73b0\u8d27API\uff0c\u4e0d\u542bU\u672c\u4f4d\u5408\u7ea6\u6df1\u5ea6")
        lines.append(f"  - \u60c5\u7eea\u6570\u636e\u4e2d\u793e\u4ea4\u5a92\u4f53\u7ef4\u5ea6\u4e3a\u5360\u4f4d\u72b6\u6001")
        lines.append(f"  - \u94fe\u4e0a\u6570\u636e\u9700CryptoQuant/Glassnode API\u5b8c\u5584")
        lines.append(f"  - 15\u5206\u949f\u7ea7\u522b\u4fe1\u53f7\u566a\u97f3\u8f83\u9ad8\uff0c\u5efa\u8bae\u4ee54H\u65b9\u5411\u4e3a\u4e3b")
        lines.append(f"")
        tf_dir = tech.get('4h', {}).get('trend', '?')
        sg_count = signals.get('signals_found', 0)
        sg_req = signals.get('required', 3)
        emo = sentiment.get('label', '?')
        lines.append(f"**\u672c\u5355\u903b\u8f91\u6458\u8981**\uff1a")
        lines.append(f"  4H\u65b9\u5411={tf_dir} | 15M\u4fe1\u53f7={sg_count}/{sg_req} | \u60c5\u7eea={emo} | \u4fe1\u53f7\u7b49\u7ea7={r.get('signal_grade', 'N/A')}")
        lines.append(f"  {'\u2705 \u53ef\u5f00\u5355' if r.get('can_trade') else '\u274c \u89c2\u671b'}")
        lines.append(f"")
        lines.append(f"**\u81ea\u6211\u8fdb\u5316\u5efa\u8bae**\uff1a")
        lines.append(f"  - \u5982\u679c\u8fde\u7eed\u51fa\u73b0\u4fe1\u53f7\u5145\u8db3\u4f46\u4e8f\u635f\u7684\u60c5\u51b5\uff0c\u8003\u8651\u63d0\u9ad8\u4fe1\u53f7\u9608\u503c\u81f34\u4e2a")
        lines.append(f"  - \u5982\u679c\u9891\u7e41\u88ab\u6b62\u635f\u626b\u6389\uff0c\u53ef\u589e\u5927ATR\u500d\u6570\uff08\u6fc0\u8fdb\u21921.5\uff0c\u7a33\u5065\u21922.0\uff09")
        lines.append(f"  - \u5982\u679c\u65b9\u5411\u5224\u65ad\u53cd\u590d\u5207\u6362\uff0c\u5efa\u8bae\u57284H EMA\u4e0a\u589e\u52a0200EMA\u8fc7\u6ee4")
        lines.append("---")
        return "\n".join(lines)
