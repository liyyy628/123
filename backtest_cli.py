"""
回测CLI入口 - 支持命令行参数和历史回测
Usage:
    python backtest_cli.py --start 2026-05-01 --end 2026-06-18 --mode conservative
    python backtest_cli.py --start 2026-05-01 --end 2026-06-18 --mode aggressive --json
"""
import argparse
import json
import sys

from backtest import run_backtest
from config import DEFAULT_MODE


def main():
    parser = argparse.ArgumentParser(description="BTC量化策略回测系统")
    parser.add_argument("--start", default="2026-05-01",
                        help="回测开始日期 YYYY-MM-DD (默认: 2026-05-01)")
    parser.add_argument("--end", default="2026-06-18",
                        help="回测结束日期 YYYY-MM-DD (默认: 2026-06-18)")
    parser.add_argument("--mode", default=DEFAULT_MODE,
                        choices=["conservative", "aggressive"],
                        help=f"交易模式 (默认: {DEFAULT_MODE})")
    parser.add_argument("--symbol", default="BTCUSDT",
                        help="交易对 (默认: BTCUSDT)")
    parser.add_argument("--json", action="store_true",
                        help="以JSON格式输出结果")

    args = parser.parse_args()

    if args.json:
        import logging
        logging.getLogger().setLevel(logging.WARNING)

    print(f"\n{'='*60}")
    print(f"  BTC/USDT 量化策略回测")
    print(f"  模式: {args.mode} | 品种: {args.symbol}")
    print(f"  区间: {args.start} ~ {args.end}")
    print(f"{'='*60}\n")

    result = run_backtest(
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        symbol=args.symbol,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(result.summary())

    if result.total_trades == 0:
        print("\n⚠️ 回测期间无交易信号。可能原因：")
        print("  - 数据量不足")
        print("  - 所选期间市场条件不满足入场条件")
        print("  - 信号阈值过高")


if __name__ == "__main__":
    main()
