#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日核心流程编排 —— 一个命令跑完分析+看板全链路。

用法:
    python daily_run.py              # 全流程（涨停提醒 + 分析看板）
    python daily_run.py --analyze    # 仅分析+看板（最常用）
"""

import sys
if sys.version_info[0] < 3:
    sys.stderr.write("错误：请使用 Python 3 运行此脚本\n")
    sys.exit(1)

print("⚠️  请确保已关闭 VPN，否则爬虫可能无法正常工作\n")

import argparse
import subprocess
import sys
import os
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("自选股趋势分析", ["python3", "stock_trend_analysis.py", "-i", "interest_stock.md"]),
    ("题材筛选 (加强+抗跌)", ["python3", "stock_filter.py", "--all", "--save-tags"]),
    ("MA5 角度排名", ["python3", "scrape_ma5_ranking.py", "--top", "200"]),
    ("成交额排行", ["python3", "scrape_amount_ranking.py", "--top", "50"]),
    ("指数+ETF 数据", ["python3", "index_data.py"]),
    ("异常检测", ["python3", "anomaly_detection.py", "--save-tags"]),
    ("生成看板", ["python3", "generate_dashboard.py"]),
]


def run(cmd, cwd):
    """Run a command; returns (success: bool, output: str)."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, r.stderr.strip() or r.stdout.strip()


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
        success, output = run(cmd, PROJECT_DIR)
        if success:
            ok += 1
            print(f"  ✓ 完成")
        else:
            fail += 1
            print(f"  ⚠ 失败，继续下一步")
            if output:
                for line in output.splitlines()[-6:]:
                    print(f"    {line}")

    elapsed = time.time() - t0
    print()
    print(f"{'=' * 60}")
    print(f"  完成: {ok} 成功, {fail} 失败, 耗时 {elapsed:.0f}s")
    print(f"{'=' * 60}")

    # ---- 重启看板服务 ----
    print()
    print("[*] 重启看板服务 (dashboard_server.py)...")
    try:
        result = subprocess.run(["lsof", "-ti:8977"], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        if pids:
            for pid in pids:
                print(f"  停掉旧进程 PID={pid}")
                subprocess.run(["kill", pid], capture_output=True)
            time.sleep(1)

        log_path = os.path.join(PROJECT_DIR, "dashboard_server.log")
        with open(log_path, "w") as log_file:
            subprocess.Popen(
                ["python3", "dashboard_server.py"],
                cwd=PROJECT_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        time.sleep(1.5)

        check = subprocess.run(["lsof", "-ti:8977"], capture_output=True, text=True)
        if check.stdout.strip():
            print("  ✓ 看板服务已启动 (端口 8977)")
        else:
            print(f"  ⚠ 启动验证失败，查看日志: {log_path}")
    except Exception as e:
        print(f"  ⚠ 重启看板服务失败: {e}")

    # ---- 打开看板 ----
    print()
    subprocess.run(["open", "http://localhost:8977/dashboard.html"])
    print("  ✓ 已在浏览器打开看板")


if __name__ == "__main__":
    main()
