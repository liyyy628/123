"""
预测日志系统 — 记录每次预测结果，支持后续回测验证

每次预测自动保存: 时间戳、价格、方向、评分、7个因子、波动率
后续可通过 prediction_backtest.py 回测预测准确率
"""
import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "predictions.jsonl")
MAX_LOG_SIZE = 10000  # Max entries before rotation

_lock = threading.Lock()


def ensure_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def log_prediction(timeframe: str, prediction: Dict, price: float, open_price: float):
    """Save a prediction to the JSONL log file.

    Args:
        timeframe: '5m' or '15m'
        prediction: the full predict() return dict
        price: current BTC price
        open_price: candle open price
    """
    ensure_dir()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe,
        "price": price,
        "open_price": open_price,
        "direction": prediction.get("direction"),
        "confidence": prediction.get("confidence"),
        "total_score": prediction.get("total_score"),
        "volatility": prediction.get("volatility"),
        "factors": [
            {"name": f["name"], "score": f["score"], "detail": f["detail"]}
            for f in prediction.get("factors", [])
        ],
    }

    with _lock:
        try:
            ensure_dir()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # Rotate if too large
            if os.path.getsize(LOG_FILE) > MAX_LOG_SIZE * 300:
                backup = LOG_FILE.replace(".jsonl", f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
                os.rename(LOG_FILE, backup)
        except Exception:
            pass  # Don't break prediction flow for log errors


def get_recent_predictions(limit: int = 50) -> List[Dict]:
    """Read the most recent predictions from the log."""
    ensure_dir()
    entries = []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except FileNotFoundError:
        pass
    return entries[-limit:]


def get_stats() -> Dict:
    """Get basic prediction log statistics."""
    entries = get_recent_predictions(1000)
    if not entries:
        return {"total": 0, "message": "暂无预测记录"}

    directions = {}
    for e in entries:
        d = e.get("direction", "unknown")
        directions[d] = directions.get(d, 0) + 1

    scores = [e.get("total_score", 0) for e in entries]
    avg_score = sum(scores) / len(scores) if scores else 0

    return {
        "total": len(entries),
        "first_ts": entries[0]["ts"] if entries else None,
        "last_ts": entries[-1]["ts"] if entries else None,
        "direction_distribution": directions,
        "avg_absolute_score": round(sum(abs(s) for s in scores) / len(scores), 1) if scores else 0,
        "avg_score": round(avg_score, 1),
    }
