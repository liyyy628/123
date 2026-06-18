"""
BTC量化策略回测引擎

基于15分钟K线数据回放BTCAnalyzer的技术信号逻辑，
模拟订单执行（含滑点和手续费），计算收益指标。
"""
import json
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import data as d
import indicators as ind
from config import (
    MODE_CONFIGS, DEFAULT_MODE, Mode,
    RSI_OVERSOLD, RSI_OVERBOUGHT, VOLUME_SURGE_RATIO,
    EMA_4H, EMA_1H,
)

logger = logging.getLogger(__name__)

# ---- Backtest Configuration ----
INITIAL_BALANCE = 10000.0       # USDT
COMMISSION_RATE = 0.001         # 0.1% per trade (spot)
SLIPPAGE_PCT = 0.0005           # 0.05% slippage
MIN_TRADE_INTERVAL = 8          # Min candles between trades (avoid overtrading)


# ═══════════════════════════════════════════════════════════════════
# Data Loading
# ═══════════════════════════════════════════════════════════════════

def load_historical_klines(symbol: str = "BTCUSDT",
                           interval: str = "15m",
                           start_date: str = None,
                           end_date: str = None,
                           limit: int = 1000) -> List[Dict]:
    """Load historical klines from Binance.

    Binance limits to 1000 candles per request. For longer periods,
    we paginate using the endTime parameter.
    """
    all_klines = []
    end_time_ms = None
    if end_date:
        end_time_ms = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

    while len(all_klines) < limit:
        batch = d.fetch_klines(symbol, interval, min(1000, limit - len(all_klines)))
        if not batch:
            break

        # Filter by date range
        parsed = d.parse_klines_to_dicts(batch)
        if start_date:
            start_ms = datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000
            parsed = [k for k in parsed if k["time"] >= start_ms]
        if end_date:
            end_ms = datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000
            parsed = [k for k in parsed if k["time"] <= end_ms]

        if not parsed:
            break

        all_klines = parsed + all_klines  # prepend (older data first)

        # Get the earliest timestamp for next pagination request
        earliest = min(k["time"] for k in parsed)
        # Fetch next batch ending before earliest
        if len(all_klines) < limit and earliest > 0:
            time.sleep(0.2)  # rate limit
            batch = d.fetch_klines(symbol, interval, 1000)
            # Filter older than earliest
            older = [k for k in d.parse_klines_to_dicts(batch) if k["time"] < earliest]
            if not older:
                break
            all_klines = older + all_klines
        else:
            break

    # Sort by time ascending
    all_klines.sort(key=lambda k: k["time"])
    if start_date:
        start_ms = datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000
        all_klines = [k for k in all_klines if k["time"] >= start_ms]
    if end_date:
        end_ms = datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000
        all_klines = [k for k in all_klines if k["time"] <= end_ms]

    return all_klines


# ═══════════════════════════════════════════════════════════════════
# Signal Detection (replicating BTCAnalyzer.analyze_15m_signals)
# ═══════════════════════════════════════════════════════════════════

