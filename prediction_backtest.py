"""
预测回测验证 — 对比历史预测与实际走势，计算准确率

Usage:
    python prediction_backtest.py                    # 显示全部统计
    python prediction_backtest.py --days 7           # 近7天
    python prediction_backtest.py --json             # JSON输出
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import data as d
from prediction_log import get_recent_predictions, LOG_FILE


def evaluate_prediction(entry: Dict) -> Optional[Dict]:
    """Evaluate a single prediction against actual outcome.

    For a 5m prediction: check price after 5 minutes
    For a 15m prediction: check price after 15 minutes
    """
    try:
        pred_ts = datetime.fromisoformat(entry["ts"])
        tf = entry["timeframe"]
        pred_direction = entry["direction"]
        pred_price = entry["price"]

        # Determine look-forward period
        if tf == "5m":
            look_forward_minutes = 5
            interval = "5m"
        else:
            look_forward_minutes = 15
            interval = "15m"

        # Check if enough time has passed
        now = datetime.now(timezone.utc)
        target_time = pred_ts + timedelta(minutes=look_forward_minutes)
        if target_time > now:
            return {
                "evaluated": False,
                "reason": f"预测尚未到期 (需等到{target_time.strftime('%H:%M')})",
            }

        # Fetch the kline that covers the target time
        # We need the candle that closed at or after target_time
        lookback_minutes = look_forward_minutes + 30  # Add buffer
        limit = max(lookback_minutes // 5, 10)

        klines = d.fetch_klines("BTCUSDT", interval, limit)
        if not klines:
            return {"evaluated": False, "reason": "无法获取验证数据"}

        parsed = d.parse_klines_to_dicts(klines)
        # Find the candle closest to prediction time + look_forward
        pred_ts_ms = int(pred_ts.timestamp() * 1000)
        target_ms = pred_ts_ms + look_forward_minutes * 60 * 1000

        future_candles = [k for k in parsed if k["time"] >= pred_ts_ms]
        if not future_candles:
            return {"evaluated": False, "reason": "无后续K线数据"}

        # Use the first complete candle after prediction
        outcome_candle = future_candles[0]
        outcome_price = outcome_candle["close"]

        # Determine actual direction
        if outcome_price > pred_price * 1.0005:  # 0.05% threshold
            actual = "up"
        elif outcome_price < pred_price * 0.9995:
            actual = "down"
        else:
            actual = "neutral"

        # Check if prediction was correct
        if pred_direction in ("up", "leaning_up") and actual == "up":
            correct = True
        elif pred_direction in ("down", "leaning_down") and actual == "down":
            correct = True
        elif pred_direction == "neutral" and actual == "neutral":
            correct = True
        else:
            correct = False

        change_pct = (outcome_price - pred_price) / pred_price * 100

        return {
            "evaluated": True,
            "correct": correct,
            "pred_direction": pred_direction,
            "actual_direction": actual,
            "pred_price": pred_price,
            "outcome_price": outcome_price,
            "change_pct": round(change_pct, 4),
            "pred_score": entry.get("total_score", 0),
            "pred_confidence": entry.get("confidence", 0),
        }

    except Exception as e:
        return {"evaluated": False, "reason": str(e)}


def run_backtest(days: int = 7) -> Dict:
    """Run backtest on predictions from the last N days."""
    predictions = get_recent_predictions(10000)
    if not predictions:
        return {"error": "无预测记录", "total": 0}

    # Filter by date range
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for p in predictions:
        try:
            ts = datetime.fromisoformat(p["ts"])
            if ts >= cutoff:
                recent.append(p)
        except (KeyError, ValueError):
            continue

    if not recent:
        return {"error": f"近{days}天无预测记录", "total": 0}

    results = []
    correct = 0
    evaluated = 0

    for entry in recent:
        result = evaluate_prediction(entry)
        if result and result.get("evaluated"):
            evaluated += 1
            if result["correct"]:
                correct += 1
            results.append(result)

    if evaluated == 0:
        return {
            "total_predictions": len(recent),
            "evaluated": 0,
            "message": "所有预测尚未到期或无法验证",
        }

    accuracy = correct / evaluated * 100 if evaluated > 0 else 0

    # Direction-specific accuracy
    dir_stats = {}
    for r in results:
        d = r["pred_direction"]
        if d not in dir_stats:
            dir_stats[d] = {"total": 0, "correct": 0}
        dir_stats[d]["total"] += 1
        if r["correct"]:
            dir_stats[d]["correct"] += 1

    for d in dir_stats:
        dir_stats[d]["accuracy"] = round(dir_stats[d]["correct"] / dir_stats[d]["total"] * 100, 1)

    # Score distribution of correct vs incorrect
    correct_scores = [r["pred_score"] for r in results if r["correct"]]
    incorrect_scores = [r["pred_score"] for r in results if not r["correct"]]

    return {
        "total_predictions": len(recent),
        "evaluated": evaluated,
        "correct": correct,
        "accuracy_pct": round(accuracy, 1),
        "direction_accuracy": dir_stats,
        "avg_score_correct": round(sum(correct_scores) / len(correct_scores), 1) if correct_scores else 0,
        "avg_score_incorrect": round(sum(incorrect_scores) / len(incorrect_scores), 1) if incorrect_scores else 0,
        "recent_results": results[-20:],
    }


def print_report(report: Dict):
    """Pretty-print backtest results."""
    print("\n" + "=" * 60)
    print("  预测准确率回测报告")
    print("=" * 60)

    if report.get("error"):
        print(f"  {report['error']}")
        return

    print(f"  总预测数:     {report['total_predictions']}")
    print(f"  已评估:       {report['evaluated']}")
    print(f"  正确:         {report['correct']}")
    print(f"  准确率:       {report['accuracy_pct']}%")
    print(f"  正确平均分:   {report['avg_score_correct']}")
    print(f"  错误平均分:   {report['avg_score_incorrect']}")
    print()

    print("  分方向准确率:")
    for d, s in report.get("direction_accuracy", {}).items():
        bar = "█" * int(s["accuracy"] / 10) + "░" * (10 - int(s["accuracy"] / 10))
        print(f"    {d:<14} {s['accuracy']:5.1f}% {bar} ({s['correct']}/{s['total']})")

    print()
    print("  最近20条评估:")
    print(f"  {'时间':<22} {'预测':<8} {'实际':<8} {'价格变化':<10} {'结果'}")
    print("  " + "-" * 58)
    for r in report.get("recent_results", [])[-10:]:
        result_icon = "✓" if r["correct"] else "✗"
        print(f"  {r.get('pred_direction','?'):<8} → {r.get('actual_direction','?'):<8} "
              f"{r['change_pct']:>+8.4f}%  {result_icon}")
    print("=" * 60)


if __name__ == "__main__":
    days = 7
    json_out = False
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--days" and i + 1 < len(sys.argv) - 1:
            try:
                days = int(sys.argv[i + 2])
            except (ValueError, IndexError):
                pass
        if arg == "--json":
            json_out = True

    report = run_backtest(days)

    if json_out:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
