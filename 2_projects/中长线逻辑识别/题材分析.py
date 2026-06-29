#!/usr/bin/env python3
"""
题材标的分析工具 — 支持 interest_stock.md 中任意题材

功能：
1. 从 interest_stock.md 中提取指定题材板块所有标的
2. 从东方财富 API 获取市盈率、营收同比、净利同比、涨跌幅
3. 分析涨跌幅与业绩指引的相关性
4. 输出 Markdown 表格 + 相关性分析

用法：
    python3 "题材分析.py" 光刻胶
    python3 "题材分析.py" "靶材"
    python3 "题材分析.py"      # 不带参数则列出所有题材
"""

import json
import subprocess
import math
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INTEREST_STOCK = BASE_DIR / "interest_stock.md"


def list_sectors():
    """列出 interest_stock.md 中所有题材名称"""
    text = INTEREST_STOCK.read_text(encoding="utf-8")
    sectors = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
            # 去除末尾的状态标记如（分化）（大分歧 2）等
            name = name.split("（")[0].split("(")[0].strip()
            sectors.append(name)
    return sectors


def read_watchlist_stocks(sector_name):
    """从 interest_stock.md 提取指定题材板块的标的"""
    text = INTEREST_STOCK.read_text(encoding="utf-8")
    lines = text.split("\n")
    
    # 找到目标题材
    sector_start = None
    sector_end = None
    matched_sector = None
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("# "):
            full_name = line_stripped[2:].strip()
            clean_name = full_name.split("（")[0].split("(")[0].strip()
            if sector_name == clean_name or sector_name in clean_name or clean_name in sector_name:
                sector_start = i + 1
                matched_sector = clean_name
                print(f"  匹配到题材: {full_name}")
                continue
        
        if sector_start and line_stripped.startswith("# ") and i > sector_start:
            sector_end = i
            break
    
    if sector_end is None:
        sector_end = len(lines)
    
    if sector_start is None:
        return [], matched_sector
    
    stocks = []
    for line in lines[sector_start:sector_end]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 格式: "股票名（备注...）"
        name = line.split("（")[0].split("(")[0].split(" ")[0].strip()
        if name:
            stocks.append(name)
    
    return stocks, matched_sector


