#!/usr/bin/env python3
"""
股票筛选器 — 基于多日分析报告的题材/标的筛选工具

用法:
    python stock_filter.py                  # 默认使用最新日期报告
    python stock_filter.py --date 05-14     # 指定日期
"""

import os
import re
import json
import sys
import datetime
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".stock_cache"
# 本地报告目录（读取用）
LOCAL_REPORT_DIR = CACHE_DIR / "reports"
MULTI_DIR = LOCAL_REPORT_DIR / "多日分析"
DAILY_DIR = LOCAL_REPORT_DIR / "每日分析"
# iCloud 报告目录（写入用，与 stock_trend_analysis.py 一致）
ICLOUD_BASE = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Documents" / "股票分析结果"
ICLOUD_FILTER_DIR = ICLOUD_BASE / "筛选结果"
# 本地筛选结果目录
LOCAL_FILTER_DIR = LOCAL_REPORT_DIR / "筛选结果"


# ── 报告解析 ──────────────────────────────────────────────

def find_latest_date():
    """从多日分析目录中获取最新日期"""
    files = sorted(MULTI_DIR.glob("multi_day_trend_*.md"))
    if not files:
        print("❌ 未找到任何多日分析报告")
        sys.exit(1)
    latest = files[-1].stem
    m = re.search(r"(\d{4}-\d{2}-\d{2})", latest)
    return m.group(1) if m else None


def load_report(date_str):
    """加载多日分析报告和 detail 报告"""
    multi_path = MULTI_DIR / f"multi_day_trend_{date_str}.md"
    detail_path = MULTI_DIR / f"multi_day_trend_{date_str}_detail.md"
    daily_path = DAILY_DIR / f"stock_trend_report_{date_str}.md"
    daily_detail_path = DAILY_DIR / f"stock_trend_report_{date_str}_detail.md"

    content = {}
    for label, path in [
        ("multi", multi_path),
        ("multi_detail", detail_path),
        ("daily", daily_path),
        ("daily_detail", daily_detail_path),
    ]:
        if path.exists():
            content[label] = path.read_text(encoding="utf-8")
        else:
            content[label] = None
            print(f"⚠️  文件不存在: {path.name}")

    return content


def parse_pct(s):
    """'+5.94%' → 5.94, '-1.18%' → -1.18"""
    return float(s.strip().replace("%", "").replace("+", ""))


def parse_sector_table(multi_text):
    """解析多日报告中的「题材情况」表"""
    rows = []
    in_sector = False
    for line in multi_text.splitlines():
        if "题材名称" in line and "d1" in line:
            in_sector = True
            continue
        if in_sector:
            if line.strip() == "" or (not line.strip().startswith("|")):
                break
            parts = [c.strip() for c in line.strip().split("|")]
            parts = [p for p in parts if p != ""]
            if len(parts) < 10:
                continue
            name = re.sub(r"\*+", "", parts[0]).strip()
            raw_status = re.sub(r"<[^>]+>", "", parts[1]).replace("**", "").strip()
            try:
                d1 = parse_pct(parts[2])
                d2 = parse_pct(parts[3])
                d3 = parse_pct(parts[4])
                d4 = parse_pct(parts[5])
                d5 = parse_pct(parts[6])
            except (ValueError, IndexError):
                continue
            avg5d = parts[7] if len(parts) > 7 else "-"
            pct5 = parts[8] if len(parts) > 8 else "-"
            stars = parts[9] if len(parts) > 9 else "-"

            rows.append({
                "name": name,
                "status": raw_status,
                "d1": d1, "d2": d2, "d3": d3, "d4": d4, "d5": d5,
                "avg5d": avg5d,
                "pct5": pct5,
                "stars": stars,
            })
    return rows


def parse_detail_table(detail_text):
    """解析 detail 报告中全部标的详情表"""
    rows = []
    in_table = False
    for line in detail_text.splitlines():
        if "标的" in line and "趋势" in line and "最新状态" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            parts = [c.strip() for c in line.strip().split("|")]
            parts = [p for p in parts if p != ""]
            if len(parts) < 3:
                continue
            rows.append({
                "name": parts[0].replace("**", "").strip(),
                "ratings": parts[1:-2],
                "trend": parts[-2] if len(parts) >= 2 else "",
                "status": parts[-1] if len(parts) >= 1 else "",
            })
    return rows


# ── 个股数据加载 ──────────────────────────────────────────────

