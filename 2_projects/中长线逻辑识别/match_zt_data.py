#!/usr/bin/env python3
"""涨停数据匹配自选股：读 log/{date}_limit_up_data.txt，匹配后写入 interest_stock_backup.md"""
import re, os, sys
from collections import OrderedDict
from datetime import date

from _topic_utils import parse_topic_header, format_topic_header

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_DIR, "log")
INTEREST_PATH = os.path.join(PROJECT_DIR, "interest_stock.md")

# ============== Concept Group → Sector Priority Mapping ==============
# 母表标题优先：直接 1:1 映射，忽略异动原因
CONCEPT_GROUP_MAP = {
    '工业气体': '半导体材料',
    '地产产业链': '地产',
    'PCB产业链': 'PCB',
    '算力租赁': '算力租赁',
    '光伏': '光伏',
    'AI应用': 'AI 应用',
    '大消费': '消费',
    '机器人': '机器人',
    '低空经济': '低空经济',
    '医药': '生物医药',
    '电力': '电力',
}

# 大类标题→允许的板块白名单（约束异动原因匹配范围）
BROAD_GROUP_SECTORS = {
    '算力/半导体产业链': [
        'CPO', 'MLCC 电容', '电子布', '光通信', '磷化铟', '光纤',
        'PCB 钻针', 'PCB 铜箔/覆铜板', 'PCB', '液冷', '先进封装', '玻璃基板',
        '电阻电容', '功率半导体', '半导体设备', '半导体材料', '碳化硅',
        '存储', '国产芯片', '半导体洁净室', '薄膜铌酸锂',
        'AI 应用', '算力租赁', '算力调度/算力工厂', 'CPU', 'AI 消费电子',
        'AIDC-电源/发电机', 'AIDC-变压器',
    ],
}
SECTOR_KEYWORDS = OrderedDict([
    ('CPO', ['cpo', '光引擎']),
    ('MLCC 电容', ['mlcc', '离型膜']),
    ('电子布', ['电子布', '玻纤布', '玻璃纤维布', '芳纶']),
    ('光通信', ['光模块', '光通信', '薄膜铌酸锂', '滤光片']),
    ('磷化铟', ['磷化铟']),
    ('光纤', ['光纤', '四氯化锗', '长飞']),
    ('PCB 钻针', ['钻针']),
    ('PCB 铜箔/覆铜板', ['覆铜板', '铜箔', 'ccl']),
    ('PCB', ['pcb', 'hdi', '印制电路板', 'cbf', '玻纤', '玻璃纤维', 'msap', '载板', '陶瓷方案']),
    ('液冷', ['液冷', '冷却液', '氟化冷却', '散热']),
    ('先进封装', ['先进封装', '封装材料', '顺酐酸酐']),
    ('玻璃基板', ['玻璃基板', '硼硅']),
    ('电阻电容', ['电容', '被动元件', '电极箔', '超级电容', 'mlpc', '薄膜电容器']),
    ('功率半导体', ['功率半导体', 'igbt', 'mosfet', '功率器件']),
    ('半导体设备', ['半导体设备', '晶圆', '刻蚀', '封装检测', '半导体测试', '光刻机', '泵阀', '超洁净']),
    ('半导体材料', ['光刻胶', '电子气体', '特种气体', '特殊气体', '电子化学品', '硅烷', '有机硅', '电子特气', '光刻气', '靶材']),
    ('碳化硅', ['碳化硅', 'sic']),
    ('机器人', ['机器人', '人形机器人', '优必选', '电子皮肤', '宇树', 'peek', '机器狗']),
    ('存储', ['存储', '长鑫', 'hbm', 'dram', 'nand']),
    ('算力租赁', ['算力租赁']),
    ('算力调度/算力工厂', ['算力调度', '算力服务器', 'gpu']),
    ('电力', ['电力', '储能', '电网', '电气', '热电', '特高压', '发电', '充电桩', '煤电', '煤', 'hvdc', '算力电源']),
    ('小金属/贵金属', ['钨', '锡', '有色', '黄金', '铜', '锗', '钽', '铟', '铌', '铪', '锶', '金属', '铁矿', '矿产']),
    ('AI 消费电子', ['ar眼镜', 'ar', 'miniled', 'ai眼镜']),
    ('AI 应用', ['ai应用', 'ai服务器', '推理服务器', 'ai智能体', 'ai算力', 'ai数据中心', 'aipc', '端侧ai', 'vr', 'ai语料', 'deepseek', '大模型', '混元', 'ai电商', 'ai音视频', 'ai影视']),
    ('低空经济', ['低空经济', '无人机', '飞行汽车', 'evtol']),
    ('商业航天', ['航天', '商业航天', '卫星', '太空算力']),
    ('生物医药', ['创新药', 'cro', '仿制药', '原料药', '化学制药', '疫苗', '干细胞', '医疗器械', '体外诊断', '血液透析', '腹膜透析', '口腔']),
    ('消费', ['服饰', '服装', '白酒', '百货', '零售', '预制菜', '旅游', '食品', '啤酒', '文旅', '豆制品']),
    ('国产芯片', ['芯片', '射频', '射频芯片', '存储芯片', '光芯片']),
    ('地产', ['地产', '房地产', '地板', '物业管理']),
    ('燃气/氢能', ['氢能', '氢燃料', '氢氟酸', '制氢']),
    ('核电', ['核电']),
    ('AIDC-电源/发电机', ['发电机', '备用电源']),
    ('AIDC-变压器', ['变压器', 'sst']),
    ('电池/储能', ['电池', '固态电池', '锂电池', '钠电池', '盐湖提锂', '新能源', '磷化工']),
    ('稀土', ['稀土']),
    ('油运', ['油运']),
    ('CPU', ['cpu']),
    ('半导体洁净室', ['洁净室']),
    ('物理AI', ['物理ai']),
    ('光伏', ['光伏', '太阳能', '钙钛矿', 'tco']),
])

