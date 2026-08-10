# -*- coding: utf-8 -*-
"""操作台接口测试：本地 HTTP 服务 + 上传生成 + 会话/边界/自检。"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

import helpers

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONSOLE_SCRIPT = PROJECT_ROOT / "看板操作台.py"


def load_console():
    spec = importlib.util.spec_from_file_location("kanban_console", CONSOLE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


console = load_console()


def fixture_bytes():
    with tempfile.TemporaryDirectory() as d:
        p = helpers.make_workbook(Path(d) / "t.xlsx", [helpers.task()])
        return p.read_bytes()


class ConsoleServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.server, self.thread = console.start_console(
            data_dir=self.data_dir,
            asset_dir=PROJECT_ROOT / "assets",
            open_browser=False,
            max_sessions=3,
            max_upload_bytes=64 * 1024,
        )
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def post(self, path, body=b""):
        req = urllib.request.Request(f"{self.base}{path}", data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)

    def generate(self, name="示例.xlsx", body=None):
        quoted = urllib.parse.quote(name)
        return self.post(f"/api/generate?filename={quoted}", body if body is not None else fixture_bytes())

    def test_homepage(self):
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("操作台", body.decode("utf-8"))

    def test_generate_ok_and_dashboard_self_contained(self):
        status, data, _ = self.generate()
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["taskCount"], 1)
        self.assertTrue(data["dashboardUrl"])
        status, html, _ = self.get(data["dashboardUrl"])
        self.assertEqual(status, 200)
        text = html.decode("utf-8")
        self.assertIn("任务清单", text)
        self.assertEqual(helpers.bd.external_refs(text), [])
        self.assertTrue(Path(data["savedPath"]).exists())

    def test_generate_rejects_garbage(self):
        status, data, _ = self.generate(body=b"not an excel file")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["errors"])

    def test_generate_rejects_wrong_extension(self):
        status, data, _ = self.generate(name="x.txt")
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_upload_too_large(self):
        status, data, _ = self.generate(body=b"x" * (128 * 1024))
        self.assertEqual(status, 413)
        self.assertFalse(data["ok"])

    def test_session_limit(self):
        tokens = []
        for i in range(4):
            _, data, _ = self.generate(name=f"a{i}.xlsx")
            self.assertTrue(data["ok"])
            tokens.append(data["token"])
        old_status, _, _ = self.get(f"/api/dashboard?session={tokens[0]}")
        self.assertEqual(old_status, 404)
        for token in tokens[1:]:
            status, _, _ = self.get(f"/api/dashboard?session={token}")
            self.assertEqual(status, 200)

    def test_download_header_utf8_filename(self):
        _, data, _ = self.generate()
        status, body, headers = self.get(data["downloadUrl"])
        self.assertEqual(status, 200)
        self.assertIn("UTF-8''", headers.get("Content-Disposition", ""))
        self.assertIn("项目看板", urllib.request.unquote(headers.get("Content-Disposition", "")))
        status2, dash, _ = self.get(data["dashboardUrl"])
        self.assertEqual(body, dash)

    def test_selfcheck(self):
        status, body, _ = self.get("/api/selfcheck")
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["assetsOk"])
        self.assertTrue(data["dataDirWritable"])
        self.assertTrue(data["openpyxl"])

    def test_smoke_test_exit_code(self):
        proc = subprocess.run(
            [sys.executable, str(CONSOLE_SCRIPT), "--smoke-test"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_quit_endpoint(self):
        req = urllib.request.Request(f"{self.base}/api/quit", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive())

    def test_ping_keeps_the_session_alive(self):
        status, data, _ = self.post("/api/ping")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertLess(data["idle"], 1.0)

    def test_selfcheck_reports_version_and_arch(self):
        _, body, _ = self.get("/api/selfcheck")
        data = json.loads(body)
        self.assertEqual(data["version"], console.__version__)
        self.assertTrue(data["arch"])
        self.assertTrue(data["python"])

    def test_upload_copy_is_not_left_behind(self):
        """M3：上传的是用户的项目数据，生成完不该留在输出目录里。"""
        _, data, _ = self.generate()
        self.assertTrue(data["ok"])
        leftovers = [p.name for p in self.data_dir.iterdir()
                     if p.name.startswith("_upload")]
        self.assertEqual(leftovers, [], f"残留了上传副本：{leftovers}")
        self.assertTrue((self.data_dir / "项目看板.html").exists())

    def test_upload_copy_is_cleaned_up_even_when_the_file_is_broken(self):
        _, data, _ = self.generate(body=b"not an excel file")
        self.assertFalse(data["ok"])
        leftovers = [p.name for p in self.data_dir.iterdir()
                     if p.name.startswith("_upload")]
        self.assertEqual(leftovers, [], f"出错路径也残留了上传副本：{leftovers}")


class IdleShutdownTest(unittest.TestCase):
    """M1：关掉浏览器标签后，进程要自己退出，不能留僵尸。"""

    def test_server_exits_after_idle_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            server, thread = console.start_console(
                data_dir=Path(d), asset_dir=PROJECT_ROOT / "assets",
                open_browser=False, idle_timeout=1, watchdog=True,
            )
            try:
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive(), "闲置超时后服务仍在运行")
                self.assertTrue(server.idle_exit)
            finally:
                server.shutdown()
                server.server_close()

    def test_heartbeat_postpones_the_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            server, thread = console.start_console(
                data_dir=Path(d), asset_dir=PROJECT_ROOT / "assets",
                open_browser=False, idle_timeout=8, watchdog=True,
            )
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                for _ in range(4):          # 心跳 4 次，跨过 8 秒的闲置线
                    time.sleep(2.5)
                    req = urllib.request.Request(f"{base}/api/ping", data=b"", method="POST")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                self.assertTrue(thread.is_alive(), "有心跳时不该退出")
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()


class SingleInstanceTest(unittest.TestCase):
    """M2：双击两次不该起两个实例、争写同一个 项目看板.html。"""

    def test_running_instance_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            server, thread = console.start_console(
                data_dir=data_dir, asset_dir=PROJECT_ROOT / "assets",
                open_browser=False, write_port_file=True,
            )
            try:
                self.assertEqual(console.running_instance(data_dir),
                                 server.server_address[1])
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()

    def test_stale_port_file_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            # 上次没退干净留下的端口文件：端口上没人应答
            console.port_file(data_dir).write_text("9", encoding="utf-8")
            self.assertIsNone(console.running_instance(data_dir))
            self.assertFalse(console.port_file(data_dir).exists(),
                             "过期端口文件应被清掉，否则下次永远起不来")

    def test_garbage_port_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            console.port_file(data_dir).write_text("不是端口", encoding="utf-8")
            self.assertIsNone(console.running_instance(data_dir))


class VersionTest(unittest.TestCase):
    """R1：版本号只能有一处来源。"""

    def test_version_is_a_sane_semver(self):
        self.assertRegex(console.__version__, r"^\d+\.\d+\.\d+$")

    def test_server_header_uses_the_version(self):
        self.assertEqual(console.ConsoleHandler.server_version,
                         f"KanbanConsole/{console.__version__}")

    def test_console_page_and_spec_read_the_same_version(self):
        raw = (PROJECT_ROOT / "console.html").read_text(encoding="utf-8")
        self.assertIn("{{VERSION}}", raw, "页面不该把版本号写死")
        self.assertNotIn("{{VERSION}}", console.load_console_html())
        self.assertIn(console.__version__, console.load_console_html())
        spec = (PROJECT_ROOT / "看板操作台.spec").read_text(encoding="utf-8")
        self.assertIn("__version__", spec, "spec 应从 看板操作台.py 读版本号")


class HygieneTest(unittest.TestCase):
    """两条守卫：都是这轮复查真抓出来的问题，别再犯第二次。"""

    def test_selfcheck_json_is_parsable_by_a_browser(self):
        """--idle-timeout 0 曾让 selfcheck 回出 Infinity——Python 认，JSON.parse 不认。"""
        with tempfile.TemporaryDirectory() as d:
            server, thread = console.start_console(
                data_dir=Path(d), asset_dir=PROJECT_ROOT / "assets",
                open_browser=False, idle_timeout=float("inf"),
            )
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{server.server_address[1]}/api/selfcheck",
                        timeout=10) as resp:
                    raw = resp.read().decode("utf-8")
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()
        for token in ("Infinity", "NaN", "-Infinity"):
            self.assertNotIn(token, raw, f"响应里有 {token}，浏览器 JSON.parse 会直接抛错")
        json.loads(raw, parse_constant=lambda c: self.fail(f"非法 JSON 常量 {c}"))

    def test_no_control_characters_in_frontend_sources(self):
        """前端源码会被原样内联进看板 HTML，混进裸控制字符会污染每一份产物。"""
        targets = [PROJECT_ROOT / "console.html"]
        targets += sorted((PROJECT_ROOT / "assets").glob("*.js"))
        targets += sorted((PROJECT_ROOT / "assets").glob("*.css"))
        targets += sorted((PROJECT_ROOT / "assets").glob("*.html"))
        for f in targets:
            raw = f.read_bytes()
            bad = {c for c in raw if c < 0x09 or 0x0e <= c <= 0x1f or c == 0x0b or c == 0x0c}
            self.assertFalse(bad, f"{f.name} 含裸控制字符 {sorted(hex(c) for c in bad)}")


class PackagingExcludesTest(unittest.TestCase):
    """spec 里排除的模块，必须真的没人用。

    没有这层验证，excludes 就是在赌——赌错了要等到用户双击 app 才炸。
    这里把它们从 import 里挡掉，跑一遍完整的「起服务 → 生成看板 → 校验产物」，
    通过才算这份清单是安全的。
    """

    BLOCKER = """
