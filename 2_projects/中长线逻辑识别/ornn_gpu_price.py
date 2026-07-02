#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ornn GPU Compute Price Index 获取脚本

从 api.ornnai.com 抓取 Ornn Compute Price Index (OCPI) 数据，
记录到 CSV 文件中，方便每日追踪 H100/H200/B200 等 GPU 租赁价格走势。

用法:
    python3 ornn_gpu_price.py                          # 获取并记录全部 GPU 类型的当日价格
    python3 ornn_gpu_price.py --today                  # 仅输出当日价格摘要（不追加 CSV）
    python3 ornn_gpu_price.py --history [N]            # 显示最近 N 天的走势表（默认 7 天）
    python3 ornn_gpu_price.py --history H100\ SXM 14   # 指定 GPU 类型 + 天数
    python3 ornn_gpu_price.py --backfill               # 将 API 全部历史数据补录到 CSV
    python3 ornn_gpu_price.py --plot                   # 打开实时折线图（过去半年走势）

支持的 GPU 类型:
    - H100 SXM
    - H200
    - A100 SXM4
    - RTX 5090
    - B200
    - RTX PRO 6000 WS
"""

import csv
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── matplotlib（可选，仅 --plot 时需要） ─────────────────
try:
    import matplotlib
    matplotlib.use("MacOSX")  # macOS 原生后端，避免空白图
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import FormatStrFormatter
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# macOS Python 经常缺少系统根证书，创建不验证 SSL 的上下文
ssl_ctx = ssl._create_unverified_context()

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(PROJECT_DIR, "ornn_gpu_prices.csv")

GPU_TYPES = [
    "H100 SXM",
    "H200",
    "A100 SXM4",
    "RTX 5090",
    "B200",
    "RTX PRO 6000 WS",
]

API_TEMPLATE = "https://api.ornnai.com/api/gpu/{}/index-history"


def fetch_gpu_history(gpu_type):
    """Fetch index history for a single GPU type. Returns list of {timestamp, index_value}."""
    url = API_TEMPLATE.format(urllib.request.quote(gpu_type, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  ⚠ {gpu_type}: 请求失败 — {e}", file=sys.stderr)
        return []

    if not data.get("success"):
        print(f"  ⚠ {gpu_type}: API 返回失败 — {data.get('message', 'unknown')}", file=sys.stderr)
        return []

    return data.get("data", [])


def get_today_price(gpu_type, history):
    """Get the most recent (today's) index value from history."""
    if not history:
        return None, None
    latest = history[-1]
    ts = latest["timestamp"]
    val = latest["index_value"]
    return ts, val


def load_existing_csv():
    """Load existing CSV records into a dict keyed by (date, gpu_type)."""
    if not os.path.exists(CSV_PATH):
        return set()
    existing = set()
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return existing
        for row in reader:
            existing.add((row.get("date", ""), row.get("gpu_type", "")))
    return existing


def append_to_csv(rows):
    """Append new rows to CSV, creating header if file doesn't exist."""
    file_exists = os.path.exists(CSV_PATH)
    fieldnames = ["date", "gpu_type", "index_value", "fetched_at"]

    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_csv_records():
    """Load all CSV records into a dict: gpu_type -> {date: index_value}."""
    if not os.path.exists(CSV_PATH):
        return {}
    records = {}
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return records
        for row in reader:
            gt = row.get("gpu_type", "")
            dt = row.get("date", "")
            val = row.get("index_value", "")
            if gt and dt and val:
                records.setdefault(gt, {})[dt] = float(val)
    return records


def show_history(days=7, gpu_filter=None):
    """从 API 获取历史数据并用表格展示最近 N 天走势。"""
    gpu_list = [gpu_filter] if gpu_filter else GPU_TYPES

    print("═" * 60)
    print(f"  Ornn GPU Price Index — 最近 {days} 天走势")
    print("═" * 60)
    print()

    all_data = {}  # gpu_type -> {date: value}
    max_date_set = set()

    for gpu_type in gpu_list:
        history = fetch_gpu_history(gpu_type)
        for entry in history:
            dt = entry["timestamp"][:10]
            val = entry["index_value"]
            all_data.setdefault(gpu_type, {})[dt] = val
            max_date_set.add(dt)

    if not all_data:
        print("  ⚠ 未获取到数据")
        return

    # 取最近 N 天的日期（取所有 GPU 类型的并集，排序后截取）
    sorted_dates = sorted(max_date_set)[-days:]

    # 打印表头：日期列
    header = f"  {'GPU 类型':<16s}"
    for d in sorted_dates:
        # 简化为 MM-DD
        header += f" | {d[5:]}"
    print("  " + header)
    print("  " + "─" * (18 + len(sorted_dates) * 7))

    for gpu_type in gpu_list:
        row = f"  {gpu_type:<16s}"
        for d in sorted_dates:
            val = all_data.get(gpu_type, {}).get(d)
            if val is not None:
                row += f" | ${val:<4.2f}"
            else:
                row += f" | {'—':>5s}"
        print(row)

    print()


def backfill():
    """将 API 返回的全部历史数据补录到 CSV 中。"""
    existing = load_existing_csv()
    new_rows = []

    print("═" * 60)
    print("  Ornn GPU 历史数据补录 (backfill)")
    print("═" * 60)
    print()

    for gpu_type in GPU_TYPES:
        print(f"  [{GPU_TYPES.index(gpu_type)+1}/{len(GPU_TYPES)}] {gpu_type}...", end="", flush=True)
        history = fetch_gpu_history(gpu_type)
        if not history:
            print(" ⚠ 失败")
            continue

        count = 0
        for entry in history:
            dt = entry["timestamp"][:10]
            val = entry["index_value"]
            if (dt, gpu_type) in existing:
                continue
            new_rows.append({
                "date": dt,
                "gpu_type": gpu_type,
                "index_value": f"{val:.2f}",
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            count += 1

        print(f" ✓ 新增 {count} 条 (共 {len(history)} 天数据)")

    if new_rows:
        append_to_csv(new_rows)
        print(f"\n  共补录 {len(new_rows)} 条 → {CSV_PATH}")
    else:
        print("\n  全部数据已在 CSV 中，无新增")

    print()


PLOT_GPU_TYPES = [
    "H100 SXM",
    "H200",
    "B200",
    "A100 SXM4",
]


def plot_prices():
    """获取实时 API 数据，绘制过去半年的 GPU 价格走势图。"""
    if not HAS_MPL:
        print("  ⚠ 请先安装 matplotlib: pip3 install matplotlib")
        return

    print("═" * 60)
    print("  正在获取实时数据绘制走势图...")
    print("═" * 60)
    print()

    all_series = {}

    for gpu_type in PLOT_GPU_TYPES:
        print(f"  {gpu_type}...", end="", flush=True)
        history = fetch_gpu_history(gpu_type)
        if not history:
            print(" ⚠ 空")
            continue

        dates = []
        values = []
        for entry in history:
            ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
            dates.append(ts)
            values.append(entry["index_value"])

        all_series[gpu_type] = (dates, values)
        print(f" ✓ {len(dates)} 天")

    if not all_series:
        print("\n  ⚠ 无数据可绘")
        return

    # ── 画图 ──
    try:
        plt.style.use("seaborn-v0_8-darkgrid")
    except OSError:
        pass
    fig, ax = plt.subplots(figsize=(14, 6.5))

    colors = ["#E74C3C", "#3498DB", "#2ECC71", "#F39C12"]
    markers = ["o", "s", "^", "D"]

    for idx, (gpu_type, (dates, values)) in enumerate(all_series.items()):
        c = colors[idx % len(colors)]
        m = markers[idx % len(markers)]
        ax.plot(dates, values, color=c, marker=m, markersize=3,
                linewidth=1.6, label=gpu_type, alpha=0.88)

    # 自动缩放 y 轴留 10% 余量
    all_vals = [v for _, (_, vs) in all_series.items() for v in vs]
    y_min, y_max = min(all_vals), max(all_vals)
    y_pad = (y_max - y_min) * 0.1 or 0.5
    ax.set_ylim(y_min - y_pad, y_max + y_pad)

    # 标注最新值
    for gpu_type, (dates, values) in all_series.items():
        last_d = dates[-1]
        last_v = values[-1]
        ax.annotate(f"${last_v:.2f}", (last_d, last_v),
                    textcoords="offset points", xytext=(10, 6),
                    fontsize=9, fontweight="bold", color="#2C3E50",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="#CCC", alpha=0.85))

    ax.set_title("Ornn GPU Compute Price Index (OCPI) — 实时走势", fontsize=15, fontweight="bold", pad=14)
    ax.set_ylabel("价格 ($/h)", fontsize=12)
    ax.set_xlabel("日期", fontsize=12)
    ax.legend(fontsize=10, framealpha=0.9, edgecolor="#BBB")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=30, ha="right", fontsize=9)
    ax.yaxis.set_major_formatter(FormatStrFormatter("$%.2f"))
    ax.grid(True, alpha=0.35, linestyle="--")
    fig.tight_layout()

    import subprocess

    # 先导出 PNG，用系统图片查看器打开（绕过 matplotlib 后端显示空白问题）
    tmp_png = os.path.join(PROJECT_DIR, ".ornn_plot_temp.png")
    # 清理旧临时文件（放入回收站）
    if os.path.exists(tmp_png):
        subprocess.run(["trash", tmp_png], capture_output=True)
    fig.savefig(tmp_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    subprocess.run(["open", tmp_png])
    print(f"\n  ✅ 走势图已生成并打开")


def main():
    args = [a for a in sys.argv if not a.startswith("-")]

    # --history [gpu_type] [days]
    if "--history" in sys.argv:
        idx = sys.argv.index("--history")
        days = 7
        gpu_filter = None
        rest = sys.argv[idx + 1:]
        for r in rest:
            if r.isdigit():
                days = int(r)
            elif r in GPU_TYPES:
                gpu_filter = r
        show_history(days=days, gpu_filter=gpu_filter)
        return

    if "--backfill" in sys.argv:
        backfill()
        return

    if "--plot" in sys.argv:
        plot_prices()
        return

    only_today = "--today" in sys.argv

    print("═" * 60)
    print("  Ornn GPU Compute Price Index (OCPI) 数据获取")
    print("═" * 60)
    print()

    if only_today:
        print("  GPU 类型        | 日期          | 指数价格 ($/h)")
        print("  " + "─" * 55)
        for gpu_type in GPU_TYPES:
            history = fetch_gpu_history(gpu_type)
            ts, val = get_today_price(gpu_type, history)
            if val is not None:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
                print(f"  {gpu_type:<16s} | {dt} | ${val:.2f}")
            else:
                print(f"  {gpu_type:<16s} | {'—':>10s} | {'无法获取':>10s}")
        print()
        return

    # 加载已有记录，避免重复
    existing = load_existing_csv()
    new_rows = []

    for gpu_type in GPU_TYPES:
        print(f"  [{GPU_TYPES.index(gpu_type)+1}/{len(GPU_TYPES)}] {gpu_type}...", end="", flush=True)
        history = fetch_gpu_history(gpu_type)
        ts, val = get_today_price(gpu_type, history)
        if val is None:
            print(" ⚠ 失败")
            continue

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if (dt, gpu_type) in existing:
            print(f" ✓ 已存在 ({dt}: ${val:.2f})，跳过")
            continue

        new_rows.append({
            "date": dt,
            "gpu_type": gpu_type,
            "index_value": f"{val:.2f}",
            "fetched_at": now,
        })
        print(f" ✓ ${val:.2f} ({dt})")

    if new_rows:
        append_to_csv(new_rows)
        print(f"\n  新增 {len(new_rows)} 条记录 → {CSV_PATH}")
    else:
        print("\n  无新记录")

    print(f"\n  📄 CSV 路径: {CSV_PATH}")
    print()


if __name__ == "__main__":
    main()
