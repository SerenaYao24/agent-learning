#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从东方财富条件选股页面爬取 5 日均线角度排行（稳健版）
数据来源: https://xuangu.eastmoney.com/

流程:
  1. 打开选股器首页
  2. 输入自然语言查询条件
  3. 点击「去选股」执行
  4. 设置每页条数（一次拉取全部）
  5. 按表头文本匹配提取数据列

用法:
    python scrape_ma5_ranking.py
        # 默认: "五日均线角度从大到小排名前 200，非 st"

    python scrape_ma5_ranking.py --query "五日均线角度从大到小排名前 100"
        # 自定义查询条件

    python scrape_ma5_ranking.py --top 30
        # 只显示/保存前 30 条

    python scrape_ma5_ranking.py --page-size 200
        # 每页拉取条数（默认=期望总数，一次性拉完）

    python scrape_ma5_ranking.py --json --no-save
        # JSON 输出，不保存文件
"""

import argparse, csv, os, sys, time, json, re
from datetime import datetime
from playwright.sync_api import sync_playwright

if sys.version_info[0] < 3:
    sys.stderr.write("错误：请使用 python3 运行此脚本，例如: python3 scrape_ma5_ranking.py\n")
    sys.exit(1)

print("⚠️  请确保已关闭 VPN，否则爬虫可能无法正常工作\n")

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(DATA_DIR, ".ma5_ranking")
os.makedirs(OUT_DIR, exist_ok=True)

DEFAULT_QUERY = "五日均线角度从大到小排名前 200，非 st"


# ---- 页面交互 ----

def _dismiss_popups(page):
    """移除页面弹窗遮罩"""
    page.evaluate("""() => {
        document.querySelectorAll('[class*="pop"], [class*="shadow"], [class*="mask"], [class*="overlay"], [class*="modal"]').forEach(el => {
            if (el.offsetHeight > 0) { el.remove(); }
        });
    }""")


def _set_page_size(page, target_size=200):
    """设置分页组件的每页条数"""
    # 打开 Element-UI 分页下拉
    page.evaluate("""() => {
        const sizer = document.querySelector('.el-pagination__sizes');
        if (!sizer) return;
        const trigger = sizer.querySelector('.el-input__inner');
        if (trigger) trigger.click();
    }""")
    page.wait_for_timeout(800)

    # 找目标选项并点击
    options = page.evaluate("""() => {
        const items = document.querySelectorAll('.el-select-dropdown__item');
        return Array.from(items).map(el => el.innerText.trim());
    }""")

    for opt in options:
        if str(target_size) in opt:
            page.evaluate(f'(t) => {{ const items = document.querySelectorAll(".el-select-dropdown__item"); for (const item of items) {{ if (item.innerText.trim() === t) {{ item.click(); return; }} }} }}', opt)
            page.wait_for_timeout(3000)
            return

    # 兜底：如果没找到精确匹配，尝试 JS 直接设置
    page.evaluate("""() => {
        const input = document.querySelector('.el-pagination__sizes input');
        if (!input) return;
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeInputValueSetter.call(input, '200条/页');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    page.wait_for_timeout(3000)


def execute_query(page, query, page_size=200):
    """
    在选股器首页执行查询，返回结果行数。
    自动设置分页大小以获取全部数据。
    """
    # 输入查询条件
    editable = page.locator('[contenteditable="true"]').first
    editable.click(force=True)
    page.wait_for_timeout(300)
    editable.fill(query)
    page.wait_for_timeout(500)

    _dismiss_popups(page)

    # 点击「去选股」
    page.locator('text=去选股').first.click(force=True, timeout=8000)
    print(f"  已执行查询: {query}")
    page.wait_for_timeout(8000)

    # 检查结果数（兼容新旧两种文案）
    total_count = page.evaluate("""() => {
        const text = document.body.innerText;
        // 新版："显示股票数量 共 200 只 股票"
        let m = text.match(/共\\s*(\\d+)\\s*只/);
        if (m) return parseInt(m[1]);
        // 旧版："选出 200 只"
        m = text.match(/选出\\s*(\\d+)\\s*只/);
        return m ? parseInt(m[1]) : 0;
    }""")
    if total_count == 0:
        print("  ⚠️  查询未返回结果")
        return 0

    # 如果当前页不够显示全部，调大分页
    current_rows = page.evaluate("""() => {
        const bodies = document.querySelectorAll('.el-table__body');
        return bodies.length > 0 ? bodies[0].querySelectorAll('tr').length : 0;
    }""")

    if current_rows < total_count and total_count <= 500:
        target = min(total_count, page_size)
        print(f"  当前显示 {current_rows}/{total_count} 条，调整为 {target} 条/页")
        _set_page_size(page, target)

    return total_count


# ---- 数据提取 ----

def _classify_tables(page):
    """扫描页面所有 table，识别数据表和名称表"""
    return page.evaluate("""() => {
        const result = { dataCols: [], nameCols: [], dataRows: [], nameRows: [], dataDate: '' };
        const allTables = document.querySelectorAll('table');

        // 步骤1: 遍历表头识别列映射（保留空列保证索引对齐）
        allTables.forEach(table => {
            const rawHeaders = Array.from(table.querySelectorAll('th'))
                .map(th => th.innerText.trim().replace(/\\n/g, ' ').replace(/\\s+/g, ' '));
            if (rawHeaders.length === 0 || rawHeaders.every(h => h === '')) return;
            
            const headerText = rawHeaders.join(' ');
            if (/角度|均线|换手/i.test(headerText)) {
                result.dataCols = rawHeaders;
                for (const h of rawHeaders) {
                    const m = h.match(/(\\d{4}\\.\\d{2}\\.\\d{2})/);
                    if (m) { result.dataDate = m[1]; break; }
                }
            }
            if (/代码|名称|序号/i.test(headerText)) {
                result.nameCols = rawHeaders;
            }
        });

        // 步骤2: 遍历表体匹配数据
        allTables.forEach(table => {
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            const rows = [];
            tbody.querySelectorAll('tr').forEach(tr => {
                const cells = [];
                tr.querySelectorAll('td').forEach(td => cells.push(td.innerText.trim()));
                if (cells.length >= 3) rows.push(cells);
            });
            if (rows.length < 5) return;

            const firstRow = rows[0];
            const hasStockCode = firstRow.some(c => /^\\d{6}$/.test(c));
            const hasAngleValues = firstRow.some(c => {
                const v = parseFloat(c);
                return !isNaN(v) && v > 10 && v < 90 && c.includes('.');
            });
            const hasPercent = firstRow.some(c => /^[+-]?\\d+\\.?\\d*%$/.test(c));

            // 名称表优先（含6位股票代码）
            if (hasStockCode) { result.nameRows = rows; return; }
            // 数据表（含角度值+百分比）
            if (hasAngleValues && hasPercent) { result.dataRows = rows; return; }
        });

        return result;
    }""")


def _build_column_index(cols, *keywords_list):
    """按列名核心段精确匹配列索引。未匹配到返回 None。"""
    mapping = {}
    for key, keywords in keywords_list:
        idx = None
        for i, col_name in enumerate(cols):
            if not col_name:
                continue
            core = col_name.replace("\n", " ").split()[0]
            if any(kw == core for kw in keywords):
                idx = i
                break
        mapping[key] = idx
    return mapping


def _safe_get(row, col_idx):
    """安全取列值，索引为 None 或越界返回空字符串"""
    if col_idx is None or row is None:
        return ""
    if col_idx < len(row):
        return row[col_idx]
    return ""


def extract_ranking(page, timeout=15):
    """主提取函数"""
    try:
        page.wait_for_selector("table tbody tr", timeout=timeout * 1000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    tables = _classify_tables(page)
    data_rows = tables.get("dataRows", [])
    name_rows = tables.get("nameRows", [])
    data_cols = tables.get("dataCols", [])
    name_cols = tables.get("nameCols", [])
    data_date = tables.get("dataDate", "")

    # 按表头匹配列索引（不存在的列返回 None，后续跳过）
    data_map = _build_column_index(data_cols,
        ("angle", ["日线周期5日均线角度"]),
        ("ma5", ["5日均线"]),
        ("turnover", ["换手率"]),
        ("volume", ["成交量(股)"]),
        ("amount", ["成交额"]),
        ("st", ["是否ST"]),  # 仅在含ST过滤的查询中出现
    )
    name_map = _build_column_index(name_cols,
        ("rank", ["序号"]),
        ("code", ["代码"]),
        ("name", ["名称"]),
        ("price", ["最新价"]),
        ("chg", ["涨跌幅"]),
    )

    angle_col = data_map.get("angle")
    name_col = name_map.get("name")
    code_col = name_map.get("code")

    # 兜底：从数据行中自动识别角度列
    if angle_col is None and data_rows:
        for i, cell in enumerate(data_rows[0]):
            try:
                v = float(cell)
                if 10 < v < 90 and "." in cell:
                    angle_col = i
                    break
            except ValueError:
                pass

    count = min(len(data_rows), len(name_rows)) if name_rows else len(data_rows)
    if count == 0:
        return [], data_date

    results = []
    for i in range(count):
        drow = data_rows[i] if i < len(data_rows) else []
        nrow = name_rows[i] if i < len(name_rows) else []

        entry = {
            "排名": str(i + 1),
            "代码": _safe_get(nrow, code_col),
            "名称": _safe_get(nrow, name_col),
            "角度": _safe_get(drow, angle_col),
            "最新价": _safe_get(nrow, name_map.get("price")),
            "涨跌幅": _safe_get(nrow, name_map.get("chg")),
        }
        # MA5 列不一定存在（取决于查询条件），有则加
        ma5_val = _safe_get(drow, data_map.get("ma5"))
        if ma5_val:
            entry["MA5"] = ma5_val
        # 可选列（不含是否ST）
        for cn_key, dm_key in [("换手率", "turnover"), ("成交量", "volume"), ("成交额", "amount")]:
            val = _safe_get(drow, data_map.get(dm_key))
            if val:
                entry[cn_key] = val

        results.append(entry)

    # 数据验证：角度范围 + 名称非空 + 过滤 ST
    valid = []
    for r in results:
        name = r.get("名称", "")
        if not name or name.isdigit():
            continue
        if "ST" in name:
            continue  # 兜底过滤（查询条件已排除，但以防万一）
        try:
            angle_val = float(r.get("角度", ""))
            if not (0 < angle_val <= 90):
                continue
        except (ValueError, TypeError):
            continue
        valid.append(r)

    return valid, data_date


# ---- 输出 ----

def save_csv(results, date_str):
    path = os.path.join(OUT_DIR, f"ranking_{date_str}.csv")
    if not results:
        return path
    # 按数据实际有的列输出（MA5可能不存在）
    base_fields = ["排名", "代码", "名称", "角度", "最新价", "涨跌幅"]
    extra_fields = ["MA5", "换手率", "成交量", "成交额"]
    fields = base_fields + [f for f in extra_fields if any(f in r for r in results)]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for r in results:
            w.writerow(r)
    return path


def print_table(results, top_n=50):
    n = min(top_n, len(results))
    date_info = results[0].get("_date", "") if results else ""
    has_ma5 = any("MA5" in r for r in results)
    print(f"\n{'='*78}")
    print(f" 5 日均线角度排行 Top {n}  (共{len(results)}条)  {date_info}")
    print(f"{'='*78}")
    if has_ma5:
        hdr = f"{'排名':<6}{'代码':<8}{'名称':<12}{'角度°':<8}{'MA5':<10}{'最新价':<10}{'涨跌幅':<8}"
    else:
        hdr = f"{'排名':<6}{'代码':<8}{'名称':<12}{'角度°':<8}{'最新价':<10}{'涨跌幅':<8}"
    print(hdr)
    print("-" * 78)
    for r in results[:n]:
        if has_ma5:
            print(f"{r['排名']:<6}{r['代码']:<8}{r['名称']:<12}"
                  f"{r['角度']:<8}{r.get('MA5',''):<10}{r['最新价']:<10}{r['涨跌幅']:<8}")
        else:
            print(f"{r['排名']:<6}{r['代码']:<8}{r['名称']:<12}"
                  f"{r['角度']:<8}{r['最新价']:<10}{r['涨跌幅']:<8}")


# ---- 主入口 ----

def main():
    parser = argparse.ArgumentParser(description="爬取东方财富 5 日均线角度排行（稳健版）")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY,
                        help=f"查询条件 (默认: {DEFAULT_QUERY})")
    parser.add_argument("--top", type=int, default=50,
                        help="显示/保存前 N 条 (默认 50)")
    parser.add_argument("--page-size", type=int, default=200,
                        help="每页拉取条数 (默认 200)")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-save", action="store_true", help="不保存 CSV")
    args = parser.parse_args()

    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"查询: {args.query}")
    t0 = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 打开选股器首页（domcontentloaded 可能因慢资源超时，改用宽松策略）
        try:
            page.goto("https://xuangu.eastmoney.com/", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            pass  # 超时也不中断，页面可能已部分加载
        page.wait_for_timeout(5000)

        # 执行查询
        total = execute_query(page, args.query, args.page_size)
        if total == 0:
            browser.close()
            print("❌ 查询无结果")
            sys.exit(1)
        print(f"  获取到 {total} 条结果")

        # 提取数据
        results, data_date = extract_ranking(page, timeout=10)
        browser.close()

    elapsed = time.time() - t0

    if not results:
        print("❌ 未提取到有效数据")
        sys.exit(1)

    data_date = data_date or today_str
    # 统一日期格式：将 "2026.05.22" 转为 "2026-05-22"
    file_date = data_date.replace(".", "-") if "." in data_date else data_date
    for r in results:
        r["_date"] = data_date

    if args.json:
        print(json.dumps(results[: args.top], ensure_ascii=False, indent=2))
    else:
        print_table(results, args.top)

    if not args.no_save:
        path = save_csv(results, file_date)
        print(f"\n已保存 {len(results)} 条 → {path}")

    angles = [float(r["角度"]) for r in results if r["角度"]]
    if angles:
        print(f"统计: 最高 {max(angles):.1f}°  |  最低 {min(angles):.1f}°  |  平均 {sum(angles)/len(angles):.1f}°  |  耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
