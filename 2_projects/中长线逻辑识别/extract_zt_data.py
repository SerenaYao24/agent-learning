#!/usr/bin/env python3
"""直接从 playwright-cli eval 输出中提取涨停数据并保存"""
import subprocess, re, sys, os
from datetime import date
from collections import Counter

PROJECT_DIR = "/Users/heloise/Desktop/agent/2_projects/中长线逻辑识别"
LOG_DIR = os.path.join(PROJECT_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)

def main():
    today = date.today().strftime("%Y-%m-%d")
    out_path = os.path.join(LOG_DIR, f"{today}_limit_up_data.txt")
    
    if os.path.exists(out_path):
        print(f"数据已存在: {out_path}")
        return out_path
    
    # 从 playwright-cli 获取数据
    r = subprocess.run(
        ['playwright-cli', 'eval', 'document.querySelectorAll("iframe")[7].contentDocument.body.innerText'],
        capture_output=True, text=True, timeout=120, cwd=PROJECT_DIR
    )
    
    raw = r.stdout
    
    # 提取 ### Result 后的内容
    data = ""
    in_result = False
    for line in raw.split('\n'):
        if line.startswith('### Result'):
            in_result = True
            rest = line[len('### Result'):].strip()
            if rest:
                data += rest + '\n'
            continue
        if in_result:
            if line.startswith('### '):
                break
            data += line + '\n'
    
    data = data.strip().strip('"')
    
    # 转义: \n → 换行符, \t → 制表符
    data = data.replace('\\n', '\n').replace('\\t', '\t')
    
    if not data:
        print("ERROR: 未能提取数据")
        print("RAW:", raw[:500])
        sys.exit(1)
    
    # 解析结构化数据
    lines = data.split('\n')
    stocks = []
    current_concept = ''
    in_table = False
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # 概念分组标题: "概念名（+XX.XX%）"
        m = re.match(r'^([\u4e00-\u9fff/\-·a-zA-Z0-9]+)[（(][\d.\-+]+%[）)]$', line)
        if m:
            current_concept = m.group(1).strip()
            in_table = False
            continue
        
        # 表头
        if '股票名称' in line:
            in_table = True
            continue
        
        # 数据行
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
    
    if not stocks:
        print("ERROR: 未能解析到股票数据")
        print("DATA[:500]:", data[:500])
        sys.exit(1)
    
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f"# 涨停表现 {today}\n")
        f.write(f"# 共 {len(stocks)} 只\n\n")
        f.write("股票名称\t代码\t涨幅\t昨收\t板数\t连板数\t板形\t首次封板\t最终封板\t成交额\t实际流通\t总市值\t换手率\t异动原因\t龙虎榜\t短线侠概念分组\n")
        for s in stocks:
            f.write(f"{s['name']}\t{s['code']}\t{s['change']}\t{s['prev_close']}\t{s['ban_shu']}\t{s['lian_ban']}\t{s['ban_xing']}\t{s['first_seal']}\t{s['final_seal']}\t{s['amount']}\t{s['flow']}\t{s['total_mv']}\t{s['turnover']}\t{s['reason']}\t{s['longhu']}\t{s['concept_group']}\n")
    
    print(f"保存: {out_path} ({len(stocks)} 只涨停)")
    
    groups = Counter(s['concept_group'] for s in stocks)
    print("\n分组统计:")
    for g, c in groups.most_common():
        print(f"  {g}: {c}只")
    
    return out_path

if __name__ == "__main__":
    main()