def load_sector_map():
    """解析 interest_stock.md → {stock_name: [sector1, sector2, ...]}"""
    sector_map = {}
    path = BASE_DIR / "interest_stock.md"
    if not path.exists():
        print("⚠️  interest_stock.md 不存在")
        return sector_map

    current_sector = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_sector = line.lstrip("#").strip()
            continue
        if current_sector:
            stock_name = line.strip()
            # 去掉括号内的备注，如 云南锗业（磷化铟）→ 云南锗业
            clean_name = re.sub(r"[（(].+?[）)]", "", stock_name).strip()
            if clean_name not in sector_map:
                sector_map[clean_name] = []
            sector_map[clean_name].append(current_sector)

    return sector_map


def load_stock_daily_changes(date_str):
    """从 stock_data.json 加载指定日期的个股涨跌幅 → {stock_name: daily_change_pct}"""
    result = {}

    code_map_path = CACHE_DIR / "stock_code_map.json"
    data_path = CACHE_DIR / "stock_data.json"
    if not code_map_path.exists() or not data_path.exists():
        print("⚠️  stock_code_map.json 或 stock_data.json 不存在")
        return result

    code_map = json.loads(code_map_path.read_text(encoding="utf-8"))
    stock_data = json.loads(data_path.read_text(encoding="utf-8"))

    # 构建 stock_name → daily_change 映射
    for stock_name, stock_code in code_map.items():
        if stock_code not in stock_data:
            continue
        sd = stock_data[stock_code]
        dates = sd.get("dates", [])
        changes = sd.get("daily_changes", [])
        if not dates or not changes:
            continue
        # 找目标日期
        for i, d in enumerate(dates):
            if d == date_str and i < len(changes):
                result[stock_name] = changes[i]
                break

    return result


def load_stock_windows(date_str):
    """从 parse_cache 或 daily_detail 解析个股窗口数据 → {stock_name: {window6: float, conclusion: str}}"""
    # 优先从 parse_cache 加载
    cache_path = CACHE_DIR / "parse_cache" / f"stock_trend_report_{date_str}.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        stocks = data.get("stocks", {})
        result = {}
        for name, info in stocks.items():
            windows = info.get("windows", [])
            w6 = 0.0
            if windows:
                try:
                    w6 = parse_pct(windows[-1])
                except ValueError:
                    pass
            result[name] = {
                "window6": w6,
                "conclusion": re.sub(r"<[^>]+>|\*+", "", info.get("conclusion", "")),
            }
        return result

    # fallback: 从 daily_detail 报告解析
    detail_path = DAILY_DIR / f"stock_trend_report_{date_str}_detail.md"
    if not detail_path.exists():
        return {}

    result = {}
    in_table = False
    current_header = ""
    for line in detail_path.read_text(encoding="utf-8").splitlines():
        if "标的名称" in line and "窗口" in line:
            in_table = True
            current_header = line.strip()
            continue
        if in_table:
            if not line.strip().startswith("|"):
                in_table = False
                continue
            parts = [c.strip() for c in line.strip().split("|")]
            parts = [p for p in parts if p != ""]
            if len(parts) < 8:
                continue
            name = re.sub(r"\*+", "", parts[0]).strip()
            # 窗口6 是倒数第2列（结论前面）
            try:
                w6 = parse_pct(parts[-2])  # 窗口6
            except (ValueError, IndexError):
                w6 = 0.0
            conclusion = re.sub(r"<[^>]+>|\*+", "", parts[-1])
            result[name] = {"window6": w6, "conclusion": conclusion}

    return result


# ── 筛选器定义 ──────────────────────────────────────────────

FILTERS = {}


def register_filter(func):
    """装饰器：注册筛选器"""
    FILTERS[func.__name__] = func
    return func