def detect_signals(window_klines: List[Dict], mode_config) -> Dict:
    """Run the 15m signal detection on a window of klines.

    This is a lightweight version of BTCAnalyzer.analyze_15m_signals()
    that doesn't require live APIs.
    """
    k15 = window_klines
    if len(k15) < 30:
        return {"signals_found": 0, "signals": [], "has_entry": False}

    closes = [k["close"] for k in k15]
    highs = [k["high"] for k in k15]
    lows = [k["low"] for k in k15]
    last = k15[-1]
    prev = k15[-2]

    result = {"signals_found": 0, "signals": [], "has_entry": False}

    # a) Candlestick pattern
    pattern = ind.detect_candlestick_pattern(last)
    engulfing = ind.detect_engulfing(prev, last)
    if pattern or engulfing:
        result["signals"].append({
            "name": "K线形态",
            "found": True,
            "detail": f"{pattern or engulfing}",
            "direction": "bullish" if "bull" in str(pattern or engulfing) or "hammer" in str(pattern or "") else "bearish",
        })
        result["signals_found"] += 1
    else:
        result["signals"].append({"name": "K线形态", "found": False, "detail": "无"})

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
    if m_prev < s_prev and m_curr > s_curr:
        macd_signal = True
    elif m_prev > s_prev and m_curr < s_curr:
        macd_signal = True
    elif h_curr > 0 and h_prev < 0:
        macd_signal = True
    elif h_curr < 0 and h_prev > 0:
        macd_signal = True
    result["signals"].append({"name": "MACD", "found": macd_signal, "detail": f"MACD={m_curr:.0f}"})
    if macd_signal:
        result["signals_found"] += 1

    # c) RSI
    rsi_val = ind.rsi_current(closes)
    rsi_signal = False
    if rsi_val is not None:
        if rsi_val < RSI_OVERSOLD or rsi_val > RSI_OVERBOUGHT:
            rsi_signal = True
    result["signals"].append({"name": "RSI", "found": rsi_signal, "detail": f"RSI={rsi_val:.1f}" if rsi_val else "N/A"})
    if rsi_signal:
        result["signals_found"] += 1

    # d) Volume
    vol = ind.analyze_volume(k15)
    result["signals"].append({"name": "成交量", "found": vol["surge"], "detail": f"量比{vol['ratio']}x"})
    if vol["surge"]:
        result["signals_found"] += 1

    result["has_entry"] = result["signals_found"] >= mode_config.min_signals
    return result


def determine_trend_from_window(window_klines: List[Dict], ema_periods: List[int]) -> str:
    """Determine trend direction from a window of 4H/1H klines."""
    trend_data = ind.determine_trend(window_klines, ema_periods)
    return trend_data.get("trend", "sideways")


# ═══════════════════════════════════════════════════════════════════
# Backtest Engine
# ═══════════════════════════════════════════════════════════════════

class Trade:
    """A single completed trade."""
    def __init__(self, direction: str, entry_price: float, entry_time: datetime,
                 stop_loss: float, take_profit: float, position_size: float):
        self.direction = direction
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.position_size = position_size  # in BTC
        self.exit_price = 0.0
        self.exit_time = None
        self.exit_reason = ""  # "tp", "sl", "close"
        self.pnl = 0.0
        self.pnl_pct = 0.0

    def close(self, exit_price: float, exit_time: datetime, reason: str):
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.exit_reason = reason
        if self.direction == "long":
            self.pnl_pct = (exit_price - self.entry_price) / self.entry_price
        else:
            self.pnl_pct = (self.entry_price - exit_price) / self.entry_price
        # Apply slippage and commission on exit
        slippage_cost = exit_price * SLIPPAGE_PCT * self.position_size
        commission_cost = exit_price * COMMISSION_RATE * self.position_size
        self.pnl = self.pnl_pct * self.entry_price * self.position_size
        self.pnl -= slippage_cost + commission_cost