import sys
BLOCKED = set({blocked!r})


class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED:
            raise ImportError("[packaging-excludes] " + name)
        return None


sys.meta_path.insert(0, _Blocker())
for _m in [m for m in sys.modules if m in BLOCKED]:
    del sys.modules[_m]

import importlib.util
spec = importlib.util.spec_from_file_location("kc", {script!r})
kc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kc)
sys.exit(kc.main(["--smoke-test"]))
"""

    @staticmethod
    def spec_excludes():
        text = (PROJECT_ROOT / "看板操作台.spec").read_text(encoding="utf-8")
        block = re.search(r"EXCLUDES = \[(.*?)\]", text, re.S)
        assert block, "spec 里找不到 EXCLUDES 清单"
        return re.findall(r'"([^"]+)"', block.group(1))

    def run_with_blocked(self, blocked):
        code = self.BLOCKER.format(blocked=sorted(blocked), script=str(CONSOLE_SCRIPT))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, cwd=str(PROJECT_ROOT), timeout=120)
        return proc

    def test_spec_declares_the_expected_excludes(self):
        excludes = self.spec_excludes()
        for must in ("ssl", "_ssl", "_hashlib", "lzma", "bz2"):
            self.assertIn(must, excludes, f"{must} 应在 spec 的 EXCLUDES 里")
        self.assertNotIn("hashlib", excludes,
                         "hashlib 不能排除——random.seed(str) 要用它，只能排 _hashlib")

    def test_console_still_works_without_every_excluded_module(self):
        proc = self.run_with_blocked(self.spec_excludes())
        self.assertEqual(proc.returncode, 0,
                         f"屏蔽 spec 的 EXCLUDES 后跑不通：\n{proc.stdout}\n{proc.stderr}")

    def test_hashlib_still_usable_without_the_openssl_backend(self):
        """去掉 _hashlib 后 hashlib 应自动退回内建实现。"""
        code = (
            "import sys\n"
            "class B:\n"
            "    def find_spec(self, n, p=None, t=None):\n"
            "        if n == '_hashlib': raise ImportError('blocked')\n"
            "sys.meta_path.insert(0, B())\n"
            "for m in [x for x in sys.modules if x in ('hashlib', '_hashlib')]:\n"
            "    del sys.modules[m]\n"
            "import hashlib, random\n"
            "assert hashlib.sha512(b'x').hexdigest()\n"
            "assert hashlib.md5(b'x').hexdigest()\n"
            "random.seed('种子')\n"
            "print('ok')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class SpecTest(unittest.TestCase):
    """打包配置是唯一一处「只有在 Mac 上真打包时才会执行」的代码。

    这里按 PyInstaller 的方式把 spec 执行一遍（注入它的全局符号、模拟 darwin），
    错版本号、丢 LSUIElement、漏资源都能在本机测出来，不用等打包。
    """

    @staticmethod
    def run_spec(platform_name):
        import types
        calls = []

        class Rec:
            def __init__(self, name):
                self.name = name

            def __call__(self, *a, **kw):
                calls.append((self.name, kw))
                obj = types.SimpleNamespace(**kw)
                obj.pure = obj.scripts = obj.binaries = obj.datas = []
                return obj

        g = {"__file__": "看板操作台.spec"}
        for n in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE", "Tree"):
            g[n] = Rec(n)
        src = (PROJECT_ROOT / "看板操作台.spec").read_text(encoding="utf-8")
        real, cwd = sys.platform, os.getcwd()
        sys.platform = platform_name
        os.chdir(PROJECT_ROOT)
        try:
            exec(compile(src, "spec", "exec"), g)
        finally:
            sys.platform = real
            os.chdir(cwd)
        return g, dict(calls)

    def test_spec_executes_and_uses_the_single_source_version(self):
        g, calls = self.run_spec("darwin")
        self.assertEqual(g["version"], console.__version__)
        plist = calls["BUNDLE"]["info_plist"]
        self.assertEqual(plist["CFBundleShortVersionString"], console.__version__)
        self.assertEqual(plist["CFBundleVersion"], console.__version__)
        self.assertNotEqual(plist["CFBundleShortVersionString"], "0.0.0")

    def test_app_does_not_take_a_dock_slot(self):
        """没有 LSUIElement，Dock 里就会留一个点不动、Cmd+Q 杀不掉的图标。"""
        _, calls = self.run_spec("darwin")
        self.assertIs(calls["BUNDLE"]["info_plist"].get("LSUIElement"), True)

    def test_frontend_assets_are_bundled(self):
        _, calls = self.run_spec("darwin")
        datas = calls["Analysis"]["datas"]
        self.assertIn(("assets", "assets"), datas)
        self.assertIn(("console.html", "."), datas)

    def test_only_one_packaging_path_remains(self):
        """Windows 分支已下线，spec 里不该再有第二条路径。"""
        src = (PROJECT_ROOT / "看板操作台.spec").read_text(encoding="utf-8")
        self.assertNotIn("KANBAN_ONEFILE", src)
        self.assertFalse((PROJECT_ROOT / "打包Windows应用.bat").exists())


if __name__ == "__main__":
    unittest.main()
