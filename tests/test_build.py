# -*- coding: utf-8 -*-
"""payload 装配、HTML 内联渲染、命令行契约。"""

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import (PROJECT_ROOT, REAL_XLSX, bd, collect, make_workbook,
                     run_cli, task)


class TestPayload(unittest.TestCase):
    def build(self, rows, config=None):
        tasks, report = collect(rows, **({"config": config} if config else {}))
        self.assertEqual([i.message for i in report.errors], [])
        cfg = {"project_name": "P", "start": None, "end": None,
               "warn_days": 5, "workdays": "周一至周五"}
        return bd.build_payload(tasks, cfg, date(2026, 1, 1))

    def test_duration_is_inclusive(self):
        p = self.build([task(计划开始=date(2026, 1, 5), 计划结束=date(2026, 1, 5))])
        self.assertEqual(p["tasks"][0]["duration"], 1)
        p = self.build([task(计划开始=date(2026, 1, 5), 计划结束=date(2026, 1, 9))])
        self.assertEqual(p["tasks"][0]["duration"], 5)

    def test_display_name_composition(self):
        p = self.build([task(任务=" 主任务 ", 子任务="")])
        self.assertEqual(p["tasks"][0]["name"], "主任务")
        p = self.build([task(任务="主任务", 子任务="子项")])
        self.assertEqual(p["tasks"][0]["name"], "主任务 · 子项")

    def test_milestone_becomes_boolean(self):
        p = self.build([task(里程碑="是"), task(任务ID="T002", 里程碑="否")])
        self.assertIs(p["tasks"][0]["milestone"], True)
        self.assertIs(p["tasks"][1]["milestone"], False)

    def test_timeline_pads_when_config_dates_absent(self):
        p = self.build([task(计划开始=date(2026, 3, 10), 计划结束=date(2026, 3, 20))])
        self.assertEqual(p["timelineStart"], "2026-03-03")   # 前后各留 7 天
        self.assertEqual(p["timelineEnd"], "2026-03-27")

    def test_timeline_spans_union_of_config_and_tasks(self):
        tasks, _ = collect([task(计划开始=date(2025, 12, 1), 计划结束=date(2026, 9, 30))])
        cfg = {"project_name": "P", "start": date(2026, 1, 1), "end": date(2026, 6, 30),
               "warn_days": 5, "workdays": "周一至周五"}
        p = bd.build_payload(tasks, cfg, date(2026, 1, 1))
        self.assertEqual(p["timelineStart"], "2025-12-01")   # 取更早的任务开始
        self.assertEqual(p["timelineEnd"], "2026-09-30")     # 取更晚的任务结束


class TestRenderHtml(unittest.TestCase):
    def setUp(self):
        self.assets = bd.load_assets()
        self.payload = {"project": "P", "warnDays": 5, "workdays": "W",
                        "timelineStart": "2026-01-01", "timelineEnd": "2026-01-10",
                        "buildDate": "2026-01-01", "tasks": []}

    def test_inlines_all_three_assets(self):
        html = bd.render_html(self.assets, self.payload)
        self.assertIn("<style>", html)
        self.assertIn("const DATA = ", html)
        self.assertNotIn(bd.TAG_CSS, html)
        self.assertNotIn(bd.TAG_DATA, html)
        self.assertNotIn(bd.TAG_JS, html)

    def test_output_has_no_external_references(self):
        html = bd.render_html(self.assets, self.payload)
        self.assertEqual(bd.external_refs(html), [])

    def test_missing_anchor_is_reported(self):
        broken = dict(self.assets, **{"template.html": "<html></html>"})
        with self.assertRaises(bd.DashboardError) as ctx:
            bd.render_html(broken, self.payload)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_script_close_tag_in_data_cannot_break_out(self):
        """任务备注里写 </script> 不能提前闭合脚本块。"""
        payload = dict(self.payload, tasks=[{"note": "</script><script>alert(1)</script>"}])
        html = bd.render_html(self.assets, payload)
        data_block = html.split("const DATA = ", 1)[1].split("\n</script>", 1)[0]
        self.assertNotIn("</script>", data_block)
        self.assertIn("<\\/script>", data_block)

    def test_missing_assets_dir_raises_with_exit_code_2(self):
        with self.assertRaises(bd.DashboardError) as ctx:
            bd.load_assets(Path("/definitely/not/here"))
        self.assertEqual(ctx.exception.exit_code, 2)


