
import sys
import json
import logging
from datetime import datetime
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from config import DEFAULT_MODE, MODE_CONFIGS
from analyzer import BTCAnalyzer


def setup_logging(level=logging.INFO):
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib").setLevel(logging.WARNING)


def print_banner():
    banner = r"""
   ____ ___________   _____ _   _ _____    ___ _   _ _  _  ___
  | __ \_   _| __ ) |_   _| | | |_   _|  |_ _| \ | | || |/ _ \
  | |_) || | |  _ \   | | | | | | | |     | ||  \| | || | (_) |
  |  _ / | | | |_) |  | | | |_| | | |     | || |\  |__   _\__, |
  |_|   |_| |____/   |_|  \___/  |_|    |___|_| \_|  |_|   /_/
  ========================= ========
  BTC/USDT ???????? v1.0
  ====================================
"""
    print(banner)


def interactive_mode():
    print_banner()
    print("\n????????")
    mode_cfg = MODE_CONFIGS
    print(f"  1. ???? (??) - ?{mode_cfg['conservative'].min_signals}???ATR?{mode_cfg['conservative'].atr_multiplier}???{int(mode_cfg['conservative'].position_pct*100)}%")
    print(f"  2. ???? (??) - ?{mode_cfg['aggressive'].min_signals}???ATR?{mode_cfg['aggressive'].atr_multiplier}???{int(mode_cfg['aggressive'].position_pct*100)}%")
    print(f"  3. ???? ({DEFAULT_MODE})")
    choice = input("\n????? (1/2/3, ??3): ").strip()
    mode_map = {"1": "conservative", "2": "aggressive"}
    mode = mode_map.get(choice, DEFAULT_MODE)
    mode_label = "??" if mode == "aggressive" else "??"
    print(f"\n{'='*60}")
    print(f"??: {mode_label}")
    print(f"????: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")
    try:
        analyzer = BTCAnalyzer(mode)
        results = analyzer.analyze()
        report = analyzer.generate_report()
        print("\n" + report)
    except KeyboardInterrupt:
        print("\n\n? ????")
        sys.exit(0)
    except Exception as e:
        print(f"\n? ????: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def json_output_mode(mode: str = DEFAULT_MODE):
    setup_logging(logging.WARNING)
    try:
        analyzer = BTCAnalyzer(mode)
        results = analyzer.analyze()
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


def main():
    setup_logging()
    args = sys.argv[1:]
    if "--json" in args:
        idx = args.index("--json")
        mode = args[idx + 1] if idx + 1 < len(args) and args[idx + 1] in ["aggressive", "conservative"] else DEFAULT_MODE
        json_output_mode(mode)
    elif "--help" in args or "-h" in args:
        print("??: python main.py [--json [mode]]")
        print("  --json [conservative|aggressive]  ??JSON??")
        print("  ????                          ????")
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