@register_filter
def 抗跌题材(sectors, details, content, ctx):
    """
    筛选抗跌题材

    条件1：d4 > 0（前一日在涨）
    条件2（满足任一）：
        A) d5 > 0（最新日也在涨）
        B) d5 <= 0 且 |d5| < d4/2（虽有回调但跌幅小于前日涨幅的一半）

    输出：题材概览 + 题材内近5日涨幅前3的股票及其当天涨跌幅
    """
    sector_map = ctx["sector_map"]       # stock → [sectors]
    stock_windows = ctx["stock_windows"] # stock → {window6, conclusion}
    daily_changes = ctx["daily_changes"] # stock → daily_change%

    # 建立 sector → [stocks] 反向映射
    sector_stocks = {}
    for stock_name, stock_sectors in sector_map.items():
        for sec in stock_sectors:
            if sec not in sector_stocks:
                sector_stocks[sec] = []
            sector_stocks[sec].append(stock_name)

    result = []
    for s in sectors:
        d4, d5 = s["d4"], s["d5"]
        if d4 <= 0:
            continue
        cond_a = d5 > 0
        cond_b = d5 <= 0 and abs(d5) < d4 / 2
        if not (cond_a or cond_b):
            continue

        if cond_a:
            reason = "A: d5>0 持续上涨"
        else:
            reason = f"B: |d5|({abs(d5):.2f}) < d4/2({d4/2:.2f}) 小幅回调"

        # 查找题材内近5日涨幅前3的股票
        sec_name = s["name"]
        stocks_in_sector = sector_stocks.get(sec_name, [])
        stock_rank = []
        for stk in stocks_in_sector:
            w_info = stock_windows.get(stk, {})
            w6 = w_info.get("window6", 0.0)
            dc = daily_changes.get(stk)
            conc = w_info.get("conclusion", "")
            stock_rank.append({
                "name": stk,
                "window6": w6,
                "daily_change": dc,
                "conclusion": conc,
            })
        # 按近5日涨幅(window6)降序，取前3
        stock_rank.sort(key=lambda x: x["window6"], reverse=True)
        top3 = stock_rank[:3]

        result.append({**s, "reason": reason, "top3": top3})

    # 按 d5 降序排列
    result.sort(key=lambda x: x["d5"], reverse=True)
    return result, "抗跌题材"


# ── 输出格式化 & 保存 ──────────────────────────────────────────

def format_filter_result(result, title):
    """格式化筛选结果为文本（终端 + 文件通用）"""
    lines = []

    if not result:
        lines.append(f"📋 **{title}** — 无符合条件的数据\n")
        return "\n".join(lines)

    lines.append("=" * 60)
    lines.append(f"📋 {title} — 共 {len(result)} 个")
    lines.append("=" * 60)
    lines.append("")

    for r in result:
        d4_str = f"{r['d4']:+.2f}%"
        d5_str = f"{r['d5']:+.2f}%"
        avg5d = r.get("avg5d", "-")
        pct5 = r.get("pct5", "-")

        lines.append(f"📌 {r['name']}  |  {r['status']}  |  d4={d4_str}  d5={d5_str}  |  近5日={avg5d}  涨超5%占比={pct5}")
        lines.append(f"   原因: {r['reason']}")

        # Top3 股票
        top3 = r.get("top3", [])
        if top3:
            lines.append(f"   近5日涨幅前3:")
            for i, stk in enumerate(top3, 1):
                w6_str = f"{stk['window6']:+.2f}%"
                dc_str = f"{stk['daily_change']:+.2f}%" if stk["daily_change"] is not None else "N/A"
                conc = stk.get("conclusion", "")
                lines.append(f"     {i}. {stk['name']}  近5日={w6_str}  当日涨跌={dc_str}  {conc}")
        lines.append("")

    return "\n".join(lines)


def format_filter_result_md(result, title, date_str):
    """格式化筛选结果为 Markdown（用于保存到文件）"""
    lines = []

    if not result:
        lines.append(f"## {title}\n\n无符合条件的数据\n")
        return "\n".join(lines)

    lines.append(f"## {title}")
    lines.append(f"")
    lines.append(f"**报告日期:** {date_str}  |  **筛选数量:** {len(result)} 个")
    lines.append(f"")
    lines.append(f"| 题材 | 状态 | d4 | d5 | 近5日涨幅 | 涨超5%占比 | 原因 |")
    lines.append(f"|:-----|:----:|:--:|:--:|:--------:|:---------:|:-----|")

    for r in result:
        d4_str = f"{r['d4']:+.2f}%"
        d5_str = f"{r['d5']:+.2f}%"
        avg5d = r.get("avg5d", "-")
        pct5 = r.get("pct5", "-")
        lines.append(f"| {r['name']} | {r['status']} | {d4_str} | {d5_str} | {avg5d} | {pct5} | {r['reason']} |")

    lines.append(f"")

    # 每个题材的 Top3 股票详情
    lines.append(f"### 题材内近5日涨幅前3标的")
    lines.append(f"")

    for r in result:
        top3 = r.get("top3", [])
        if not top3:
            continue
        lines.append(f"**{r['name']}** ({r['status']}, d5={r['d5']:+.2f}%)")
        lines.append(f"")
        lines.append(f"| # | 标的 | 近5日涨幅 | 当日涨跌 | 评级 |")
        lines.append(f"|:-:|:-----|:--------:|:--------:|:----:|")
        for i, stk in enumerate(top3, 1):
            w6_str = f"{stk['window6']:+.2f}%"
            dc_str = f"{stk['daily_change']:+.2f}%" if stk["daily_change"] is not None else "N/A"
            conc = stk.get("conclusion", "")
            lines.append(f"| {i} | {stk['name']} | {w6_str} | {dc_str} | {conc} |")
        lines.append(f"")

    return "\n".join(lines)