class TestCli(unittest.TestCase):
    def test_real_project_builds_successfully(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.html"
            code, stdout, stderr = run_cli("--input", REAL_XLSX, "--output", out)
            self.assertEqual(code, 0, stderr)
            self.assertTrue(out.exists())
            self.assertIn("已生成", stdout)

    def test_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.html", Path(d) / "b.html"
            run_cli("--input", REAL_XLSX, "--output", a)
            run_cli("--input", REAL_XLSX, "--output", b)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_generated_file_is_self_contained(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.html"
            run_cli("--input", REAL_XLSX, "--output", out)
            html = out.read_text(encoding="utf-8")
            self.assertEqual(bd.external_refs(html), [],
                             "离线看板不允许引用任何外部资源")

    def test_emit_json_is_valid_and_complete(self):
        code, stdout, _ = run_cli("--input", REAL_XLSX, "--emit-json")
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertEqual(set(data) >= {"project", "warnDays", "timelineStart",
                                       "timelineEnd", "buildDate", "tasks"}, True)
        self.assertTrue(data["tasks"])

    def test_dev_mode_writes_data_js(self):
        data_js = PROJECT_ROOT / "assets" / "data.js"
        existed = data_js.exists()
        before = data_js.read_text(encoding="utf-8") if existed else None
        try:
            with tempfile.TemporaryDirectory() as d:
                code, stdout, _ = run_cli("--input", REAL_XLSX,
                                          "--output", Path(d) / "o.html", "--dev")
            self.assertEqual(code, 0)
            self.assertTrue(data_js.exists())
            self.assertTrue(data_js.read_text(encoding="utf-8").startswith("const DATA = "))
        finally:
            if existed:
                data_js.write_text(before, encoding="utf-8")
            elif data_js.exists():
                data_js.unlink()

    # --- 退出码契约 ---

    def test_exit_0_on_success(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, _ = run_cli("--input", REAL_XLSX, "--output", Path(d) / "o.html")
        self.assertEqual(code, 0)

    def test_exit_1_on_data_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad = make_workbook(Path(d) / "bad.xlsx", [task(**{"进度%": 150})])
            code, _, stderr = run_cli("--input", bad, "--output", Path(d) / "o.html")
        self.assertEqual(code, 1)
        self.assertIn("[校验失败]", stderr)
        self.assertIn("修正后重新运行", stderr)

    def test_exit_2_on_missing_input(self):
        code, _, stderr = run_cli("--input", "/no/such/file.xlsx")
        self.assertEqual(code, 2)
        self.assertIn("找不到输入文件", stderr)

    def test_exit_2_on_missing_assets(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, stderr = run_cli("--input", REAL_XLSX, "--output", Path(d) / "o.html",
                                      "--assets", "/no/such/assets")
        self.assertEqual(code, 2)
        self.assertIn("缺少前端资源文件", stderr)

    def test_no_output_written_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.html"
            bad = make_workbook(Path(d) / "bad.xlsx", [task(阶段="")])
            run_cli("--input", bad, "--output", out)
            self.assertFalse(out.exists(), "校验失败时不应留下半成品文件")

    def test_error_output_never_leaks_a_raw_traceback(self):
        with tempfile.TemporaryDirectory() as d:
            bad = make_workbook(Path(d) / "bad.xlsx", [task(计划开始="下周三")])
            _, _, stderr = run_cli("--input", bad, "--output", Path(d) / "o.html")
        self.assertNotIn("Traceback (most recent call last)", stderr)


class TestAssetsHygiene(unittest.TestCase):
    def test_assets_exist_next_to_script(self):
        for name in bd.ASSET_NAMES:
            self.assertTrue((PROJECT_ROOT / "assets" / name).is_file(), name)

    def test_template_only_references_local_sibling_assets(self):
        html = (PROJECT_ROOT / "assets" / "template.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', html)
        self.assertEqual(sorted(refs), ["dashboard.css", "dashboard.js", "data.js"])

    def test_no_cdn_in_assets(self):
        for name in ("dashboard.css", "dashboard.js", "template.html"):
            text = (PROJECT_ROOT / "assets" / name).read_text(encoding="utf-8")
            self.assertNotIn("http://", text, name)
            self.assertNotIn("https://", text, name)


if __name__ == "__main__":
    unittest.main()
