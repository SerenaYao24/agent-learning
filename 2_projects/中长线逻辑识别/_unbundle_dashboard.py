#!/usr/bin/env python3
"""v3: 提取内嵌数据到文件，HTML 改为按需加载"""
import re, json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "dashboard.html")
INDEX_DIR = os.path.join(BASE, ".index_data")
os.makedirs(INDEX_DIR, exist_ok=True)

with open(HTML, encoding="utf-8") as f:
    html = f.read()

orig_size = len(html)

def find_js_value(text, start_pos):
    """找完整 JS 值（对象或数组），返回 (start, end)"""
    depth = 0
    in_str = False
    quote = ''
    escape = False
    for i in range(start_pos, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c in ('"', "'") and not in_str:
            quote = c
            in_str = True
        elif in_str and c == quote:
            in_str = False
        elif not in_str:
            if c in ('[', '{'):
                depth += 1
            elif c in (']', '}'):
                depth -= 1
                if depth == 0:
                    return start_pos, i + 1
    return None, None

# ---- 1. STOCK_MAP ----
# 找 var STOCK_MAP=
m = re.search(r"var STOCK_MAP=", html)
if m:
    # 找后面的 { 
    brace_pos = html.find("{", m.end())
    if brace_pos >= 0:
        s, e = find_js_value(html, brace_pos)
        if s is not None:
            data_raw = html[s:e]
            path = os.path.join(INDEX_DIR, "stock_map.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(data_raw)
            
            # 删除整行 var STOCK_MAP={...};
            line_start = html.rfind("\n", 0, m.start()) + 1
            semi_end = html.find(";", e)
            if semi_end < 0 or semi_end > e + 10:
                semi_end = e
            else:
                semi_end += 1
            old_block = html[line_start:semi_end]
            new_block = "var STOCK_MAP={};"
            html = html.replace(old_block, new_block, 1)
            print(f"✅ STOCK_MAP → stock_map.js ({len(data_raw)/1024:.0f} KB)")

# ---- 2. 提取各函数中 const/var DATA = [...] ----
extractions = [
    ("buildIndexGrid", "index_chart.js"),
    ("buildEtfGrid", "etf_chart.js"),
    ("buildRankTable", "ranking.json"),
    ("buildBlockTable", "block.json"),
    ("buildSectorTable", "sector.json"),
]

for func_name, fname in extractions:
    func_pos = html.find("function " + func_name + "(")
    if func_pos < 0:
        print(f"⚠️  未找到 {func_name}")
        continue
    
    # 函数体内找 DATA/data = [
    func_end = html.find("\nfunction ", func_pos + 10)
    if func_end < 0:
        func_end = len(html)
    body = html[func_pos:func_end]
    
    # 找常量声明
    for pat in ["const DATA = [", "const data = [", "var data = ["]:
        m2 = re.search(re.escape(pat), body)
        if m2:
            break
    if not m2:
        print(f"⚠️  {func_name} 未找到数据声明")
        continue
    
    abs_pos = func_pos + m2.end()
    val_s, val_e = find_js_value(html, abs_pos - 1)  # -1 for the = sign position
    if val_s is None:
        # Try from exact [
        val_s, val_e = find_js_value(html, abs_pos - 1)
        if val_s is None:
            print(f"⚠️  {func_name} 数据解析失败")
            continue
    
    data_raw = html[val_s:val_e]
    is_js = fname.endswith(".js")
    
    path = os.path.join(INDEX_DIR, fname)
    if is_js:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data_raw)
        count = "—"
    else:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(json.loads(data_raw), f, ensure_ascii=False)
            count = len(json.loads(data_raw))
        except json.JSONDecodeError:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data_raw)
            count = "parse_err"
    
    # 替换：将整个声明替换为读取全局变量
    line_start = html.rfind("\n", 0, func_pos + m2.start()) + 1
    semi_end = html.find(";", val_e)
    if semi_end < 0 or semi_end > val_e + 20:
        semi_end = val_e
    else:
        semi_end += 1
    old_block = html[line_start:semi_end]
    
    varname = f"_CHART_{func_name.upper()}"
    if is_js:
        new_block = f"const DATA = (window.{varname}||[]);"
    else:
        new_block = f"const data = (window.{varname}||[]);"
    
    html = html.replace(old_block, new_block)
    print(f"✅ {func_name} → {fname} ({len(data_raw)/1024:.0f} KB, {count} 条)")

# ---- 3. 注入数据预加载 ----
init_str = """// ===== 异步加载图表数据 =====
var _DATA_LOADED = false;
function loadAllData(cb) {
  if (_DATA_LOADED) { cb(); return; }
  var pending = 6, done = function(){ if (--pending <= 0) { _DATA_LOADED = true; cb(); } };
  // JS 端点返回 window 赋值
  [{s:'/api/data/stock-map'}, {s:'/api/data/index-chart'}, {s:'/api/data/etf-chart'}]
    .forEach(function(x){ var el=document.createElement('script'); el.src=x.s; el.onload=done; el.onerror=done; document.head.appendChild(el); });
  // JSON 端点：fetch + JSON.parse 存入 window
  [{u:'/api/data/ranking', n:'_CHART_BUILDRANKTABLE'}, {u:'/api/data/block', n:'_CHART_BUILDBLOCKTABLE'}, {u:'/api/data/sector', n:'_CHART_BUILDSECTORTABLE'}]
    .forEach(function(f){ fetch(f.u).then(function(r){return r.json();}).then(function(d){ window[f.n]=d; done(); }).catch(done); });
}

"""
html = html.replace("// ========== 初始化 ==========", init_str + "// ========== 初始化 ==========")

# ---- 4. 修改 init 先加载数据 ----
old_init = "  loadRiskState();\n  loadOppState();\n  loadIndexState();\n  buildIndexGrid();\n  buildRankTable();\n  _tabRendered['index'] = true;"
new_init = "  loadRiskState();\n  loadOppState();\n  loadIndexState();\n  loadAllData(function() {\n    buildIndexGrid();\n    buildRankTable();\n    _tabRendered['index'] = true;\n  });"
html = html.replace(old_init, new_init)

with open(HTML, "w", encoding="utf-8") as f:
    f.write(html)

new_size = os.path.getsize(HTML)
print(f"\n📊 dashboard.html: {orig_size/1024:.0f} KB → {new_size/1024:.0f} KB ({100*(1-new_size/orig_size):.0f}% reduction)")
