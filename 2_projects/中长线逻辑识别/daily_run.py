#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日核心流程编排 —— 一个命令跑完分析+看板全链路。

用法:
    python3 daily_run.py              # 全流程（涨停提醒 + 分析看板）
    python3 daily_run.py --analyze    # 仅分析+看板（最常用）
    python3 daily_run.py --refresh    # 强制刷新，第一步忽略缓存重新拉取 API
"""

import sys
if sys.version_info[0] < 3:
    sys.stderr.write("错误：请使用 Python 3 运行此脚本\n")
    sys.exit(1)

print("⚠️  请确保已关闭 VPN，否则爬虫可能无法正常工作\n")

import argparse
import datetime
import glob
import re
import subprocess
import sys
import os
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("自选股趋势分析", ["python3", "stock_trend_analysis.py", "-i", "interest_stock.md"]),
    # 题材筛选 (加强+抗跌) 已停用：抗跌题材/加强题材判断不再在每日流程中执行
    # （逻辑保留于 stock_filter.py，可手动运行）
    ("MA5 角度排名", ["python3", "scrape_ma5_ranking.py", "--top", "200"]),
    ("指数+ETF 数据", ["python3", "index_data.py"]),
    ("MA5角度趋势", ["python3", "_gen_ma5_trend.py"]),
    ("异常检测", ["python3", "anomaly_detection.py", "--save-tags"]),
    ("同花顺热榜", ["python3", "scrape_ths_hot_rank.py", "--risk-check"]),
    ("强势标的检测", ["python3", "strong_stocks.py"]),
    ("生成看板", ["python3", "generate_dashboard.py"]),
    ("同步到腾讯文档", ["python3", "sync_tencent_doc.py"]),
]


# ── 清理逻辑 ──────────────────────────────────────────────

CLEANUP_DIRS = [
    ".stock_cache/reports/每日分析/",
    ".stock_cache/reports/多日分析/",
    ".stock_cache/reports/筛选结果/",
    ".stock_cache/parse_cache/",
    "filter/",
]



def get_all_file_dates(filepath):
    """
    Try to extract a date from a filename using known patterns.
    Returns a datetime.date or None.
    """
    base = os.path.basename(filepath)
    # Pattern: YYYY-MM-DD anywhere in filename
    m = re.search(r'(\d{4}-\d{2}-\d{2})', base)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), '%Y-%m-%d').date()
        except ValueError:
            pass
    # Fallback: use file's mtime
    try:
        mtime = os.path.getmtime(filepath)
        return datetime.datetime.fromtimestamp(mtime).date()
    except OSError:
        return None


def cleanup_old_files(days=30):
    """删除指定天数之前的报告、筛选结果、5日线排行文件"""
    if days < 30:
        print(f"  ⛔ 拒绝清理：days={days} < 30，不允许删除 30 天以内的文件")
        return
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    deleted = 0
    skipped = 0

    for rel_dir in CLEANUP_DIRS:
        abs_dir = os.path.join(PROJECT_DIR, rel_dir)
        if not os.path.isdir(abs_dir):
            continue

        for fname in os.listdir(abs_dir):
            fpath = os.path.join(abs_dir, fname)
            if not os.path.isfile(fpath):
                continue

            fdate = get_all_file_dates(fpath)
            if fdate is None:
                skipped += 1
                continue

            if fdate < cutoff:
                try:
                    subprocess.run(["trash", fpath], capture_output=True, check=True)
                    deleted += 1
                except Exception as e:
                    print(f"  ⚠ 删除失败: {fpath} — {e}")

    print(f"  已删除 {deleted} 个旧文件（{days} 天前），跳过 {skipped} 个（无日期标记）")


def run(cmd, cwd):
    """Run a command; returns (success: bool, output: str)."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, r.stderr.strip() or r.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="每日核心流程编排")
    parser.add_argument("--analyze", action="store_true", help="仅分析+看板，跳过涨停提醒")
    parser.add_argument("--refresh", action="store_true", help="强制刷新：第一步忽略缓存重新拉取 API，后续步骤重新执行")
    args = parser.parse_args()

    # ---- 强制刷新时，仅删除 OHLCV 缓存 ----
    if args.refresh:
        print("=" * 60)
        print("  --refresh 模式：删除 OHLCV 缓存，强制重新拉取")
        print("=" * 60)
        cache_dir = os.path.join(PROJECT_DIR, ".stock_cache")
        for fname in ["stock_data.json"]:
            fpath = os.path.join(cache_dir, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
                print(f"  已删除缓存: {fname}")
        print("  报告/排名/筛选等后续步骤自动重新生成，不删除")
        print()

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

    # ---- 清理旧文件 ----
    print()
    print("=" * 60)
    print("  清理 30 天前的旧文件")
    print("=" * 60)
    cleanup_old_files()

    # ---- 分析+看板 ----
    print()
    for i, (desc, cmd) in enumerate(STEPS, 1):
        print(f"[{i}/{len(STEPS)}] {desc}...")
        success, output = run(cmd, PROJECT_DIR)
        if success:
            ok += 1
            print(f"  ✓ 完成")
            if output:
                for line in output.splitlines()[-20:]:
                    print(f"    {line}")
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

    # ---- 重启主看板服务 ----
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
