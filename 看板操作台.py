#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地网页式操作台：选 Excel → 校验/分析 → 生成并查看/下载看板。

完全离线、单机使用：HTTP 服务只监听 127.0.0.1，不提供任何局域网/公网接口。
复用 build_dashboard.py 的解析、校验、CPM 推算与 HTML 渲染，不重复实现业务逻辑。

用法:
    python3 看板操作台.py                 # 启动操作台并自动打开浏览器
    python3 看板操作台.py --no-browser    # 只启动服务，不自动打开浏览器
    python3 看板操作台.py --smoke-test    # 非交互自检（打包后验证冻结包用）
"""

import argparse
import io
import json
import os
import platform
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import openpyxl

from build_dashboard import (
    HEADERS,
    DashboardError,
    build,
    external_refs,
    load_assets,
    render_html,
)

# 版本号的唯一来源：打包 spec、HTTP 响应头、操作台页脚都从这里取（PLAN 8.5 R1）
__version__ = "3.1.0"

DEFAULT_DATA_DIR = Path.home() / "项目看板操作台"
DEFAULT_OUTPUT_NAME = "项目看板.html"
PORT_FILE_NAME = ".console.port"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100MB
MAX_SESSIONS = 10

# 页面每 30 秒心跳一次；连续这么久没有任何动静就自行退出。
# 没有这一层，用户关掉浏览器标签后进程会一直留着，而唯一的退出入口
# （页面上的「退出操作台」按钮）恰恰随标签一起没了（PLAN 8.5 M1）
IDLE_TIMEOUT_SECONDS = 600
WATCHDOG_TICK_SECONDS = 5


def resource_dir():
    """源码目录；PyInstaller 冻结时返回 _MEIPASS。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_console_html():
    html = (resource_dir() / "console.html").read_text(encoding="utf-8")
    return html.replace("{{VERSION}}", __version__)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, data_dir, asset_dir,
                 max_sessions=MAX_SESSIONS, max_upload_bytes=MAX_UPLOAD_BYTES,
                 idle_timeout=IDLE_TIMEOUT_SECONDS):
        super().__init__(addr, handler)
        self.data_dir = Path(data_dir)
        self.asset_dir = Path(asset_dir)
        self.max_sessions = max_sessions
        self.max_upload_bytes = max_upload_bytes
        self.idle_timeout = idle_timeout
        self.sessions = {}
        self.console_html = load_console_html()
        self.last_seen = time.monotonic()
        self.idle_exit = False          # 是否因为闲置而自动退出，供调用方区分

    def touch(self):
        self.last_seen = time.monotonic()

    def idle_seconds(self):
        return time.monotonic() - self.last_seen


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = f"KanbanConsole/{__version__}"

    def log_message(self, fmt, *args):  # 保持操作台安静，不刷终端
        pass

    # ------------------------------------------------------------ 工具方法
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(self, body, content_type, status=200, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=400, hint=""):
        self._send_json({
            "ok": False,
            "errors": [{"level": "error", "where": "", "message": message, "hint": hint}],
        }, status)

    # ------------------------------------------------------------ GET
    def do_GET(self):
        self.server.touch()             # 任何请求都算「人还在」
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(self.server.console_html.encode("utf-8"),
                             "text/html; charset=utf-8")
        elif parsed.path == "/api/dashboard":
            self._serve_session(parsed, attach=False)
        elif parsed.path == "/api/download":
            self._serve_session(parsed, attach=True)
        elif parsed.path == "/api/selfcheck":
            self._send_json(self._selfcheck())
        else:
            self._send_json({"ok": False, "error": "接口不存在"}, 404)

    def _selfcheck(self):
        asset_names = ("template.html", "dashboard.css", "dashboard.js")
        assets_ok = all((self.server.asset_dir / n).exists() for n in asset_names)
        data_ok = True
        try:
            self.server.data_dir.mkdir(parents=True, exist_ok=True)
            probe = self.server.data_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            data_ok = False
        return {
            "ok": assets_ok and data_ok,
            "version": __version__,
            "openpyxl": openpyxl.__version__,
            "assetsOk": assets_ok,
            "dataDirWritable": data_ok,
            "port": self.server.server_address[1],
            "python": platform.python_version(),
            "arch": platform.machine(),
            "frozen": bool(getattr(sys, "frozen", False)),
            # 不自动退出时回 0 而不是 inf——json.dumps(inf) 出的是 Infinity，
            # Python 认，浏览器的 JSON.parse 不认，环境自检会整个报错
            "idleTimeout": (self.server.idle_timeout
                            if self.server.idle_timeout != float("inf") else 0),
        }

    def _serve_session(self, parsed, attach=False):
        token = (parse_qs(parsed.query).get("session") or [""])[0]
        entry = self.server.sessions.get(token)
        if not entry:
            self._send_json({"ok": False, "error": "会话不存在或已过期"}, 404)
            return
        body = entry["html"].encode("utf-8")
        headers = {}
        if attach:
            headers["Content-Disposition"] = (
                "attachment; filename=\"kanban.html\"; "
                "filename*=UTF-8''%E9%A1%B9%E7%9B%AE%E7%9C%8B%E6%9D%BF.html"
            )
        self._send_bytes(body, "text/html; charset=utf-8", 200, headers)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        self.server.touch()
        parsed = urlparse(self.path)
        if parsed.path == "/api/generate":
            self._handle_generate(parsed)
        elif parsed.path == "/api/ping":
            # 页面的心跳。只要标签还开着就一直有；标签一关，看门狗就会开始计时
            self._send_json({"ok": True, "idle": round(self.server.idle_seconds(), 1)})
        elif parsed.path == "/api/quit":
            self._send_json({"ok": True, "message": "操作台即将退出"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send_json({"ok": False, "error": "接口不存在"}, 404)

    def _handle_generate(self, parsed):
        filename = (parse_qs(parsed.query).get("filename") or [""])[0].strip()
        suffix = Path(filename).suffix.lower()
        if suffix not in (".xlsx", ".xlsm"):
            self._error("请选择 .xlsx 或 .xlsm 格式的 Excel 文件")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._error("未收到文件内容，请重新选择文件")
            return
        if length > self.server.max_upload_bytes:
            self._error(f"文件超过大小上限（{self.server.max_upload_bytes // (1024 * 1024)}MB）", 413)
            return
        body = self.rfile.read(length)
        if not body:
            self._error("未收到文件内容，请重新选择文件")
            return
        upload_path = None
        try:
            self.server.data_dir.mkdir(parents=True, exist_ok=True)
            # 用临时文件而不是固定的 _upload.xlsx：上传的是用户的项目数据，
            # 旧实现会把它永久留在输出目录里（PLAN 8.5 M3）
            fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="_upload-",
                                            dir=self.server.data_dir)
            os.close(fd)
            upload_path = Path(tmp_name)
            upload_path.write_bytes(body)
            try:
                payload, report = build(upload_path)
            finally:
                # 解析完立刻删，不拖到最外层的 finally——那时响应已经发出去了，
                # 调用方有机会先看到这份残留的用户数据副本
                upload_path.unlink(missing_ok=True)
                upload_path = None
            assets = load_assets(self.server.asset_dir)
            html = render_html(assets, payload)
            leftovers = external_refs(html)
            if leftovers:
                raise RuntimeError(f"看板产物残留外部引用：{'、'.join(leftovers)}")
            saved_path = self.server.data_dir / DEFAULT_OUTPUT_NAME
            saved_path.write_text(html, encoding="utf-8")
            token = uuid.uuid4().hex
            self.server.sessions[token] = {
                "html": html,
                "filename": filename,
                "saved_path": str(saved_path),
                "payload": payload,
            }
            self._trim_sessions()
            self._send_json({
                "ok": True,
                "token": token,
                "taskCount": len(payload["tasks"]),
                "milestoneCount": sum(1 for t in payload["tasks"] if t.get("milestone")),
                "criticalCount": payload.get("criticalCount", 0),
                "criticalDuration": payload.get("criticalDuration", 0),
                "warnings": [i.format() for i in report.warnings],
                "dashboardUrl": f"/api/dashboard?session={token}",
                "downloadUrl": f"/api/download?session={token}",
                "savedPath": str(saved_path),
            })
        except DashboardError as exc:
            self._send_json({
                "ok": False,
                "errors": [
                    {"level": i.level, "where": i.where, "message": i.message, "hint": i.hint}
                    for i in exc.issues
                ],
            })
        except Exception as exc:
            self._error(f"处理失败：{type(exc).__name__}: {exc}", 500)
        finally:
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)

    def _trim_sessions(self):
        while len(self.server.sessions) > self.server.max_sessions:
            self.server.sessions.pop(next(iter(self.server.sessions)))