def build_all_stock_name_map():
    """
    从东方财富获取全A股名称→代码映射
    API限制单页最多100条，需翻页获取全部5534+只标的
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    base_url = (f"https://push2.eastmoney.com/api/qt/clist/get?cb=jQuery&pn={{pn}}&pz=100"
                f"&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2"
                f"&fid=f3&fs={fs}&fields=f12,f14")
    
    # 先拿第1页获取总数
    result = subprocess.run(["curl", "-s", base_url.format(pn=1)],
                            capture_output=True, text=True, timeout=15)
    raw = result.stdout
    start = raw.find("(")
    end = raw.rfind(")")
    data = json.loads(raw[start+1:end])
    total = data.get("data", {}).get("total", 0)
    items = data.get("data", {}).get("diff", [])
    
    name_to_code = {}
    def add_item(item):
        code = item.get("f12", "")
        name = item.get("f14", "")
        name_to_code[name] = code
        if name.endswith("-U"):
            name_to_code[name[:-2]] = code
    
    for item in items:
        add_item(item)
    
    # 翻页获取剩余
    total_pages = (total + 99) // 100
    if total_pages > 1:
        print(f"  全A股共 {total} 只，分 {total_pages} 页获取...")
        processes = []
        for pn in range(2, total_pages + 1):
            p = subprocess.Popen(["curl", "-s", base_url.format(pn=pn)],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append((pn, p))
        
        for pn, p in processes:
            try:
                raw = p.stdout.read().decode("utf-8")
                start = raw.find("(")
                end = raw.rfind(")")
                data = json.loads(raw[start+1:end])
                for item in data.get("data", {}).get("diff", []):
                    add_item(item)
            except Exception:
                pass
            # 每页显示进度
            if pn % 10 == 0 or pn == total_pages:
                print(f"    已处理 {pn}/{total_pages} 页...")
    
    return name_to_code


def map_stock_to_code(names, name_to_code=None):
    """将股票名映射到代码"""
    if not names:
        return {}
    
    if name_to_code is None:
        name_to_code = build_all_stock_name_map()
    
    result = {}
    for name in names:
        if name in name_to_code:
            result[name] = name_to_code[name]
        else:
            matched = False
            for api_name, code in name_to_code.items():
                clean_api = api_name.replace("-U", "")
                if name == clean_api or name in api_name or api_name in name:
                    result[name] = code
                    matched = True
                    break
            if not matched:
                result[name] = None
    
    return result


def get_batch_stock_data(codes):
    """从东方财富批量获取PE、涨跌幅等数据"""
    secids_list = []
    for code in codes:
        if code.startswith("6") or code.startswith("9"):
            secids_list.append(f"1.{code}")
        else:
            secids_list.append(f"0.{code}")
    
    secids = ",".join(secids_list)
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            result = subprocess.run([
                "curl", "-s",
                f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f12,f14,f2,f3,f9,f37&secids={secids}"
            ], capture_output=True, text=True, timeout=10)
            data = json.loads(result.stdout)
            items = data.get("data", {}).get("diff", [])
            if items:
                return {item["f12"]: item for item in items}
        except Exception:
            pass
    
    return {}


def get_individual_profit_data(codes):
    """逐只获取净利润同比数据"""
    profit_data = {}
    for code in codes:
        market = "1" if code.startswith("6") or code.startswith("9") else "0"
        try:
            r = subprocess.run([
                "curl", "-s",
                f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f58,f183,f184,f185&fltt=2"
            ], capture_output=True, text=True, timeout=5)
            d = json.loads(r.stdout).get("data", {})
            profit_data[code] = {
                "rev_yoy": d.get("f184"),
                "profit_yoy": d.get("f185"),
            }
        except Exception as e:
            profit_data[code] = {"rev_yoy": None, "profit_yoy": None}
    
    return profit_data


def safe_float(v):
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def pearson_r(x, y):
    """皮尔逊相关系数"""
    n = len(x)
    if n < 3:
        return None, None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    if var_x == 0 or var_y == 0:
        return None, None
    r = cov / math.sqrt(var_x * var_y)
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1 else float("inf")
    return r, t


def fmt_pe(v):
    if v is None or v == "-":
        return "-"
    vf = float(v)
    if vf <= 0:
        return f"{vf:.2f}(亏损)"
    return f"{vf:.2f}"


def fmt_pct(v):
    if v is None or v == "-":
        return "-"
    try:
        vf = float(v)
        if abs(vf) > 10000:
            return "基数极低"
        return f"{vf:.2f}%"
    except (ValueError, TypeError):
        return "-"


def main():
    sector_query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    
    if not sector_query:
        # 不带参数：列出所有题材
        sectors = list_sectors()
        print("=" * 60)
        print("interest_stock 中所有题材列表:")
        print("=" * 60)
        for i, s in enumerate(sectors, 1):
            print(f"  {i:2d}. {s}")
        print()
        print(f"用法: python3 \"题材分析.py\" 题材名")
        print(f"示例: python3 \"题材分析.py\" 光刻胶")
        return
    
    print("=" * 80)
    print(f"题材标的分析工具 — {sector_query}")
    print("=" * 80)
    
    # Step 1: 读取题材标的
    print(f"\n[1/4] 从 interest_stock.md 读取 [{sector_query}] 板块...")
    stock_names, matched = read_watchlist_stocks(sector_query)
    
    if not stock_names:
        print(f"  未找到题材 [{sector_query}]")
        print("  可用题材:")
        for s in list_sectors():
            print(f"    - {s}")
        return
    
    print(f"  找到 {len(stock_names)} 只标的:")
    for n in stock_names:
        print(f"    - {n}")
    
    # Step 2: 名称→代码映射
    print("\n[2/4] 构建全A股名称→代码映射...")
    all_name_map = build_all_stock_name_map()
    print(f"  已加载 {len(all_name_map)} 只标的映射")
    
    name_code_map = map_stock_to_code(stock_names, all_name_map)
    
    codes = []
    unmatched = []
    for name in stock_names:
        code = name_code_map.get(name)
        if code:
            codes.append(code)
            print(f"  {name} -> {code}")
        else:
            unmatched.append(name)
            print(f"  ⚠ {name} -> 未找到匹配")
    
    if not codes:
        print("错误: 无法映射任何股票代码")
        return
    
    # Step 3: 获取财务数据
    print(f"\n[3/4] 获取 {len(codes)} 只标的市盈率和财务数据...")
    batch_data = get_batch_stock_data(codes)
    profit_data = get_individual_profit_data(codes)
    
    # 合并数据
    combined = []
    for name in stock_names:
        code = name_code_map.get(name)
        if not code:
            continue
        bd = batch_data.get(code, {})
        pd = profit_data.get(code, {})
        combined.append({
            "code": code,
            "name": name,
            "pe": bd.get("f9", "-"),
            "price": bd.get("f2", "-"),
            "chg": bd.get("f3", "-"),
            "rev_yoy": pd.get("rev_yoy", "-"),
            "profit_yoy": pd.get("profit_yoy", "-"),
        })
    
    # 按PE排序
    def sort_key(item):
        pe = safe_float(item["pe"])
        if pe is None:
            return (2, float("inf"))
        if pe <= 0:
            return (1, -pe)
        return (0, pe)
    combined.sort(key=sort_key)
    
    # Step 4: 输出表格
    print("\n[4/4] 输出汇总表格\n")
    sep = " | "
    header = f"| 代码{sep}名称{sep}当前市盈率(动){sep}营收同比%{sep}净利同比%{sep}今日涨跌幅 |"
    print(header)
    print("|------|------|---------------:|----------:|----------:|----------:|")
    for item in combined:
        print(
            f"| {item['code']} "
            f"| {item['name']} "
            f"| {fmt_pe(item['pe']):>15}"
            f"| {fmt_pct(item['rev_yoy']):>9}"
            f"| {fmt_pct(item['profit_yoy']):>9}"
            f"| {fmt_pct(item['chg']):>9} |"
        )
    
    if unmatched:
        print(f"\n  ⚠ {len(unmatched)} 只标的未在东方财富找到匹配:")
        for n in unmatched:
            print(f"    - {n}")
    
    # ========== 相关性分析 ==========
    print("\n\n" + "=" * 80)
    print(f"涨跌幅与业绩指引相关性分析 — {matched or sector_query}")
    print("=" * 80)
    
    chg_vals, profit_yoy_vals = [], []
    rev_yoy_vals, rev_yoy_chg_vals = [], []
    
    for item in combined:
        chg = safe_float(item["chg"])
        profit_yoy = safe_float(item["profit_yoy"])
        rev_yoy = safe_float(item["rev_yoy"])
        
        if chg is not None and profit_yoy is not None and -1000 <= profit_yoy <= 1000:
            chg_vals.append(chg)
            profit_yoy_vals.append(profit_yoy)
        
        if chg is not None and rev_yoy is not None and -200 <= rev_yoy <= 200:
            rev_yoy_chg_vals.append(chg)
            rev_yoy_vals.append(rev_yoy)
    
    # 净利同比 vs 涨跌幅
    print("\n📊 【净利同比 vs 涨跌幅】")
    if len(chg_vals) >= 3:
        r, t = pearson_r(profit_yoy_vals, chg_vals)
        if r is not None:
            strength = "强" if abs(r) > 0.7 else ("中等" if abs(r) > 0.4 else "弱")
            direction = "正相关" if r > 0 else "负相关"
            print(f"  样本量: {len(chg_vals)}")
            print(f"  皮尔逊相关系数 (r): {r:.4f}")
            print(f"  相关性: {strength} {direction}")
            if r > 0.3:
                print("  解读: 业绩增长越好 → 涨幅越大（业绩驱动）")
            elif r < -0.3:
                print("  解读: 业绩越好涨幅越小（利好兑现/提前反映）")
            else:
                print("  解读: 涨跌幅与净利同比无明显线性关系")
    else:
        print("  样本不足（<3），跳过")
    
    if chg_vals:
        print("\n  净利同比排序对照:")
        print(f"  {'名称':<8} {'净利同比%':<12} {'涨跌幅%':<10}")
        print(f"  {'-'*30}")
        pairs = [(safe_float(item["profit_yoy"]), safe_float(item["chg"]), item["name"]) for item in combined]
        pairs.sort(key=lambda x: x[0] if x[0] is not None else -1e9, reverse=True)
        for profit_y, chg, name in pairs:
            if profit_y is not None and chg is not None and -1000 <= profit_y <= 1000:
                print(f"  {name:<8} {profit_y:>+8.2f}%  {chg:>+6.2f}%")
    
    # 营收同比 vs 涨跌幅
    print("\n📊 【营收同比 vs 涨跌幅】")
    if len(rev_yoy_vals) >= 3:
        r, _ = pearson_r(rev_yoy_vals, rev_yoy_chg_vals)
        if r is not None:
            print(f"  皮尔逊相关系数 (r): {r:.4f}")
    else:
        print("  样本不足（<3），跳过")
    
    if rev_yoy_vals:
        print("\n  营收同比排序对照:")
        print(f"  {'名称':<8} {'营收同比%':<12} {'涨跌幅%':<10}")
        print(f"  {'-'*30}")
        pairs = [(safe_float(item["rev_yoy"]), safe_float(item["chg"]), item["name"]) for item in combined]
        pairs.sort(key=lambda x: x[0] if x[0] is not None else -1e9, reverse=True)
        for rev_y, chg, name in pairs:
            if rev_y is not None and chg is not None and -200 <= rev_y <= 200:
                print(f"  {name:<8} {rev_y:>+8.2f}%  {chg:>+6.2f}%")
    
    # PE区间 vs 涨跌幅
    print("\n\n📊 【市盈率区间 vs 涨跌幅】")
    pe_ranges = {"低PE (< 50)": [], "中等PE (50-100)": [], "高PE (100-500)": [], "超高PE (> 500)": [], "亏损 (PE<0)": []}
    for item in combined:
        chg = safe_float(item["chg"])
        pe = safe_float(item["pe"])
        if chg is None:
            continue
        if pe is None:
            pe_ranges["高PE (100-500)"].append(chg)
        elif pe < 0:
            pe_ranges["亏损 (PE<0)"].append(chg)
        elif pe < 50:
            pe_ranges["低PE (< 50)"].append(chg)
        elif pe < 100:
            pe_ranges["中等PE (50-100)"].append(chg)
        elif pe < 500:
            pe_ranges["高PE (100-500)"].append(chg)
        else:
            pe_ranges["超高PE (> 500)"].append(chg)
    
    for r_name, vals in pe_ranges.items():
        if vals:
            avg = sum(vals) / len(vals)
            print(f"  {r_name:<18}: {len(vals)}只, 平均涨跌幅 {avg:+.2f}%")
            print(f"    {', '.join(f'{v:+.1f}%' for v in sorted(vals, reverse=True))}")
    
    # 增长/衰退 vs 涨跌幅
    print("\n\n📊 【净利同比分组 vs 涨跌幅】")
    grow = [(item["name"], safe_float(item["chg"])) for item in combined
            if safe_float(item["profit_yoy"]) is not None and safe_float(item["profit_yoy"]) > 10]
    decline = [(item["name"], safe_float(item["chg"])) for item in combined
               if safe_float(item["profit_yoy"]) is not None and safe_float(item["profit_yoy"]) < -10]
    stable = [(item["name"], safe_float(item["chg"])) for item in combined
              if safe_float(item["profit_yoy"]) is not None and -10 <= safe_float(item["profit_yoy"]) <= 10]
    
    if grow:
        avg_g = sum(v for _, v in grow if v is not None) / len([v for _, v in grow if v is not None])
        print(f"  增长组（净利同比>+10%）: {len(grow)}只, 平均 {avg_g:+.2f}%")
        for n, c in grow:
            if c is not None: print(f"    {n}: {c:+.2f}%")
    if decline:
        avg_d = sum(v for _, v in decline if v is not None) / len([v for _, v in decline if v is not None])
        print(f"  衰退组（净利同比<-10%）: {len(decline)}只, 平均 {avg_d:+.2f}%")
        for n, c in decline:
            if c is not None: print(f"    {n}: {c:+.2f}%")
    if stable:
        avg_s = sum(v for _, v in stable if v is not None) / len([v for _, v in stable if v is not None])
        print(f"  持平组（±10%以内）: {len(stable)}只, 平均 {avg_s:+.2f}%")
        for n, c in stable:
            if c is not None: print(f"    {n}: {c:+.2f}%")
    
    # 总结
    print("\n\n" + "=" * 80)
    print("分析总结")
    print("=" * 80)
    all_chgs = [safe_float(item["chg"]) for item in combined if safe_float(item["chg"]) is not None]
    up_count = sum(1 for c in all_chgs if c > 0)
    down_count = sum(1 for c in all_chgs if c < 0)
    avg_chg_all = sum(all_chgs) / len(all_chgs) if all_chgs else 0
    
    print(f"  板块整体: {len(all_chgs)}只, 上涨{up_count}只, 下跌{down_count}只")
    print(f"  平均涨跌幅: {avg_chg_all:+.2f}%")
    if matched:
        print(f"  题材: {matched}")
    if up_count > down_count * 1.5:
        print(f"  结论: 今日该题材整体强势 (上涨:下跌 = {up_count}:{down_count})")
    elif down_count > up_count * 1.5:
        print(f"  结论: 今日该题材整体弱势 (上涨:下跌 = {up_count}:{down_count})")
    else:
        print(f"  结论: 今日该题材分化明显 (上涨:下跌 = {up_count}:{down_count})")
    print("  数据来源: 东方财富 (push2.eastmoney.com)")


if __name__ == "__main__":
    main()