class BacktestResult:
    """Container for backtest results."""
    def __init__(self):
        self.trades: List[Trade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.initial_balance = INITIAL_BALANCE
        self.final_balance = INITIAL_BALANCE
        self.total_return_pct = 0.0
        self.sharpe_ratio = 0.0
        self.max_drawdown_pct = 0.0
        self.win_rate = 0.0
        self.profit_factor = 0.0
        self.total_trades = 0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.best_trade = None
        self.worst_trade = None
        self.mode = ""
        self.start_date = ""
        self.end_date = ""

    def calculate_metrics(self):
        """Compute all performance metrics from trade history."""
        self.total_trades = len(self.trades)
        if self.total_trades == 0:
            return

        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl <= 0]
        self.win_rate = len(winning_trades) / self.total_trades

        total_wins = sum(t.pnl for t in winning_trades) if winning_trades else 0
        total_losses = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 0
        self.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        self.avg_win = total_wins / len(winning_trades) if winning_trades else 0
        self.avg_loss = total_losses / len(losing_trades) if losing_trades else 0

        # Final balance
        balance = self.initial_balance
        for t in self.trades:
            balance += t.pnl
        self.final_balance = balance
        self.total_return_pct = (self.final_balance - self.initial_balance) / self.initial_balance * 100

        # Equity curve
        eq_balance = self.initial_balance
        timestamps = [self.trades[0].entry_time] if self.trades else []
        for t in self.trades:
            eq_balance += t.pnl
            timestamps.append(t.exit_time)
        self.equity_curve = list(zip(timestamps, [eq_balance] * len(timestamps))) if timestamps else []

        # Max drawdown from equity curve
        peak = self.initial_balance
        max_dd = 0.0
        for _, val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown_pct = max_dd * 100

        # Sharpe ratio (simplified: assumes risk-free rate = 0)
        if len(self.trades) >= 2:
            returns_pct = [t.pnl_pct * 100 for t in self.trades]
            mean_ret = sum(returns_pct) / len(returns_pct)
            variance = sum((r - mean_ret) ** 2 for r in returns_pct) / (len(returns_pct) - 1)
            std_ret = math.sqrt(variance) if variance > 0 else 1e-10
            # Annualized (assuming ~365*24*4 = 35040 15-min periods per year)
            self.sharpe_ratio = (mean_ret / std_ret) * math.sqrt(35040) if std_ret > 0 else 0

        # Best / worst
        if self.trades:
            self.best_trade = max(self.trades, key=lambda t: t.pnl_pct)
            self.worst_trade = min(self.trades, key=lambda t: t.pnl_pct)

    def summary(self) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append(f"  回测报告 | 模式: {self.mode} | {self.start_date} ~ {self.end_date}")
        lines.append("=" * 60)
        lines.append(f"  初始资金:     ${self.initial_balance:,.2f}")
        lines.append(f"  最终资金:     ${self.final_balance:,.2f}")
        lines.append(f"  总收益率:     {self.total_return_pct:+.2f}%")
        lines.append(f"  夏普比率:     {self.sharpe_ratio:.2f}")
        lines.append(f"  最大回撤:     {self.max_drawdown_pct:.2f}%")
        lines.append(f"  总交易数:     {self.total_trades}")
        lines.append(f"  胜率:         {self.win_rate*100:.1f}%")
        lines.append(f"  盈亏比:       {self.profit_factor:.2f}")
        lines.append(f"  平均盈利:     ${self.avg_win:+.2f}")
        lines.append(f"  平均亏损:     ${self.avg_loss:+.2f}")
        if self.best_trade:
            lines.append(f"  最佳交易:     {self.best_trade.pnl_pct*100:+.2f}% @ {self.best_trade.entry_time}")
        if self.worst_trade:
            lines.append(f"  最差交易:     {self.worst_trade.pnl_pct*100:+.2f}% @ {self.worst_trade.entry_time}")
        lines.append("=" * 60)

        # Trade log
        lines.append("")
        lines.append(f"{'时间':<20} {'方向':<6} {'入场':<10} {'出场':<10} {'盈亏%':<8} {'原因'}")
        lines.append("-" * 65)
        for t in self.trades:
            dir_label = "做多" if t.direction == "long" else "做空"
            lines.append(
                f"{str(t.entry_time):<20} {dir_label:<6} "
                f"{t.entry_price:<10.1f} {t.exit_price:<10.1f} "
                f"{t.pnl_pct*100:>+7.2f}% {t.exit_reason}"
            )
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            "mode": self.mode,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_balance": self.initial_balance,
            "final_balance": round(self.final_balance, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "trades": [
                {
                    "entry_time": str(t.entry_time),
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl_pct": round(t.pnl_pct * 100, 4),
                    "pnl_usd": round(t.pnl, 2),
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
        }


# ═══════════════════════════════════════════════════════════════════
# Main Backtest Runner
# ═══════════════════════════════════════════════════════════════════

def run_backtest(start_date: str = "2026-05-01",
                 end_date: str = "2026-06-18",
                 mode: Mode = DEFAULT_MODE,
                 symbol: str = "BTCUSDT") -> BacktestResult:
    """Run a full backtest over the given date range.

    Args:
        start_date: YYYY-MM-DD start
        end_date: YYYY-MM-DD end
        mode: 'conservative' or 'aggressive'
        symbol: Trading pair
    """
    mode_config = MODE_CONFIGS[mode]
    logger.info(f"Loading historical data: {start_date} to {end_date}...")
    klines = load_historical_klines(symbol, "15m", start_date, end_date, limit=2000)
    if not klines:
        logger.error("No data loaded!")
        return BacktestResult()

    # Also load 4H and 1H for trend context
    klines_4h = load_historical_klines(symbol, "4h", start_date, end_date, limit=500)
    klines_1h = load_historical_klines(symbol, "1h", start_date, end_date, limit=500)

    logger.info(f"Loaded {len(klines)} 15m candles, running backtest...")

    result = BacktestResult()
    result.mode = mode
    result.start_date = start_date
    result.end_date = end_date

    balance = INITIAL_BALANCE
    position = None  # Current open trade or None
    last_trade_idx = -MIN_TRADE_INTERVAL
    warmup = 50  # Need warmup candles for indicators

    for i in range(warmup, len(klines)):
        current_candle = klines[i]
        current_price = current_candle["close"]
        current_time = datetime.fromtimestamp(current_candle["time"] / 1000)

        # Check if we need to close existing position
        if position is not None:
            if position.direction == "long":
                if current_candle["low"] <= position.stop_loss:
                    position.close(position.stop_loss, current_time, "止损")
                elif current_candle["high"] >= position.take_profit:
                    position.close(position.take_profit, current_time, "止盈")
                elif i == len(klines) - 1:
                    position.close(current_price, current_time, "收盘")
            else:  # short
                if current_candle["high"] >= position.stop_loss:
                    position.close(position.stop_loss, current_time, "止损")
                elif current_candle["low"] <= position.take_profit:
                    position.close(position.take_profit, current_time, "止盈")
                elif i == len(klines) - 1:
                    position.close(current_price, current_time, "收盘")

            if position.exit_price > 0:
                balance += position.pnl
                result.trades.append(position)
                position = None
                last_trade_idx = i
                continue

        # Skip if cooldown period not passed
        if i - last_trade_idx < MIN_TRADE_INTERVAL:
            continue

        # Detect signals
        window_15m = klines[max(0, i - 50):i + 1]
        signals = detect_signals(window_15m, mode_config)

        # Determine trend from 4H context
        # Find closest 4H candle before current time
        trend_4h = "sideways"
        if klines_4h:
            # Use the most recent 4H candle relative to current time
            recent_4h = [k for k in klines_4h if k["time"] <= current_candle["time"]]
            if len(recent_4h) >= 20:
                trend_4h = determine_trend_from_window(recent_4h[-30:], EMA_4H)

        trend_1h = "sideways"
        if klines_1h:
            recent_1h = [k for k in klines_1h if k["time"] <= current_candle["time"]]
            if len(recent_1h) >= 20:
                trend_1h = determine_trend_from_window(recent_1h[-30:], EMA_1H)

        # Determine trade direction
        direction = "wait"
        if trend_4h == "bullish" and trend_1h != "bearish":
            direction = "long"
        elif trend_4h == "bearish" and trend_1h != "bullish":
            direction = "short"

        if not signals["has_entry"] or direction == "wait":
            continue

        # Calculate position size and risk
        atr_val = ind.atr_current(
            [k["high"] for k in window_15m],
            [k["low"] for k in window_15m],
            [k["close"] for k in window_15m],
        )
        if atr_val is None or atr_val <= 0:
            continue

        position_pct = mode_config.position_pct
        position_value = balance * position_pct
        position_size = position_value / current_price  # BTC

        # Commission on entry
        entry_commission = current_price * COMMISSION_RATE * position_size
        balance -= entry_commission

        offset = atr_val * mode_config.atr_multiplier
        if direction == "long":
            stop_loss = current_price - offset
            take_profit = current_price + offset  # 1:1 risk-reward for simplicity
        else:
            stop_loss = current_price + offset
            take_profit = current_price - offset

        position = Trade(
            direction=direction,
            entry_price=current_price,
            entry_time=current_time,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
        )

    # Close any remaining open position
    if position is not None and position.exit_price == 0:
        last_price = klines[-1]["close"]
        position.close(last_price, datetime.fromtimestamp(klines[-1]["time"] / 1000), "强制平仓")
        balance += position.pnl
        result.trades.append(position)

    result.calculate_metrics()
    return result