# ---------------------------------------------------------------- 进程生命周期

def _watchdog(server):
    """闲置到点就自己退出。心跳来自操作台页面，标签一关就没了。"""
    while True:
        time.sleep(WATCHDOG_TICK_SECONDS)
        if server.idle_seconds() >= server.idle_timeout:
            server.idle_exit = True
            server.shutdown()
            return


def port_file(data_dir):
    return Path(data_dir) / PORT_FILE_NAME


def running_instance(data_dir):
    """已有实例在跑就返回它的端口，否则返回 None（顺手清掉过期的端口文件）。"""
    path = port_file(data_dir)
    try:
        port = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/selfcheck", timeout=1.5) as resp:
            if resp.status == 200 and b"assetsOk" in resp.read():
                return port
    except (urllib.error.URLError, OSError, ValueError):
        pass
    path.unlink(missing_ok=True)        # 端口文件是上次没退干净留下的
    return None


def start_console(data_dir=None, asset_dir=None, open_browser=True,
                  max_sessions=MAX_SESSIONS, max_upload_bytes=MAX_UPLOAD_BYTES,
                  idle_timeout=IDLE_TIMEOUT_SECONDS, watchdog=False,
                  write_port_file=False):
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = Path(asset_dir or (resource_dir() / "assets"))
    server = ConsoleServer(("127.0.0.1", 0), ConsoleHandler,
                           data_dir, asset_dir,
                           max_sessions=max_sessions,
                           max_upload_bytes=max_upload_bytes,
                           idle_timeout=idle_timeout)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    if write_port_file:
        port_file(data_dir).write_text(str(port), encoding="utf-8")
    if watchdog:
        threading.Thread(target=_watchdog, args=(server,), daemon=True).start()
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return server, thread


