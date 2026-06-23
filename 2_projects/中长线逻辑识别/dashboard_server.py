#!/usr/bin/env python3
"""统一服务：静态文件 + API 持久化（风险标签 & 重点监控）"""
import json, os, re, socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, unquote

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(DATA_DIR, ".index_data")
RISK_TAGS_FILE = os.path.join(DATA_DIR, "risk_tags.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor.json")
OPP_TAGS_FILE = os.path.join(DATA_DIR, "opp_tags.json")
INDEX_STATE_FILE = os.path.join(DATA_DIR, "index_state.json")
INDEX_LEVELS_FILE = os.path.join(DATA_DIR, "index_levels.json")
REVIEW_FILE = os.path.join(DATA_DIR, "review.json")
REVIEW_META_FILE = os.path.join(DATA_DIR, "review_meta.json")

os.makedirs(INDEX_DIR, exist_ok=True)


def read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class DashboardHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        # 将请求路径映射到 DATA_DIR 下
        parsed = urlparse(path)
        p = parsed.path
        if p.startswith("/api/"):
            return ""  # API 请求不走文件
        # 静态文件从 DATA_DIR（项目目录）提供
        p = p.lstrip("/")
        return os.path.join(DATA_DIR, p) if p else DATA_DIR

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 数据 API
        data_files = {
            "/api/data/stock-map": ("stock_map.js", "application/javascript"),
            "/api/data/index-chart": ("index_chart.js", "application/javascript"),
            "/api/data/etf-chart": ("etf_chart.js", "application/javascript"),
            "/api/data/ranking": ("ranking.json", "application/json"),
            "/api/data/block": ("block.json", "application/json"),
            "/api/data/sector": ("sector.json", "application/json"),
            "/api/data/ma5-trend": ("ma5_trend.json", "application/json"),
            "/api/data/indicators": ("indicators.json", "application/json"),
            "/api/data/themes": ("themes.json", "application/json"),
            "/api/data/colors": ("colors.json", "application/json"),
        }
        if path in data_files:
            fname, ctype = data_files[path]
            fpath = os.path.join(INDEX_DIR, fname)
            if os.path.exists(fpath):
                # JS 文件需要包装为 window 赋值
                wrappers = {
                    "stock_map.js": ("window.STOCK_MAP=window.STOCK_MAP||{};Object.assign(window.STOCK_MAP,", ");"),
                    "index_chart.js": ("window._CHART_BUILDINDEXGRID=", ";"),
                    "etf_chart.js": ("window._CHART_BUILDETFGRID=", ";"),
                }
                if fname in wrappers:
                    with open(fpath, encoding="utf-8") as f:
                        raw = f.read()
                    body = (wrappers[fname][0] + raw + wrappers[fname][1]).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", len(body))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._serve_file(fpath, ctype)
            else:
                self._json_response({"error": "no data"}, 404)
            return

        if path == "/api/risk-tags":
            data = read_json(RISK_TAGS_FILE, {"manual": [], "deleted": []})
            self._json_response(data)
        elif path == "/api/opp-tags":
            data = read_json(OPP_TAGS_FILE, {"manual": [], "deleted": []})
            self._json_response(data)
        elif path == "/api/monitor":
            data = read_json(MONITOR_FILE, [])
            self._json_response(data)
        elif path == "/api/index-state":
            data = read_json(INDEX_STATE_FILE, {"state": "区间震荡"})
            self._json_response(data)
        elif path.startswith("/api/index-levels"):
            query = urlparse(self.path).query
            params = {}
            if query:
                for kv in query.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        params[k] = v
            index_name = unquote(params.get("index", "上证指数"))
            data = read_json(INDEX_LEVELS_FILE, {})
            if isinstance(data, dict) and "lines" in data:
                # 旧格式 → 迁移
                old_lines = data.get("lines", [])
                old_defaults = data.get("defaults", [4200, 4050, 4000, 3940, 3794])
                data = {"上证指数": {"lines": old_lines, "defaults": old_defaults}, "创业板指": {"lines": [], "defaults": [2100, 2000, 1900, 1800, 1700]}}
                write_json(INDEX_LEVELS_FILE, data)
            index_data = data.get(index_name, {"lines": [], "defaults": [4200, 4050, 4000, 3940, 3794]})
            self._json_response(index_data)
        elif path == "/api/review":
            all_data = read_json(REVIEW_FILE, [])
            # 按 created_at 降序，取最近 5 条
            all_data.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            self._json_response(all_data[:5])
        elif path == "/api/review-meta":
            data = read_json(REVIEW_META_FILE, {"market_style":"","personal_state":"","operation_expect":""})
            self._json_response(data)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8")

        if path == "/api/risk-tags":
            try:
                data = json.loads(body)
                write_json(RISK_TAGS_FILE, data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/opp-tags":
            try:
                data = json.loads(body)
                write_json(OPP_TAGS_FILE, data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/monitor":
            try:
                data = json.loads(body)
                write_json(MONITOR_FILE, data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/index-state":
            try:
                data = json.loads(body)
                write_json(INDEX_STATE_FILE, data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path.startswith("/api/index-levels"):
            try:
                data = json.loads(body)
                query = urlparse(self.path).query
                params = {}
                if query:
                    for kv in query.split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            params[k] = v
                index_name = unquote(params.get("index", "上证指数"))
                full = read_json(INDEX_LEVELS_FILE, {})
                if isinstance(full, dict) and "lines" in full:
                    full = {"上证指数": {"lines": full.get("lines", []), "defaults": full.get("defaults", [4200, 4050, 4000, 3940, 3794])}, "创业板指": {"lines": [], "defaults": [2100, 2000, 1900, 1800, 1700]}}
                full[index_name] = data
                write_json(INDEX_LEVELS_FILE, full)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/review":
            try:
                entry = json.loads(body)
                # 确保必填字段
                entry.setdefault("date", "")
                entry.setdefault("operation", "")
                entry.setdefault("expectation", "")
                entry.setdefault("detail", "")
                entry.setdefault("created_at", "")
                all_data = read_json(REVIEW_FILE, [])
                all_data.append(entry)
                write_json(REVIEW_FILE, all_data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/review-meta":
            try:
                data = json.loads(body)
                write_json(REVIEW_META_FILE, data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        else:
            self._json_response({"error": "not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8")

        if path == "/api/review":
            try:
                entry = json.loads(body)
                created_at = entry.get("created_at", "")
                all_data = read_json(REVIEW_FILE, [])
                for i, e in enumerate(all_data):
                    if e.get("created_at", "") == created_at:
                        all_data[i] = entry
                        write_json(REVIEW_FILE, all_data)
                        self._json_response({"ok": True})
                        return
                # 未找到匹配的 created_at → 追加
                all_data.append(entry)
                write_json(REVIEW_FILE, all_data)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        else:
            self._json_response({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/review":
            try:
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
                entry = json.loads(body) if body else {}
                created_at = entry.get("created_at", "")
                all_data = read_json(REVIEW_FILE, [])
                new_data = [e for e in all_data if e.get("created_at", "") != created_at]
                if len(new_data) < len(all_data):
                    write_json(REVIEW_FILE, new_data)
                    self._json_response({"ok": True})
                else:
                    self._json_response({"ok": False, "error": "not found"}, 404)
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        else:
            self._json_response({"error": "not found"}, 404)

    def _serve_file(self, fpath, content_type):
        with open(fpath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def find_free_port(start=8977):
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


if __name__ == "__main__":
    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"✓ Dashboard 服务已启动: http://127.0.0.1:{port}/dashboard.html")
    server.serve_forever()
