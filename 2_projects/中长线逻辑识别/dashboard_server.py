#!/usr/bin/env python3
"""统一服务：静态文件 + API 持久化（风险标签 & 重点监控）"""
import json, os, re, socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR = os.path.join(DATA_DIR, ".index_data")
RISK_TAGS_FILE = os.path.join(DATA_DIR, "risk_tags.json")
MONITOR_FILE = os.path.join(DATA_DIR, "monitor.json")
OPP_TAGS_FILE = os.path.join(DATA_DIR, "opp_tags.json")
INDEX_STATE_FILE = os.path.join(DATA_DIR, "index_state.json")

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
        return super().translate_path(path)

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
        else:
            self._json_response({"error": "not found"}, 404)


    def _serve_file(self, path, content_type):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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