def _match_by_reason(reason, allowed_sectors=None):
    """Match sector by reason keywords, optionally constrained to allowed_sectors."""
    reason_lower = reason.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if allowed_sectors is not None and sector not in allowed_sectors:
            continue
        for kw in keywords:
            if kw.lower() in reason_lower:
                return sector
    return None

def match_sector(reason, concept_group=''):
    """
    Sector matching with concept group priority:
    1. Direct 1:1 concept_group → sector mapping (ignores reason)
    2. Broad concept_group → constrained reason matching (whitelist)
    3. Fallback: unconstrained reason matching (for '其他概念' etc.)
    """
    cg = concept_group.strip() if concept_group else ''
    
    # Step 1: Direct concept group → sector
    if cg in CONCEPT_GROUP_MAP:
        return CONCEPT_GROUP_MAP[cg]
    
    # Step 2: Broad concept group → constrained reason matching
    if cg in BROAD_GROUP_SECTORS:
        sector = _match_by_reason(reason, BROAD_GROUP_SECTORS[cg])
        if sector:
            return sector
        # If no match within constraint, fall through to unconstrained
    
    # Step 3: Default unconstrained reason matching
    return _match_by_reason(reason)

def parse_interest_stock(filepath):
    """Parse interest_stock.md -> {section: [(line, name, desc)]}, set of names, {section: note}"""
    with open(filepath, encoding='utf-8') as f:
        text = f.read()
    sections = OrderedDict()
    section_notes = {}  # topic_name → topic_note
    all_names = set()
    current = None
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('# '):
            topic_name, topic_note = parse_topic_header(s)
            current = topic_name
            sections.setdefault(current, [])
            if topic_note:
                section_notes[current] = topic_note
            continue
        if current is not None and s:
            name = s.split('（')[0].split('(')[0].strip()
            desc = ''
            if '（' in s:
                desc = s[s.index('（'):]
            all_names.add(name)
            sections[current].append((s, name, desc))
    return sections, all_names, section_notes

def load_zt_data(filepath):
    """Load parsed ZT data from log file."""
    stocks = []
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.strip().split('\t')
            if len(parts) >= 14 and parts[0] != '股票名称':
                stocks.append({
                    'name': parts[0], 'code': parts[1], 'change': parts[2],
                    'ban_shu': parts[4], 'ban_xing': parts[6],
                    'first_seal': parts[7], 'final_seal': parts[8],
                    'amount': parts[9], 'flow': parts[10],
                    'turnover': parts[12], 'reason': parts[13],
                    'concept_group': parts[15] if len(parts) > 15 else '',
                })
    return stocks