def smoke_fixture_bytes():
    """生成一个最小但合法的 16 字段工作簿，供 --smoke-test 使用。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "任务"
    ws.append(list(HEADERS))
    ws.append(["T001", "阶段甲", "示例任务", "", "张三", "研发",
               date(2026, 1, 5), date(2026, 1, 9), 0,
               "", "否", "中", "未开始", "中", "", ""])
    cfg = wb.create_sheet("项目配置")
    cfg.append(["项目配置", ""])
    cfg.append(["项目名称", "自检项目"])
    cfg.append(["项目开始日期", date(2026, 1, 1)])
    cfg.append(["项目结束日期", date(2026, 6, 30)])
    cfg.append(["预警提前天数", 5])
    cfg.append(["工作日", "周一至周五"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_smoke_test(server):
    """非交互自检：启动服务 → 生成一份看板 → 校验产物 → 返回退出码。"""
    import urllib.request
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/", timeout=10) as resp:
            page = resp.read().decode("utf-8")
            if resp.status != 200 or "操作台" not in page:
                print("[smoke] 首页异常", file=sys.stderr)
                return 1
        req = urllib.request.Request(
            f"{base}/api/generate?filename=smoke.xlsx",
            data=smoke_fixture_bytes(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok") or data.get("taskCount", 0) < 1:
            print(f"[smoke] 生成异常：{data}", file=sys.stderr)
            return 1
        with urllib.request.urlopen(f"{base}{data['dashboardUrl']}", timeout=10) as resp:
            html = resp.read().decode("utf-8")
        if "任务清单" not in html or external_refs(html):
            print("[smoke] 看板产物异常", file=sys.stderr)
            return 1
        print("[smoke] 自检通过：首页、生成、看板产物均正常")
        return 0
    except Exception as exc:
        print(f"[smoke] 自检失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地网页式项目看板操作台（完全离线）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--smoke-test", action="store_true", help="非交互自检后退出")
    parser.add_argument("--version", action="version", version=f"看板操作台 {__version__}")
    parser.add_argument("--idle-timeout", type=int, default=IDLE_TIMEOUT_SECONDS,
                        help=f"闲置多少秒后自动退出，0 表示不自动退出（默认 {IDLE_TIMEOUT_SECONDS}）")
    args = parser.parse_args(argv)

    open_browser = not args.no_browser and not args.smoke_test

    # 单实例：已经有一个在跑就把浏览器指过去，不再起第二个。
    # 旧版双击两次会起两个服务、两个端口，且都往同一个 项目看板.html 写（PLAN 8.5 M2）
    if not args.smoke_test:
        existing = running_instance(DEFAULT_DATA_DIR)
        if existing:
            url = f"http://127.0.0.1:{existing}/"
            print(f"操作台已经在运行：{url}")
            if open_browser:
                webbrowser.open(url)
            return 0

    try:
        server, thread = start_console(
            open_browser=open_browser,
            idle_timeout=args.idle_timeout if args.idle_timeout > 0 else float("inf"),
            watchdog=not args.smoke_test and args.idle_timeout > 0,
            write_port_file=not args.smoke_test,
        )
    except OSError as exc:
        print(f"[启动失败] {exc}", file=sys.stderr)
        return 2

    port = server.server_address[1]
    if args.smoke_test:
        code = run_smoke_test(server)
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        return code

    print(f"操作台已启动：http://127.0.0.1:{port}/（点击页面「退出」或按 Ctrl+C 关闭）")
    if args.idle_timeout > 0:
        print(f"关掉浏览器标签后，闲置 {args.idle_timeout} 秒会自动退出，不会留后台进程。")
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        server.shutdown()
        thread.join(timeout=10)
    finally:
        server.server_close()
        port_file(DEFAULT_DATA_DIR).unlink(missing_ok=True)
    if server.idle_exit:
        print("操作台闲置超时，已自动退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
