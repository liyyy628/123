
import logging
from datetime import datetime
from typing import Dict, List, Optional

import data as d
import indicators as ind
from sentiment import score_sentiment, check_high_impact_events, check_news_volatility, check_onchain
from orderbook import analyze_order_book, analyze_taker_volumes, RiskManager
from prediction_market import get_prediction_market_signal
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
        logger.info("🌐 正在获取市场数据...")
        self.raw_data = d.fetch_multi_tf_klines()
        for tf in ["4h", "1h", "15m"]:
            if tf in self.raw_data:
                self.parsed[tf] = d.parse_klines_to_dicts(self.raw_data[tf])
        self.current_price = self.parsed["15m"][-1]["close"] if self.parsed.get("15m") else 0
        logger.info(f"✅ 数据获取完成，当前BTC价格: {self.current_price:.2f}")

    def analyze_technical(self) -> Dict:
        logger.info("📊 技术面分析中...")
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
            result["overall_trend"] = "多头 📈"
            result["preferred_direction"] = "long"
        elif result["4h"].get("trend") == "bearish" and result["1h"].get("trend") != "bullish":
            result["overall_trend"] = "空头 📉"
            result["preferred_direction"] = "short"
        else:
            result["overall_trend"] = "震荡 ⚖️"
            result["preferred_direction"] = "wait"
        return result

    def analyze_15m_signals(self) -> Dict:
        logger.info("🔍 检测15分钟入场信号...")
        result = {"signals_found": 0, "total_checked": 0, "signals": [], "has_entry": False}
        k15 = self.parsed.get("15m", [])
        if len(k15) < 30:
            result["error"] = "15分钟数据不足"
            return result
        closes = [k["close"] for k in k15]
        highs = [k["high"] for k in k15]
        lows = [k["low"] for k in k15]
        last = k15[-1]
        prev = k15[-2]
        # a) K线形态
        pattern = ind.detect_candlestick_pattern(last)
        engulfing = ind.detect_engulfing(prev, last)
        if pattern or engulfing:
            detail = f"{pattern or engulfing} @ {last['close']:.1f}"
            result["signals"].append({"name": "K线形态", "found": True, "detail": detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "K线形态", "found": False, "detail": "无显著反转形态"})
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
            macd_detail = "MACD金叉 ✓"
        elif m_prev > s_prev and m_curr < s_curr:
            macd_signal = True
            macd_detail = "MACD死叉 ✓"
        elif h_curr > 0 and h_prev < 0:
            macd_signal = True
            macd_detail = "MACD柱状线翻正 ✓"
        elif h_curr < 0 and h_prev > 0:
            macd_signal = True
            macd_detail = "MACD柱状线翻负 ✓"
        if macd_signal:
            result["signals"].append({"name": "MACD", "found": True, "detail": macd_detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "MACD", "found": False, "detail": f"MACD={m_curr:.0f}, Signal={s_curr:.0f}, Hist={h_curr:.0f}"})
        result["total_checked"] += 1
        # c) RSI (volatility-adaptive thresholds)
        rsi_val = ind.rsi_current(closes)
        # Calculate ATR for adaptive thresholds
        tr_vals = []
        for i in range(1, min(15, len(highs))):
            tr_vals.append(max(highs[-i] - lows[-i],
                               abs(highs[-i] - closes[-i-1]),
                               abs(lows[-i] - closes[-i-1])))
        atr_val_15m = sum(tr_vals) / len(tr_vals) if tr_vals else 0
        vol_pct_15m = atr_val_15m / closes[-1] * 100 if closes[-1] > 0 else 0
        if vol_pct_15m < 0.15:
            rsi_os, rsi_ob = 35, 65
        elif vol_pct_15m > 0.50:
            rsi_os, rsi_ob = 22, 78
        else:
            rsi_os, rsi_ob = RSI_OVERSOLD, RSI_OVERBOUGHT

        rsi_signal = False
        rsi_detail = f"RSI={rsi_val:.1f}" if rsi_val else "RSI=N/A"
        if rsi_val is not None:
            if rsi_val < rsi_os:
                rsi_signal = True
                rsi_detail += f" 超卖区拐头 ✓(阈{rsi_os})"
            elif rsi_val > rsi_ob:
                rsi_signal = True
                rsi_detail += f" 超买区拐头 ✓(阈{rsi_ob})"
            rsi_vals = ind.rsi(closes)
            if len(rsi_vals) >= 3 and all(v is not None for v in rsi_vals[-3:]):
                p2, p1, cur = rsi_vals[-3], rsi_vals[-2], rsi_val
                if p2 > p1 and p1 < cur and cur < rsi_os + 10:
                    rsi_signal = True
                    rsi_detail += " (RSI底背离反弹 ✓)"
                elif p2 < p1 and p1 > cur and cur > rsi_ob - 10:
                    rsi_signal = True
                    rsi_detail += " (RSI顶背离回落 ✓)"
        if rsi_signal:
            result["signals"].append({"name": "RSI", "found": True, "detail": rsi_detail})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "RSI", "found": False, "detail": rsi_detail})
        result["total_checked"] += 1
        # d) 成交量
        vol = ind.analyze_volume(k15)
        if vol["surge"]:
            result["signals"].append({"name": "成交量", "found": True, "detail": f"放量{vol['ratio']}x（基线{vol['avg_volume']:.0f} → 近期{vol['recent_volume']:.0f}）✓"})
            result["signals_found"] += 1
        else:
            result["signals"].append({"name": "成交量", "found": False, "detail": f"量比{vol['ratio']}x（基线{vol['avg_volume']:.0f} → 近期{vol['recent_volume']:.0f}）"})
        result["total_checked"] += 1
        # e) 订单簿 - 在外部分析
        result["signals"].append({"name": "订单簿", "found": False, "detail": "详见【订单簿流动性分析】"})
        result["has_entry"] = result["signals_found"] >= self.mode_config.min_signals
        result["required"] = self.mode_config.min_signals
        return result

    def analyze(self) -> Dict:
        logger.info(f"\n{'='*60}")
        logger.info(f"🚀 BTC/USDT 量化交易分析 | 模式: {self.mode} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*60}\n")
        self.fetch_all_data()
        news = check_high_impact_events()
        tech = self.analyze_technical()
        signals_15m = self.analyze_15m_signals()
        sentiment = score_sentiment(self.current_price)
        news_vol = check_news_volatility(self.parsed.get("15m", []))
        onchain = check_onchain()
        pred_market = get_prediction_market_signal()
        ob = analyze_order_book()
        taker = analyze_taker_volumes()
        atr_val = tech.get("15m", {}).get("atr", 0)
        direction = tech["preferred_direction"]
        sentiment_conflict = (sentiment["total"] >= 2 and direction == "short") or (sentiment["total"] <= -2 and direction == "long")
        chain_pressure = onchain.get("score", 0) < -1  # Negative on-chain score = bearish pressure
        rm = RiskManager(self.mode, atr_val, self.current_price) if atr_val > 0 else None
        risk_result = {}
        if rm and direction != "wait":
            risk_result = rm.summary(direction, self.current_price, sentiment_conflict, chain_pressure)
        now = datetime.utcnow()
        is_weekend = now.weekday() >= 5
        is_low_volume = tech.get("15m", {}).get("volume", {}).get("recent_volume", 0) < MIN_15M_VOLUME_BTC
        signal_grade = "正常"
        if is_weekend and WEEKEND_DEGRADE:
            signal_grade = "降级（周末）"
        elif is_low_volume and LOW_VOLUME_DEGRADE:
            signal_grade = "降级（低流动性）"
        if sentiment_conflict:
            signal_grade = f"降级（情绪冲突：情绪{sentiment['label']} vs 方向{direction}）"
        onchain_bearish = onchain.get("score", 0) <= -3
        can_trade = (signals_15m["has_entry"] and not news["has_event"] and direction != "wait"
                     and not is_weekend and not is_low_volume and not sentiment_conflict
                     and not onchain_bearish)
        self.results = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "mode": self.mode,
                        "price": self.current_price, "technical": tech, "signals_15m": signals_15m,
                        "sentiment": sentiment, "news": news, "news_volatility": news_vol,
                        "onchain": onchain, "prediction_market": pred_market,
                        "orderbook": ob, "taker": taker, "risk": risk_result,
                        "signal_grade": signal_grade, "can_trade": can_trade}
        return self.results

    def generate_report(self) -> str:
        r = self.results
        mode_label = "激进" if self.mode == "aggressive" else "稳健"
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
        lines.append(f"**交易模式**：{mode_label}")
        lines.append(f"**多周期方向**：{tech.get('overall_trend', 'N/A')}")
        lines.append(f"  - 4H: {tech.get('4h', {}).get('description', 'N/A')}")
        lines.append(f"  - 1H: {tech.get('1h', {}).get('description', 'N/A')}")
        lines.append(f"**关键价位**：")
        lines.append(f"  - 支撑: {' / '.join(f'{s:.1f}' for s in supports[:3]) if supports else 'N/A'}")
        lines.append(f"  - 阻力: {' / '.join(f'{r:.1f}' for r in resistances[:3]) if resistances else 'N/A'}")
        lines.append(f"**当前价格**：{r.get('price', 0):,.2f}")
        lines.append(f"")
        lines.append(f"**情绪评分**：{sentiment.get('total', 0)}分 {sentiment.get('label', 'N/A')}")
        for d in sentiment.get("details", []):
            lines.append(f"  - {d.get('indicator')}: {d.get('value')} ({d.get('note')})")
        lines.append(f"")
        lines.append(f"**新闻状态**：{'⚠️ ' + '; '.join(news.get('events', [])) if news.get('has_event') else '无重大事件 ✅'}")
        if news.get("has_event"):
            lines.append(f"  > {news.get('warning')}")
        lines.append(f"**新闻波动**：{news_vol.get('note', 'N/A')} (最大波动 {news_vol.get('max_move_pct', 0)}%)")
        lines.append(f"")
        pred_market = r.get("prediction_market", {})
        lines.append(f"**预测市场**：{pred_market.get('note', 'N/A')}")
        lines.append(f"**链上数据**：{onchain.get('status', 'N/A')}")
        for d in onchain.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"")
        lines.append(f"**订单簿**：{ob.get('imbalance', 'N/A')}")
        for d in ob.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"**吃单分析**：{'⚠️ 恐慌信号!' if taker.get('panic') else '正常'}")
        for d in taker.get("details", []):
            lines.append(f"  - {d}")
        lines.append(f"")
        lines.append(f"**15分钟信号**（需要≥{signals.get('required', 3)}个）：")
        for s in signals.get("signals", []):
            mark = "✅" if s.get("found") else "❌"
            lines.append(f"  {mark} {s['name']}: {s['detail']}")
        lines.append(f"  总计: {signals.get('signals_found', 0)}/{signals.get('total_checked', 5)}")
        lines.append(f"")
        lines.append(f"**信号等级**：{r.get('signal_grade', 'N/A')}")
        lines.append(f"")
        lines.append(f"**操作建议**：")
        if r.get("can_trade"):
            lines.append(f"  - 方向：{risk.get('direction', 'N/A')}")
            lines.append(f"  - 入场区间：{risk.get('entry', 0):,.1f}")
            lines.append(f"  - 止损：{risk.get('stop_loss', 0):,.1f}")
            lines.append(f"  - 止盈1：{risk.get('take_profit_1', 0):,.1f}")
            lines.append(f"  - 止盈2（移动止损）：{risk.get('take_profit_2_params', {}).get('description', 'N/A')}")
            lines.append(f"  - 仓位：{risk.get('position', {}).get('final_pct', 0)*100:.2f}% = {risk.get('position', {}).get('position_value', 0):,.2f} USDT ({risk.get('position', {}).get('position_btc', 0):.6f} BTC)")
            lines.append(f"  - 仓位系数明细：{risk.get('position', {}).get('factor_breakdown', 'N/A')}")
            lines.append(f"  - 本单风险：{risk.get('risk_per_trade', 0):.2f}% | 盈亏比：{risk.get('reward_risk', 0):.2f}:1")
        else:
            lines.append(f"  - 当前不满足入场条件，建议观望 👀")
            reasons = []
            if not signals.get("has_entry"):
                reasons.append(f"15分钟信号不足（{signals.get('signals_found', 0)}/{signals.get('required', 3)}）")
            if news.get("has_event"):
                reasons.append("宏观事件窗口期")
            if tech.get("preferred_direction") == "wait":
                reasons.append("多周期方向不明确")
            if r.get("signal_grade") != "正常":
                reasons.append(f"信号降级（{r.get('signal_grade')}）")
            if onchain.get("score", 0) <= -3:
                reasons.append("链上数据偏空（鲸鱼/活跃地址等指标）")
            for reason in reasons:
                lines.append(f"  - ❌ {reason}")
        lines.append(f"")
        lines.append(f"**风险提示**：")
        lines.append(f"  - 数据源为Binance现货API，不含U本位合约深度")
        lines.append(f"  - 情绪数据中社交媒体维度为占位状态")
        lines.append(f"  - 链上数据需CryptoQuant/Glassnode API完善")
        lines.append(f"  - 15分钟级别信号噪音较高，建议以4H方向为主")
        lines.append(f"")
        tf_dir = tech.get('4h', {}).get('trend', '?')
        sg_count = signals.get('signals_found', 0)
        sg_req = signals.get('required', 3)
        emo = sentiment.get('label', '?')
        lines.append(f"**本单逻辑摘要**：")
        lines.append(f"  4H方向={tf_dir} | 15M信号={sg_count}/{sg_req} | 情绪={emo} | 信号等级={r.get('signal_grade', 'N/A')}")
        lines.append(f"  {'✅ 可开单' if r.get('can_trade') else '❌ 观望'}")
        lines.append(f"")
        lines.append(f"**自我进化建议**：")
        lines.append(f"  - 如果连续出现信号充足但亏损的情况，考虑提高信号阈值至4个")
        lines.append(f"  - 如果频繁被止损扫掉，可增大ATR倍数（激进→1.5，稳健→2.0）")
        lines.append(f"  - 如果方向判断反复切换，建议在4H EMA上增加200EMA过滤")
        lines.append("---")
        return "\n".join(lines)