def _write_filter_file(dir_path, filename, md_text):
    """写入筛选结果到指定目录，已存在则 append"""
    dir_path.mkdir(parents=True, exist_ok=True)
    out_path = dir_path / filename

    if out_path.exists():
        existing = out_path.read_text(encoding="utf-8")
        out_path.write_text(existing + "\n\n---\n\n" + md_text, encoding="utf-8")
    else:
        out_path.write_text(md_text, encoding="utf-8")

    return out_path


def save_filter_result(md_text, date_str):
    """保存筛选结果到 iCloud + 本地，已存在则 append"""
    filename = f"filter_{date_str}.md"

    # iCloud 主路径（云端留存）
    icloud_ok = False
    try:
        icloud_path = _write_filter_file(ICLOUD_FILTER_DIR, filename, md_text)
        icloud_ok = True
        print(f"✅ 已保存到 iCloud: {icloud_path}")
    except PermissionError as e:
        print(f"⚠️ 无法写入 iCloud: {e}")

    # 本地副本（本地留存）
    try:
        local_path = _write_filter_file(LOCAL_FILTER_DIR, filename, md_text)
        print(f"✅ 已保存到本地: {local_path}")
    except Exception as e:
        print(f"⚠️ 写入本地副本失败: {e}")


# ── 主交互 ──────────────────────────────────────────────

def main():
    # 解析命令行参数
    date_str = None
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            date_str_raw = sys.argv[idx + 1]
            if re.match(r"\d{2}-\d{2}$", date_str_raw):
                year = datetime.date.today().year
                date_str = f"{year}-{date_str_raw}"
            else:
                date_str = date_str_raw

    if not date_str:
        date_str = find_latest_date()

    if not date_str:
        print("❌ 无法确定报告日期")
        sys.exit(1)

    print(f"📅 使用报告日期: {date_str}")

    # 加载报告
    content = load_report(date_str)
    if not content.get("multi"):
        print("❌ 多日分析报告加载失败")
        sys.exit(1)

    # 解析报告
    sectors = parse_sector_table(content["multi"])
    details = parse_detail_table(content.get("multi_detail") or "") if content.get("multi_detail") else []

    # 加载个股数据
    sector_map = load_sector_map()
    stock_windows = load_stock_windows(date_str)
    daily_changes = load_stock_daily_changes(date_str)

    ctx = {
        "sector_map": sector_map,
        "stock_windows": stock_windows,
        "daily_changes": daily_changes,
    }

    print(f"📊 已解析题材数: {len(sectors)}，标的数: {len(details)}")
    print(f"📊 个股数据: sector_map={len(sector_map)}, stock_windows={len(stock_windows)}, daily_changes={len(daily_changes)}\n")

    # 展示筛选器菜单
    filter_list = list(FILTERS.items())
    while True:
        print("=" * 40)
        print("🔍 可用筛选器:")
        print("-" * 40)
        for i, (name, func) in enumerate(filter_list, 1):
            doc = (func.__doc__ or "").strip().split("\n")[0].strip()
            print(f"  [{i}] {name}  —  {doc}")
        print(f"  [0] 退出")
        print("-" * 40)

        choice = input("请输入编号选择筛选器: ").strip()
        if choice == "0":
            print("👋 再见！")
            break

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(filter_list):
                print("❌ 无效编号，请重新选择\n")
                continue
        except ValueError:
            print("❌ 请输入数字\n")
            continue

        name, func = filter_list[idx]
        print(f"\n▶️  执行筛选器: {name}\n")

        result, title = func(sectors, details, content, ctx)

        # 终端输出
        term_output = format_filter_result(result, title)
        print(term_output)

        # 保存到文件
        md_output = format_filter_result_md(result, title, date_str)
        save_filter_result(md_output, date_str)

        input("按回车继续...")


if __name__ == "__main__":
    main()
