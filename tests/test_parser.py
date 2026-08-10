# -*- coding: utf-8 -*-
"""单元格解析与「项目配置」页读取。"""

import unittest
from datetime import date, datetime

from helpers import bd, collect, messages


class TestParseDate(unittest.TestCase):
    def test_accepts_all_documented_formats(self):
        for text in ("2026-03-09", "2026/03/09", "2026.03.09", "2026年03月09日"):
            with self.subTest(text=text):
                self.assertEqual(bd.parse_date(text, "计划开始"), date(2026, 3, 9))

    def test_accepts_datetime_date_and_excel_serial(self):
        self.assertEqual(bd.parse_date(datetime(2026, 3, 9, 13, 30), "x"), date(2026, 3, 9))
        self.assertEqual(bd.parse_date(date(2026, 3, 9), "x"), date(2026, 3, 9))
        # Excel 序列号：1899-12-30 起算，46090 = 2026-03-09
        self.assertEqual(bd.parse_date(46090, "x"), date(2026, 3, 9))

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(bd.parse_date("  2026-03-09  ", "x"), date(2026, 3, 9))

    def test_rejects_empty_and_garbage(self):
        for bad in (None, "", "下周三", "2026-13-45", "03/09/2026"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bd.parse_date(bad, "计划开始")

    def test_error_message_names_the_field_and_value(self):
        with self.assertRaises(ValueError) as ctx:
            bd.parse_date("下周三", "计划结束")
        self.assertIn("计划结束", str(ctx.exception))
        self.assertIn("下周三", str(ctx.exception))


class TestParseNumber(unittest.TestCase):
    def test_accepts_int_float_str_and_percent(self):
        for raw, want in ((0, 0.0), (80, 80.0), (80.5, 80.5), ("80", 80.0), ("80%", 80.0), (" 80 % ", 80.0)):
            with self.subTest(raw=raw):
                self.assertEqual(bd.parse_number(raw, "进度%"), want)

    def test_accepts_bounds(self):
        self.assertEqual(bd.parse_number(0, "进度%"), 0.0)
        self.assertEqual(bd.parse_number(100, "进度%"), 100.0)

    def test_rejects_out_of_range(self):
        for bad in (-1, 101, "150"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    bd.parse_number(bad, "进度%")
                self.assertIn("超出范围", str(ctx.exception))

    def test_rejects_empty_and_garbage(self):
        for bad in (None, "", "很快", "8 0"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    bd.parse_number(bad, "进度%")


class TestLoadConfig(unittest.TestCase):
    def test_reads_all_fields(self):
        _, report = collect([], config={
            "项目名称": "甲项目", "项目开始日期": date(2026, 2, 1),
            "项目结束日期": date(2026, 8, 1), "预警提前天数": 7, "工作日": "周一至周六",
        })
        self.assertEqual(messages(report, "error"), ["任务页没有数据：请至少填写一行任务"])

    def test_missing_sheet_falls_back_to_defaults(self):
        import tempfile, openpyxl
        from pathlib import Path
        from helpers import make_workbook, task
        with tempfile.TemporaryDirectory() as d:
            p = make_workbook(Path(d) / "t.xlsx", [task()], config=None)
            wb = openpyxl.load_workbook(p)
            report = bd.Report()
            cfg = bd.load_config(wb, report)
        self.assertEqual(cfg["project_name"], "未命名项目")
        self.assertEqual(cfg["warn_days"], 5)
        self.assertIsNone(cfg["start"])
        self.assertEqual(report.issues, [])

    def test_bad_date_is_collected_not_raised(self):
        """旧版这里会让 ValueError 一路冒到顶层，弹出原始 traceback。"""
        _, report = collect([], config={**{"项目名称": "x"}, "项目开始日期": "2026年13月45日"})
        self.assertTrue(any("项目开始日期" in m for m in messages(report, "error")))

    def test_bad_warn_days_is_collected(self):
        _, report = collect([], config={"预警提前天数": "很多天"})
        self.assertIn("预警提前天数应为非负整数", messages(report, "error"))

    def test_start_after_end_is_rejected(self):
        _, report = collect([], config={
            "项目开始日期": date(2026, 9, 1), "项目结束日期": date(2026, 1, 1),
        })
        self.assertIn("项目开始日期晚于项目结束日期", messages(report, "error"))


if __name__ == "__main__":
    unittest.main()
