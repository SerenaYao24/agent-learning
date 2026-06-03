#!/usr/bin/env python3
"""从短线侠 涨停表现 页面抓取涨停数据，输出到 log/{date}_limit_up_data.txt"""
import subprocess, re, sys, os
from datetime import date

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)

def run_agent(cmd, timeout=30):
    """Run an agent-browser command, return stdout."""
    r = subprocess.run(f"agent-browser {cmd}", shell=True, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and "already" not in r.stderr:
        # Don't fail on "already open" messages
        pass
    return r.stdout

def main():
    today = date.today().strftime("%Y-%m-%d")
    out_path = os.path.join(LOG_DIR, f"{today}_limit_up_data.txt")
    
    if os.path.exists(out_path):
        print(f"数据已存在: {out_path}")
        return out_path
    
    print("1. 打开短线侠...")
    run_agent("open https://duanxianxia.com/web/main")
    run_agent("wait --load networkidle")
    
    # Find 涨停表现 link ref
    snap = run_agent("snapshot -i")
    m = re.search(r'link "涨停表现" \[ref=(e\d+)\]', snap)
    if not m:
        print("ERROR: 找不到涨停表现链接")
        sys.exit(1)
    zt_ref = m.group(1)
    
    print(f"2. 点击涨停表现 ({zt_ref})...")
    run_agent(f"click {zt_ref}")
    run_agent("wait 3000")
    
    # Find "全部展开" button
    snap2 = run_agent("snapshot -i")
    m2 = re.search(r'button "全部展开" \[ref=(e\d+)\]', snap2)
    if m2:
        print(f"3. 点击全部展开 ({m2.group(1)})...")
        run_agent(f"click {m2.group(1)}")
        run_agent("wait 3000")
    else:
        print("3. 未找到全部展开按钮，尝试直接提取...")
    
    # Extract data from iframe[7] (the expanded concept-grouped table)
    print("4. 提取数据...")
    raw = run_agent('eval \'document.querySelectorAll("iframe")[7].contentDocument.body.innerText\'', timeout=60)
    
    # Parse: convert literal \n and \t to actual chars
    data = raw.strip().strip('"')
    data = data.replace('\\n', '\n').replace('\\t', '\t')
    
    if not data or len(data) < 500:
        # Try alternative iframe index
        for idx in [7, 8, 9]:
            raw2 = run_agent(f'eval \'document.querySelectorAll("iframe")[{idx}].contentDocument.body.innerText\'', timeout=60)
            d2 = raw2.strip().strip('"').replace('\\n', '\n').replace('\\t', '\t')
            if len(d2) > 1000 and '股票名称' in d2:
                data = d2
                break
    
    if not data or '股票名称' not in data:
        print("ERROR: 未能提取到涨停数据")
        sys.exit(1)
    
    # Parse structured stock data
    lines = data.split('\n')
    stocks = []
    current_concept = ''
    in_table = False
    
    for line in lines:
        line = line.strip("[]' ")
        if not line:
            continue
        
        # Concept header: "概念名（+XX.XX%）"
        if re.match(r'^[\u4e00-\u9fff/\-·a-zA-Z]+[（(][\d.\-+]+%[）)]$', line):
            current_concept = re.sub(r'[（(][\d.\-+]+%[）)]', '', line).strip()
            in_table = False
            continue
        
        # Table header
        if '股票名称' in line:
            in_table = True
            continue
        
        # Data row
        if in_table and '\t' in line:
            parts = line.split('\t')
            if len(parts) >= 14:
                stocks.append({
                    'name': parts[0].strip(),
                    'code': parts[1].strip(),
                    'change': parts[2].strip(),
                    'prev_close': parts[3].strip(),
                    'ban_shu': parts[4].strip(),
                    'lian_ban': parts[5].strip(),
                    'ban_xing': parts[6].strip(),
                    'first_seal': parts[7].strip(),
                    'final_seal': parts[8].strip(),
                    'amount': parts[9].strip(),
                    'flow': parts[10].strip(),
                    'total_mv': parts[11].strip(),
                    'turnover': parts[12].strip(),
                    'reason': parts[13].strip(),
                    'longhu': parts[14].strip() if len(parts) > 14 else '',
                    'concept_group': current_concept,
                })
    
    # Write output
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f"# 涨停表现 {today}\n")
        f.write(f"# 共 {len(stocks)} 只\n\n")
        f.write("股票名称\t代码\t涨幅\t昨收\t板数\t连板数\t板形\t首次封板\t最终封板\t成交额\t实际流通\t总市值\t换手率\t异动原因\t龙虎榜\t短线侠概念分组\n")
        for s in stocks:
            f.write(f"{s['name']}\t{s['code']}\t{s['change']}\t{s['prev_close']}\t{s['ban_shu']}\t{s['lian_ban']}\t{s['ban_xing']}\t{s['first_seal']}\t{s['final_seal']}\t{s['amount']}\t{s['flow']}\t{s['total_mv']}\t{s['turnover']}\t{s['reason']}\t{s['longhu']}\t{s['concept_group']}\n")
    
    print(f"5. 保存: {out_path} ({len(stocks)} 只涨停)")
    return out_path

if __name__ == "__main__":
    main()
