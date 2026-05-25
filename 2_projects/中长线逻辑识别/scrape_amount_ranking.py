#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从东方财富条件选股页面爬取成交额排行 Top 50
数据来源: https://xuangu.eastmoney.com/

用法:
    python scrape_amount_ranking.py                    # Top 50
    python scrape_amount_ranking.py --top 100          # Top 100
    python scrape_amount_ranking.py --json --no-save   # JSON 输出，不保存
"""

import argparse, csv, os, sys, time, json
from datetime import datetime
from playwright.sync_api import sync_playwright

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(DATA_DIR, ".ma5_ranking")
os.makedirs(OUT_DIR, exist_ok=True)

DEFAULT_QUERY = "成交额从大到小排名前 50，非 st"


# ---- 页面交互 ----

def _dismiss_popups(page):
    page.evaluate("""() => {
        document.querySelectorAll('[class*="pop"], [class*="shadow"], [class*="mask"], [class*="overlay"], [class*="modal"]').forEach(el => {
            if (el.offsetHeight > 0) { el.remove(); }
        });
    }""")


def _set_page_size(page, target_size=50):
    page.evaluate("""() => {
        const sizer = document.querySelector('.el-pagination__sizes');
        if (!sizer) return;
        const trigger = sizer.querySelector('.el-input__inner');
        if (trigger) trigger.click();
    }""")
    page.wait_for_timeout(800)
    options = page.evaluate("""() => {
        const items = document.querySelectorAll('.el-select-dropdown__item');
        return Array.from(items).map(el => el.innerText.trim());
    }""")
    for opt in options:
        if str(target_size) in opt:
            page.evaluate(f'(t) => {{ const items = document.querySelectorAll(".el-select-dropdown__item"); for (const item of items) {{ if (item.innerText.trim() === t) {{ item.click(); return; }} }} }}', opt)
            page.wait_for_timeout(3000)
            return
    page.evaluate(f"""(size) => {{
        const input = document.querySelector('.el-pagination__sizes input');
        if (!input) return;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, size + '条/页');
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        input.dispatchEvent(new Event('change', {{bubbles: true}}));
    }}""", str(target_size))
    page.wait_for_timeout(3000)


def execute_query(page, query, page_size=50):
    editable = page.locator('[contenteditable="true"]').first
    editable.click(force=True)
    page.wait_for_timeout(300)
    editable.fill(query)
    page.wait_for_timeout(500)
    _dismiss_popups(page)

    page.locator('text=去选股').first.click(force=True, timeout=8000)
    print(f"  已执行查询: {query}")
    page.wait_for_timeout(8000)

    total_count = page.evaluate("""() => {
        const m = document.body.innerText.match(/选出\\s*(\\d+)\\s*只/);
        return m ? parseInt(m[1]) : 0;
    }""")
    if total_count == 0:
        print("  ⚠️  查询未返回结果")
        return 0

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
    """扫描页面所有 table，识别数据表和名称表（成交额版）"""
    return page.evaluate("""() => {
        const result = { nameCols: [], dataCols: [], nameRows: [], dataRows: [], dataDate: '' };
        const allTables = document.querySelectorAll('table');

        allTables.forEach(table => {
            const rawHeaders = Array.from(table.querySelectorAll('th'))
                .map(th => th.innerText.trim().replace(/\\n/g, ' ').replace(/\\s+/g, ' '));
            if (rawHeaders.length === 0 || rawHeaders.every(h => h === '')) return;

            const headerText = rawHeaders.join(' ');
            if (/成交额|换手/i.test(headerText)) {
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

        allTables.forEach(table => {
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            const rows = [];
            tbody.querySelectorAll('tr').forEach(tr => {
                const cells = [];
                tr.querySelectorAll('td').forEach(td => cells.push(td.innerText.trim()));
                if (cells.length >= 3) rows.push(cells);
            });
            if (rows.length < 3) return;

            const firstRow = rows[0];
            const hasStockCode = firstRow.some(c => /^\\d{6}$/.test(c));
            // 成交额特征：大数字 + 含"亿"字
            const hasLargeAmount = firstRow.some(c => {
                if (c.includes('亿')) return true;
                const v = parseFloat(c.replace(/,/g, ''));
                return !isNaN(v) && v > 1e8;
            });
            const hasPercent = firstRow.some(c => /^[+-]?\\d+\\.?\\d*%$/.test(c));

            if (hasStockCode) { result.nameRows = rows; return; }
            if (hasLargeAmount && hasPercent) { result.dataRows = rows; return; }
        });

        return result;
    }""")


def _build_column_index(cols, *keywords_list):
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
    if col_idx is None or row is None:
        return ""
    if col_idx < len(row):
        return row[col_idx]
    return ""


def extract_ranking(page, timeout=15):
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

    data_map = _build_column_index(data_cols,
        ("turnover", ["换手率"]),
        ("volume", ["成交量(股)"]),
        ("amount", ["成交额"]),
    )
    name_map = _build_column_index(name_cols,
        ("rank", ["序号"]),
        ("code", ["代码"]),
        ("name", ["名称"]),
        ("price", ["最新价"]),
        ("chg", ["涨跌幅"]),
    )

    count = min(len(data_rows), len(name_rows)) if name_rows else len(data_rows)
    if count == 0:
        return [], data_date

    results = []
    for i in range(count):
        drow = data_rows[i] if i < len(data_rows) else []
        nrow = name_rows[i] if i < len(name_rows) else []

        entry = {
            "排名": str(i + 1),
            "代码": _safe_get(nrow, name_map.get("code")),
            "名称": _safe_get(nrow, name_map.get("name")),
            "最新价": _safe_get(nrow, name_map.get("price")),
            "涨跌幅": _safe_get(nrow, name_map.get("chg")),
            "成交额": _safe_get(drow, data_map.get("amount")),
        }
        for cn_key, dm_key in [("换手率", "turnover"), ("成交量", "volume")]:
            val = _safe_get(drow, data_map.get(dm_key))
            if val:
                entry[cn_key] = val

        results.append(entry)

    valid = []
    for r in results:
        name = r.get("名称", "")
        if not name or name.isdigit() or "ST" in name:
            continue
        valid.append(r)

    return valid, data_date


# ---- 输出 ----

def save_csv(results, date_str):
    path = os.path.join(OUT_DIR, f"amount_ranking_{date_str}.csv")
    if not results:
        return path
    fields = ["排名", "代码", "名称", "最新价", "涨跌幅", "成交额"]
    extra = ["换手率", "成交量"]
    fields += [f for f in extra if any(f in r for r in results)]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for r in results:
            w.writerow(r)
    return path


def print_table(results, top_n=50):
    n = min(top_n, len(results))
    print(f"\n{'='*78}")
    print(f" 成交额排行 Top {n}  (共{len(results)}条)")
    print(f"{'='*78}")
    hdr = f"{'排名':<6}{'代码':<8}{'名称':<12}{'最新价':<10}{'涨跌幅':<8}{'成交额':<14}"
    print(hdr)
    print("-" * 78)
    for r in results[:n]:
        print(f"{r['排名']:<6}{r['代码']:<8}{r['名称']:<12}"
              f"{r['最新价']:<10}{r['涨跌幅']:<8}{r.get('成交额',''):<14}")


# ---- 主入口 ----

def main():
    parser = argparse.ArgumentParser(description="爬取东方财富成交额排行")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"查询: {args.query}")
    t0 = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto("https://xuangu.eastmoney.com/", wait_until="domcontentloaded", timeout=40000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        total = execute_query(page, args.query, args.page_size)
        if total == 0:
            browser.close()
            print("❌ 查询无结果")
            sys.exit(1)
        print(f"  获取到 {total} 条结果")

        results, data_date = extract_ranking(page, timeout=10)
        browser.close()

    elapsed = time.time() - t0

    if not results:
        print("❌ 未提取到有效数据")
        sys.exit(1)

    data_date = data_date or today_str
    file_date = data_date.replace(".", "-") if "." in data_date else data_date

    if args.json:
        print(json.dumps(results[: args.top], ensure_ascii=False, indent=2))
    else:
        print_table(results, args.top)

    if not args.no_save:
        path = save_csv(results, file_date)
        print(f"\n已保存 {len(results)} 条 → {path}")

    print(f"耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
