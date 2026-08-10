# -*- coding: utf-8 -*-
"""阶段 4a：关键路径与浮动时差（CPM 正推/逆推）。

口径：按日历日。「项目配置」的「工作日」字段目前只作展示，不参与推算。
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import bd, collect, make_workbook, task


def sched(rows):
    tasks, report = collect(rows)
    assert not report.errors, [i.message for i in report.errors]
    return bd.compute_schedule(tasks), {t["id"]: t for t in tasks}


def days(a, b):
    return (b - a).days + 1


class TestForwardPass(unittest.TestCase):
    def test_task_without_predecessor_anchors_on_its_planned_start(self):
        s, _ = sched([task(任务ID="A", 计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 6))])
        self.assertEqual(date.fromordinal(s["A"]["es"]), date(2026, 3, 2))
        self.assertEqual(date.fromordinal(s["A"]["ef"]), date(2026, 3, 6))

    def test_successor_starts_the_day_after_its_predecessor_finishes(self):
        s, _ = sched([
            task(任务ID="A", 计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 6)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 3, 9), 计划结束=date(2026, 3, 13)),
        ])
        self.assertEqual(date.fromordinal(s["B"]["es"]), date(2026, 3, 7))
        self.assertEqual(date.fromordinal(s["B"]["ef"]), date(2026, 3, 11))   # 工期 5 天

    def test_multiple_predecessors_take_the_latest(self):
        s, _ = sched([
            task(任务ID="A", 计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 6)),
            task(任务ID="B", 计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 20)),
            task(任务ID="C", 前置任务="A,B", 计划开始=date(2026, 3, 23), 计划结束=date(2026, 3, 25)),
        ])
        self.assertEqual(date.fromordinal(s["C"]["es"]), date(2026, 3, 21))   # 跟着更晚的 B


class TestFloatAndCriticality(unittest.TestCase):
    def test_serial_chain_is_entirely_critical(self):
        rows = [task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 5)),
                task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 6), 计划结束=date(2026, 1, 10)),
                task(任务ID="C", 前置任务="B", 计划开始=date(2026, 1, 11), 计划结束=date(2026, 1, 15))]
        s, _ = sched(rows)
        self.assertTrue(all(s[i]["critical"] for i in "ABC"))
        self.assertTrue(all(s[i]["tf"] == 0 for i in "ABC"))

    def test_short_parallel_branch_carries_the_float(self):
        """A→{B 长, C 短}→D：C 不在关键路径上，浮动 = 两条支路的工期差。"""
        rows = [
            task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 2)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 3), 计划结束=date(2026, 1, 22)),  # 20 天
            task(任务ID="C", 前置任务="A", 计划开始=date(2026, 1, 3), 计划结束=date(2026, 1, 7)),   # 5 天
            task(任务ID="D", 前置任务="B,C", 计划开始=date(2026, 1, 23), 计划结束=date(2026, 1, 25)),
        ]
        s, _ = sched(rows)
        self.assertTrue(s["A"]["critical"])
        self.assertTrue(s["B"]["critical"])
        self.assertTrue(s["D"]["critical"])
        self.assertFalse(s["C"]["critical"])
        self.assertEqual(s["C"]["tf"], 15)          # 20 - 5

    def test_equal_length_branches_are_both_critical(self):
        rows = [
            task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 2)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 3), 计划结束=date(2026, 1, 12)),
            task(任务ID="C", 前置任务="A", 计划开始=date(2026, 1, 3), 计划结束=date(2026, 1, 12)),
            task(任务ID="D", 前置任务="B,C", 计划开始=date(2026, 1, 13), 计划结束=date(2026, 1, 14)),
        ]
        s, _ = sched(rows)
        self.assertTrue(all(s[i]["critical"] for i in "ABCD"))

    def test_dead_end_task_gets_float_to_project_finish(self):
        """没有后继的支线任务，浮动一直算到全项目完工。"""
        rows = [
            task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 5)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 6), 计划结束=date(2026, 3, 1)),
            task(任务ID="X", 前置任务="A", 计划开始=date(2026, 1, 6), 计划结束=date(2026, 1, 8)),
        ]
        s, _ = sched(rows)
        self.assertTrue(s["B"]["critical"])
        self.assertFalse(s["X"]["critical"])
        self.assertGreater(s["X"]["tf"], 0)

    def test_late_dates_are_never_earlier_than_early_dates(self):
        rows = [task(任务ID=f"T{i}", 前置任务=f"T{i-1}" if i else "",
                     计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 3)) for i in range(10)]
        s, _ = sched(rows)
        for tid, v in s.items():
            self.assertGreaterEqual(v["ls"], v["es"], tid)
            self.assertGreaterEqual(v["lf"], v["ef"], tid)
            self.assertGreaterEqual(v["tf"], 0, tid)

    def test_duration_is_preserved_between_early_and_late_windows(self):
        rows = [task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 10)),
                task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 11), 计划结束=date(2026, 1, 12))]
        s, by = sched(rows)
        for tid, v in s.items():
            dur = days(by[tid]["start"], by[tid]["end"])
            self.assertEqual(v["ef"] - v["es"] + 1, dur)
            self.assertEqual(v["lf"] - v["ls"] + 1, dur)


class TestEdgeCases(unittest.TestCase):
    def test_single_task_is_critical(self):
        s, _ = sched([task(任务ID="A")])
        self.assertTrue(s["A"]["critical"])

    def test_empty_input(self):
        self.assertEqual(bd.compute_schedule([]), {})

    def test_disconnected_components_are_both_scheduled(self):
        rows = [
            task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 5)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 6), 计划结束=date(2026, 1, 10)),
            task(任务ID="X", 计划开始=date(2026, 2, 1), 计划结束=date(2026, 2, 3)),
        ]
        s, _ = sched(rows)
        self.assertEqual(set(s), {"A", "B", "X"})
        self.assertEqual(date.fromordinal(s["X"]["es"]), date(2026, 2, 1))

    def test_unknown_predecessor_is_ignored_by_the_scheduler(self):
        """校验层已会为「前置不存在」报错；调度器本身不能因此崩掉。"""
        s = bd.compute_schedule([
            {"id": "A", "preds": ["幽灵"], "start": date(2026, 1, 1), "end": date(2026, 1, 3)},
        ])
        self.assertIn("A", s)

    def test_long_chain_does_not_blow_up(self):
        rows = [{"id": f"T{i}", "preds": [f"T{i-1}"] if i else [],
                 "start": date(2026, 1, 1), "end": date(2026, 1, 2)} for i in range(2000)]
        s = bd.compute_schedule(rows)
        self.assertEqual(len(s), 2000)
        self.assertTrue(all(v["critical"] for v in s.values()))


class TestPayloadFields(unittest.TestCase):
    def payload(self, rows):
        with tempfile.TemporaryDirectory() as d:
            p = make_workbook(Path(d) / "t.xlsx", rows, config=None)
            return bd.build(p)[0]

    def test_cpm_fields_are_iso_dates(self):
        p = self.payload([task(任务ID="A", 计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 6))])
        t = p["tasks"][0]
        self.assertEqual(t["es"], "2026-03-02")
        self.assertEqual(t["ef"], "2026-03-06")
        self.assertIsInstance(t["tf"], int)
        self.assertIs(t["critical"], True)

    def test_summary_counts_are_exposed(self):
        p = self.payload([
            task(任务ID="A", 计划开始=date(2026, 1, 1), 计划结束=date(2026, 1, 10)),
            task(任务ID="B", 前置任务="A", 计划开始=date(2026, 1, 11), 计划结束=date(2026, 1, 20)),
            task(任务ID="X", 前置任务="A", 计划开始=date(2026, 1, 11), 计划结束=date(2026, 1, 12)),
        ])
        self.assertEqual(p["criticalCount"], 2)
        self.assertEqual(p["criticalDuration"], 20)      # A(10) + B(10)

    def test_real_project_has_a_sane_critical_path(self):
        from helpers import REAL_XLSX
        p = bd.build(REAL_XLSX)[0]
        crit = [t for t in p["tasks"] if t["critical"]]
        self.assertTrue(crit, "真实项目应至少有一条关键路径")
        self.assertTrue(all(t["tf"] == 0 for t in crit))
        self.assertTrue(all(t["tf"] > 0 for t in p["tasks"] if not t["critical"]))


if __name__ == "__main__":
    unittest.main()
