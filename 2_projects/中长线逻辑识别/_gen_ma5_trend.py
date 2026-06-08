#!/usr/bin/env python3
"""生成近5日MA5角度排名趋势数据 -> .index_data/ma5_trend.json"""
import os, json, glob, unicodedata
from collections import defaultdict

from _topic_utils import parse_topic_header

BASE = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(BASE, ".index_data")
RANK_DIR = os.path.join(BASE, ".ma5_ranking")
INTEREST = os.path.join(BASE, "interest_stock.md")
os.makedirs(INDEX_DIR, exist_ok=True)

def parse_ranking(path):
    """解析 ranking CSV，返回 [{code, name, rank, angle, price, chg}]"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8-sig") as f:
        lines = [l.strip() for l in f if l.strip()]
    if len(lines) < 2:
        return rows
    headers = [h.strip() for h in lines[0].split(",")]
    ci = {h: i for i, h in enumerate(headers)}
    for line in lines[1:]:
        cols = line.split(",")
        try:
            rank_val = int(cols[0].strip())
            if rank_val > 200:  # 只取前200名
                continue
            code_idx = ci.get("代码", ci.get("code", 1))
            name_idx = ci.get("名称", ci.get("name", 2))
            angle_idx = ci.get("角度", ci.get("angle", 3))
            price_idx = ci.get("最新价", ci.get("price", 4))
            chg_idx = ci.get("涨跌幅", ci.get("chg", 5))
            raw_code = cols[code_idx].strip()
            # 统一去前缀（sh/sz/bj），不同日期的 CSV 代码格式不一致
            norm_code = raw_code.replace("sh", "").replace("sz", "").replace("bj", "")
            raw_name = cols[name_idx].strip()
            # 全角→半角标准化（如"粤电力Ａ"→"粤电力A"）
            norm_name = unicodedata.normalize('NFKC', raw_name)
            rows.append({
                "code": norm_code,
                "name": norm_name,
                "rank": rank_val,
                "angle": float(cols[angle_idx].strip()),
                "price": cols[price_idx].strip(),
                "chg": cols[chg_idx].strip(),
            })
        except (ValueError, IndexError):
            continue
    return rows

def parse_sectors():
    """解析 interest_stock.md -> {stock_name: [sectors], stock_name_with_note: original_line}"""
    sector_map = {}
    notes = {}
    cur = None
    if not os.path.exists(INTEREST):
        return sector_map, notes
    with open(INTEREST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                cur = parse_topic_header(line)[0]  # 只用 topic_name 做分类
                continue
            if cur:
                # 提取名称和备注
                name = line.split("（")[0].split("(")[0].strip()
                note = ""
                if "（" in line:
                    note = line.split("（", 1)[1].rstrip("）")
                if name not in sector_map:
                    sector_map[name] = []
                sector_map[name].append(cur)
                notes[name] = note
    return sector_map, notes

# 加载近5日排名
files = sorted(glob.glob(os.path.join(RANK_DIR, "ranking_2026-*.csv")))
recent = files[-5:] if len(files) >= 5 else files

daily_rankings = []
for fp in recent:
    date = os.path.basename(fp).replace("ranking_", "").replace(".csv", "")
    rows = parse_ranking(fp)
    # 按 rank 升序
    rows.sort(key=lambda x: x["rank"])
    daily_rankings.append({"date": date, "stocks": rows})

if len(daily_rankings) < 2:
    print("⚠️  不足2天排名数据，至少需要2天")
    exit(0)

# 加载自选股题材
sectors, notes_map = parse_sectors()

# 以最新日为基础，构建每个标的的排名变化
latest = daily_rankings[-1]
prev = daily_rankings[-2] if len(daily_rankings) >= 2 else None

# 构建历史排名映射: {code: {date: rank}}
history = defaultdict(dict)
for dr in daily_rankings:
    for s in dr["stocks"]:
        history[s["code"]][dr["date"]] = s["rank"]

# 构建输出: 每个标的的最新排名 + 多日排名差
stocks = []
for s in latest["stocks"]:
    code = s["code"]
    name = s["name"]
    stock_sectors = sectors.get(name, [])
    note = notes_map.get(name, "")
    
    rank = s["rank"]
    # 与之前每一天对比的排名差 (date, diff)
    rank_diffs = []
    # 所有日期的排名序列
    rank_history = []
    for k in range(len(daily_rankings)-2, -1, -1):  # 从最近往前遍历
        prev_day = daily_rankings[k]
        prev_date = prev_day["date"]
        found = False
        for ps in prev_day["stocks"]:
            if ps["code"] == code:
                prev_rank = ps["rank"]
                diff = prev_rank - rank  # 正=上升
                rank_diffs.append({"date": prev_date, "rank": prev_rank, "diff": diff})
                rank_history.append(prev_rank)
                found = True
                rank = prev_rank
                break
        if not found:
            # 不在前200名内，用200作为基准排名计算增幅
            diff = 200 - rank
            rank_diffs.append({"date": prev_date, "rank": None, "diff": diff})
            rank_history.append(None)
            rank = 200  # 假设前日刚好在200名边缘
    
    # 反转使最新在前
    rank_diffs.reverse()
    rank_history.reverse()
    # 加入当日排名在最后
    rank_history.append(s["rank"])
    
    stocks.append({
        "code": code,
        "name": name,
        "rank": s["rank"],
        "rank_diffs": rank_diffs,
        "rank_history": rank_history,
        "angle": s["angle"],
        "chg": s["chg"],
        "sectors": stock_sectors,
        "note": note,
    })

    # 构建题材筛选标签: 计算每个题材 top3 标的的平均排名
topic_sector_names = set()
for v in sectors.values():
    for sn in v:
        topic_sector_names.add(sn)

topic_tags = []
for sector_name in topic_sector_names:
    sector_stocks = [s for s in stocks if sector_name in s["sectors"]]
    if len(sector_stocks) < 3:
        avg_rank = "-"
    else:
        top3 = sorted(sector_stocks, key=lambda x: x["rank"])[:3]
        avg_rank = round(sum(t["rank"] for t in top3) / 3)
    
    topic_tags.append({
        "name": sector_name,
        "count": len(sector_stocks),
        "avg_rank": avg_rank,
    })

# 按平均排名排序（有值的排前面）
topic_tags.sort(key=lambda x: x["avg_rank"] if isinstance(x["avg_rank"], int) else 9999)

output = {
    "date": latest["date"],
    "prev_date": prev["date"] if prev else "",
    "dates": [dr["date"] for dr in daily_rankings],
    "stocks": stocks,
    "topic_tags": topic_tags,
}

out_path = os.path.join(INDEX_DIR, "ma5_trend.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False)

print(f"✅ MA5趋势数据 → {out_path}")
print(f"   标的: {len(stocks)} | 题材标签: {len(topic_tags)} | 日期跨度: {daily_rankings[0]['date']} ~ {latest['date']}")
