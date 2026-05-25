#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日核心流程编排 —— 一个命令跑完分析+看板全链路。

用法:
    python daily_run.py              # 全流程（涨停提醒 + 分析看板）
    python daily_run.py --analyze    # 仅分析+看板（最常用）
"""

import argparse
import subprocess
import sys
import os
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("自选股趋势分析", ["python", "stock_trend_analysis.py", "-i", "interest_stock.md"]),
    ("MA5 角度排名", ["python", "scrape_ma5_ranking.py", "--top", "200"]),
    ("指数+ETF 数据", ["python", "index_data.py"]),
    ("异常检测", ["python", "anomaly_detection.py", "--save-tags"]),
    ("生成看板", ["python", "generate_dashboard.py"]),
]


def run(cmd, cwd):
    """Run a command; returns True on success."""
    return subprocess.run(cmd, cwd=cwd).returncode == 0


def main():
    parser = argparse.ArgumentParser(description="每日核心流程编排")
    parser.add_argument("--analyze", action="store_true", help="仅分析+看板，跳过涨停提醒")
    args = parser.parse_args()

    t0 = time.time()
    ok = 0
    fail = 0

    # ---- 涨停板复盘 ----
    if not args.analyze:
        print("=" * 60)
        print("  涨停板复盘 & interest_stock 更新")
        print("=" * 60)
        print()
        print("  此步骤需人工确认，请运行 /limit-up-review skill")
        print("  完成后按回车继续分析流程，或 Ctrl+C 退出...")
        print()
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消")
            sys.exit(0)

    # ---- 分析+看板 ----
    print()
    for i, (desc, cmd) in enumerate(STEPS, 1):
        print(f"[{i}/{len(STEPS)}] {desc}...")
        if run(cmd, PROJECT_DIR):
            ok += 1
            print(f"  ✓ 完成")
        else:
            fail += 1
            print(f"  ⚠ 失败（退出码非零），继续下一步")

    elapsed = time.time() - t0
    print()
    print(f"{'=' * 60}")
    print(f"  完成: {ok} 成功, {fail} 失败, 耗时 {elapsed:.0f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
