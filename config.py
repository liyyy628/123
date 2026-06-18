"""
交易系统全局配置
所有可调参数集中管理，方便回测调优
"""
from dataclasses import dataclass
from typing import Literal, Dict, List

Mode = Literal["aggressive", "conservative"]

@dataclass
class TradingModeConfig:
    min_signals: int
    atr_multiplier: float
    position_pct: float
    risk_reward: float = 2.0

MODE_CONFIGS: Dict[Mode, TradingModeConfig] = {
    "aggressive": TradingModeConfig(min_signals=2, atr_multiplier=1.2, position_pct=0.02),
    "conservative": TradingModeConfig(min_signals=3, atr_multiplier=1.8, position_pct=0.01),
}

DEFAULT_MODE: Mode = "conservative"

EMA_4H = [20, 50, 100, 200]
EMA_1H = [20, 50]
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
VOLUME_SURGE_RATIO = 1.5
ORDER_IMBALANCE_RATIO = 1.2
TAKER_VOLUME_SURGE = 3.0
WHALE_TRANSFER_THRESHOLD = 1000
EXCHANGE_NETFLOW_WARN = 500
FUNDING_RATE_BULLISH = -0.00005
FUNDING_RATE_BEARISH = 0.0001
LONG_SHORT_RATIO_BULLISH = 0.7
LONG_SHORT_RATIO_BEARISH = 1.5
FEAR_GREED_BULLISH = 25
FEAR_GREED_BEARISH = 75
SOCIAL_SURGE_THRESHOLD = 0.3
SENTIMENT_CONFLICT_FACTOR = 0.5
CHAIN_PRESSURE_FACTOR = 0.7
HIGH_IMPACT_EVENTS = ["CPI", "非农", "FOMC", "GDP", "PCE", "美联储", "interest rate", "NFP", "unemployment"]
NEWS_VOLATILITY_THRESHOLD = 0.008
TRAILING_STOP_TRIGGER = 0.5
TRAILING_STOP_MOVE = 0.3
BINANCE_BASE = "https://api.binance.com"
BINANCE_FUTURES = "https://fapi.binance.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
WEEKEND_DEGRADE = True
LOW_VOLUME_DEGRADE = True
MIN_15M_VOLUME_BTC = 50
REVIEW_LOG_PATH = "logs/trade_review.csv"
SELF_EVOLVE_LOG = "logs/self_evolve.json"
