#!/usr/bin/env python3
"""常驻启动器：接收 HTML 按钮请求，按需拉起 / 停止 dashboard_server.py。

浏览器页面本身无法直接执行本地进程，因此这个常驻服务负责：
- 监听固定端口（默认 8990），提供 /start /stop /status 接口
- 自身启动时即拉起（或复用）dashboard_server.py 子进程，实现登录自启
- 接收到 /start 请求时，若 dashboard 未运行则派生子进程
- /status 还能探测是否已有 dashboard 在运行（含用户手动启动的情况）
- /stop 优先结束本启动器派生的子进程，否则按端口结束（兼容 launchd 重启后的复用）

建议通过 LaunchAgent（见 com.dashboard.launcher.plist）开机自启，
这样 HTML 里的「启动后台服务」按钮随时可用。
"""
import json
import os
import subprocess
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_PORT = 8990
DASHBOARD_START_PORT = 8977  # dashboard 端口探测起点
DASHBOARD_SCRIPT = os.path.join(DATA_DIR, "dashboard_server.py")

# 保护对子进程状态的读写
_lock = threading.Lock()
_dashboard_proc = None      # subprocess.Popen 或 None
_dashboard_port = None      # 由本启动器拉起时记录的端口


def find_free_port(start):
    import socket
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def probe_dashboard_port():
    """探测当前是否有 dashboard 服务在运行，返回端口或 None。"""
    import urllib.request
    for port in range(DASHBOARD_START_PORT, DASHBOARD_START_PORT + 20):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/health", method="GET"
            )
            with urllib.request.urlopen(req, timeout=0.4) as resp:
                if resp.status == 200:
                    return port
        except Exception:
            continue
    return None


def is_proc_alive(proc):
    return proc is not None and proc.poll() is None


def kill_by_port(port):
    """按监听端口结束进程（用于停止非本启动器派生的 dashboard）。返回是否有操作。"""
    import subprocess as _sp
    try:
        out = _sp.check_output(
            ["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
            stderr=_sp.DEVNULL,
        ).decode().strip()
    except Exception:
        return False
    if not out:
        return False
    for pid in out.split():
        try:
            os.kill(int(pid), 15)  # SIGTERM
        except Exception:
            pass
    return True


def start_dashboard():
    """拉起 dashboard 子进程；若已在运行则复用。返回 (port, spawned_by_us)。"""
    global _dashboard_proc, _dashboard_port
    with _lock:
        if is_proc_alive(_dashboard_proc):
            return _dashboard_port, True
    # 先探测是否已有 dashboard 在跑（可能用户手动启动）
    existing = probe_dashboard_port()
    if existing is not None:
        with _lock:
            _dashboard_port = existing
            _dashboard_proc = None  # 不是我们拉起的，不纳入管理
        return existing, False
    port = find_free_port(DASHBOARD_START_PORT)
    proc = subprocess.Popen(
        [sys.executable, DASHBOARD_SCRIPT, "--port", str(port)],
        cwd=DATA_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # 脱离启动器，独立运行
    )
    with _lock:
        _dashboard_proc = proc
        _dashboard_port = port
    return port, True


def stop_dashboard():
    """停止 dashboard：优先结束本启动器派生的子进程，否则按端口结束。"""
    global _dashboard_proc, _dashboard_port
    with _lock:
        proc = _dashboard_proc
        _dashboard_port = None
        _dashboard_proc = None
    if is_proc_alive(proc):
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True
    # 非本启动器派生（如 launchd 重启后探测复用的），按端口结束
    port = probe_dashboard_port()
    if port is not None and kill_by_port(port):
        return True
    return False


class LauncherHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json({})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/status", "/", ""):
            port = None
            with _lock:
                if is_proc_alive(_dashboard_proc):
                    port = _dashboard_port
            if port is None:
                port = probe_dashboard_port()
            running = port is not None
            self._send_json({
                "running": running,
                "port": port,
                "url": f"http://127.0.0.1:{port}/dashboard.html" if running else "",
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/start":
            try:
                port, by_us = start_dashboard()
                self._send_json({
                    "ok": True,
                    "port": port,
                    "managed": by_us,
                    "url": f"http://127.0.0.1:{port}/dashboard.html",
                })
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
        elif parsed.path == "/stop":
            stopped = stop_dashboard()
            self._send_json({"ok": True, "stopped": stopped})
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass  # 静默日志


if __name__ == "__main__":
    # 登录自启时顺便把 dashboard 也拉起来，省去手动点按钮
    try:
        port, by_us = start_dashboard()
        print(f"✓ dashboard 已{'拉起' if by_us else '复用已有'} (端口 {port})")
    except Exception as e:
        print(f"! 启动 dashboard 失败: {e}")
    server = HTTPServer(("127.0.0.1", LAUNCHER_PORT), LauncherHandler)
    print(f"✓ 启动器已运行: http://127.0.0.1:{LAUNCHER_PORT}  (负责拉起 dashboard 服务)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n启动器已停止")