def main():
    today = date.today().strftime("%Y-%m-%d")
    data_file = os.path.join(LOG_DIR, f"{today}_limit_up_data.txt")
    
    if not os.path.exists(data_file):
        print(f"ERROR: 数据文件不存在: {data_file}")
        print("请先运行 scrape_zt_data.py")
        sys.exit(1)
    
    print(f"1. 加载数据: {data_file}")
    zt_stocks = load_zt_data(data_file)
    print(f"   涨停 {len(zt_stocks)} 只")
    
    print(f"2. 加载自选股: {INTEREST_PATH}")
    wl_sections, wl_names, section_notes = parse_interest_stock(INTEREST_PATH)
    print(f"   {len(wl_names)} 只标的, {len(wl_sections)} 个板块")
    
    # Classify
    in_wl = [s for s in zt_stocks if s['name'] in wl_names]
    new_stocks = [s for s in zt_stocks if s['name'] not in wl_names]
    print(f"3. 已在自选: {len(in_wl)}, 新标的: {len(new_stocks)}")
    
    # Build a map of existing stock -> section + description for append
    stock_section_map = {}  # name -> (section_name, desc)
    for sec_name, entries in wl_sections.items():
        if sec_name == '未匹配题材':
            continue
        for entry_line, name, desc in entries:
            if name not in stock_section_map:  # first occurrence wins
                stock_section_map[name] = (sec_name, desc, entry_line)
    
    # Match new stocks
    matched = OrderedDict()
    unmatched = []
    for s in new_stocks:
        sector = match_sector(s['reason'], s.get('concept_group', ''))
        if sector:
            matched.setdefault(sector, []).append(s)
        else:
            unmatched.append(s)
    
    # ===== Build interest_stock_backup.md =====
    output = []
    written_names = set()
    
    # Build updated descriptions map for existing stocks
    # name -> new desc (with appended 异动原因)
    updated_descs = {}
    for s in in_wl:
        old_desc = ''
        if s['name'] in stock_section_map:
            old_desc = stock_section_map[s['name']][1]
        
        new_reason = s['reason']
        
        if old_desc:
            # Check if new reason's keywords already covered by existing desc (avoid near-duplicate appends)
            new_kws = set(k.strip() for k in new_reason.replace('+', ' ').replace('（', ' ').replace('）', ' ').split() if len(k.strip()) > 1)
            existing_text = old_desc.replace('（', ' ').replace('）', ' ')
            covered = all(kw in existing_text for kw in new_kws) if new_kws else False
            
            if not covered and new_reason not in old_desc:
                if old_desc.endswith('）'):
                    new_desc = f"{old_desc[:-1]}；{new_reason}）"
                else:
                    new_desc = f"（{new_reason}）"
            else:
                new_desc = old_desc
        else:
            new_desc = f"（{new_reason}）"
        updated_descs[s['name']] = new_desc
    
    # Write regular sections
    for sec_name, entries in wl_sections.items():
        if sec_name == '未匹配题材':
            continue
        output.append(format_topic_header(sec_name, section_notes.get(sec_name, '')))
        for entry_line, name, desc in entries:
            if name not in written_names:
                if name in updated_descs:
                    # Replace with updated description
                    output.append(f"{name}{updated_descs[name]}")
                else:
                    output.append(entry_line)
                written_names.add(name)
        
        # Add new matched stocks to this section
        if sec_name in matched:
            for s in matched[sec_name]:
                if s['name'] not in written_names:
                    output.append(f"{s['name']}（{s['reason']}）")
                    written_names.add(s['name'])
        
        output.append('')
    
    # Add new sections not in WL
    for sec_name in matched:
        if sec_name not in wl_sections:
            # 新题材来自 SECTOR_KEYWORDS，没有备注
            output.append(format_topic_header(sec_name))
            for s in matched[sec_name]:
                if s['name'] not in written_names:
                    output.append(f"{s['name']}（{s['reason']}）")
                    written_names.add(s['name'])
            output.append('')
    
    # Unmatched section
    orig_unmatched = wl_sections.get('未匹配题材', [])
    output.append(format_topic_header('未匹配题材'))
    for entry_line, name, desc in orig_unmatched:
        if entry_line.strip() and name not in written_names:
            output.append(entry_line.strip())
            written_names.add(name)
    for s in unmatched:
        output.append(f"{s['name']}（{s['reason']}）")
    output.append('')
    
    # Write output
    backup_path = os.path.join(PROJECT_DIR, "interest_stock_backup.md")
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output))
    
    # ===== Verification =====
    print(f"\n4. 写入: {backup_path}")
    text = '\n'.join(output)
    
    errors = []
    # Format check
    sections = re.split(r'\n# ', text)
    for sec in sections:
        body = [l for l in sec.strip().split('\n') if not l.startswith('#')]
        blanks = [i for i,l in enumerate(body) if l.strip()=='' and i<len(body)-1 and body[i+1].strip()!='']
        if blanks:
            errors.append(f'Format: blank in "{sec.split(chr(10))[0][:30]}"')
    
    # Duplicate names
    all_lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('#')]
    name_counts = {}
    for l in all_lines:
        n = l.split('（')[0].split('(')[0].strip()
        name_counts[n] = name_counts.get(n, 0) + 1
    for n, c in name_counts.items():
        if c > 1:
            errors.append(f'DUP: {n} x{c}')
    
    # Duplicate sections
    headers = re.findall(r'^# (.+)$', text, re.MULTILINE)
    from collections import Counter
    for h, c in Counter(headers).items():
        if c > 1:
            errors.append(f'DUP SECT: {h}')
    
    if errors:
        print(f"\n❌ {len(errors)} 问题:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("✅ 验证通过")
    
    # Summary
    total_new = sum(len(v) for v in matched.values())
    print(f"\n=== 摘要 ===")
    print(f"已在自选(异动原因已追加): {len(in_wl)}")
    print(f"新增匹配: {total_new}")
    print(f"未匹配: {len(unmatched)}")
    for sec, stks in matched.items():
        print(f"  [{sec}] +{len(stks)}: {', '.join(s['name'] for s in stks)}")
    if unmatched:
        print(f"  [未匹配] {len(unmatched)}: {', '.join(s['name'] for s in unmatched)}")
        print(f"\n=== 🔍 模型审查（未匹配标的） ===")
        print("请模型逐条审查异动原因，如有推荐板块，直接移至对应 section，禁止输出【建议】标注")
        print()
        for s in unmatched:
            print(f"  - {s['name']} | 异动原因: {s['reason']}")


if __name__ == "__main__":
    main()
