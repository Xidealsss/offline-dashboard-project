# -*- coding: utf-8 -*-
"""行级校验、任务图校验、环检测。"""

import unittest
from datetime import date

from helpers import bd, chain, collect, messages, task


class TestRowRules(unittest.TestCase):
    def assert_row_error(self, rows, needle, row_no=None):
        tasks, report = collect(rows)
        errs = [i for i in report.issues if i.level == "error"]
        hits = [i for i in errs if needle in i.message]
        self.assertTrue(hits, f"没有报出「{needle}」，实际：{[i.message for i in errs]}")
        if row_no is not None:
            self.assertIn(f"第{row_no}行", hits[0].where)
        return tasks, report

    def test_required_fields(self):
        for field, needle in (("任务ID", "任务ID不能为空"), ("阶段", "阶段不能为空"),
                              ("任务", "任务不能为空"), ("负责人", "负责人不能为空")):
            with self.subTest(field=field):
                self.assert_row_error([task(**{field: ""})], needle, row_no=2)

    def test_enum_fields(self):
        for field, value, needle in (("里程碑", "也许", "里程碑只能填"),
                                     ("优先级", "特急", "优先级只能填"),
                                     ("状态", "在做", "状态只能填"),
                                     ("风险等级", "极高", "风险等级只能填")):
            with self.subTest(field=field):
                self.assert_row_error([task(**{field: value})], needle, row_no=2)

    def test_start_after_end(self):
        self.assert_row_error(
            [task(计划开始=date(2026, 2, 10), 计划结束=date(2026, 2, 1))],
            "计划开始日期晚于计划结束日期", row_no=2)

    def test_progress_out_of_range(self):
        self.assert_row_error([task(**{"进度%": 150})], "超出范围", row_no=2)

    def test_self_dependency(self):
        self.assert_row_error([task(任务ID="T001", 前置任务="T001")], "前置任务不能包含自身")

    def test_blank_rows_are_skipped(self):
        tasks, report = collect([task(任务ID="T001"), task(任务ID="", 阶段="", 任务="",
                                                          负责人="", 协作部门="", 里程碑="",
                                                          优先级="", 状态="", 风险等级="",
                                                          计划开始="", 计划结束="", **{"进度%": ""})])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(messages(report, "error"), [])

    def test_owner_split_on_all_separators(self):
        tasks, _ = collect([task(负责人="张三、李四,王五；赵六")])
        self.assertEqual(tasks[0]["owners"], ["张三", "李四", "王五", "赵六"])

    def test_row_number_points_at_the_real_spreadsheet_row(self):
        rows = [task(任务ID=f"T{i}") for i in range(1, 5)]
        rows[2] = task(任务ID="T3", **{"进度%": 999})
        self.assert_row_error(rows, "超出范围", row_no=4)   # 第 3 条任务 = 表格第 4 行


class TestSheetLevel(unittest.TestCase):
    def test_missing_task_sheet(self):
        _, report = collect([task()], headers=["甲", "乙", "丙"])
        self.assertTrue(any("找不到任务表" in m for m in messages(report, "error")))

    def test_missing_columns(self):
        _, report = collect([task()], headers=["任务ID", "阶段", "任务"])
        self.assertTrue(any("缺少必要列" in m for m in messages(report, "error")))

    def test_empty_task_sheet(self):
        _, report = collect([])
        self.assertIn("任务页没有数据：请至少填写一行任务", messages(report, "error"))


class TestGraph(unittest.TestCase):
    def test_duplicate_id(self):
        _, report = collect([task(任务ID="T001"), task(任务ID="T001")])
        self.assertTrue(any("重复" in m for m in messages(report, "error")))

    def test_missing_predecessor(self):
        _, report = collect([task(任务ID="T001", 前置任务="T404")])
        self.assertTrue(any("「T404」不存在" in m for m in messages(report, "error")))

    def test_cascade_from_broken_row_is_suppressed(self):
        """T001 本行已因进度越界报错，就不该再为 T002 多报一条「前置不存在」。"""
        _, report = collect([
            task(任务ID="T001", **{"进度%": 150}),
            task(任务ID="T002", 前置任务="T001"),
        ])
        errs = messages(report, "error")
        self.assertEqual(len(errs), 1, f"期望只有 1 条错误，实际：{errs}")
        self.assertIn("超出范围", errs[0])

    def test_all_error_classes_reported_in_one_pass(self):
        """旧版遇到重复ID就 sys.exit，用户得修一个跑一次。"""
        _, report = collect([
            task(任务ID="T001"),
            task(任务ID="T001"),                      # 重复
            task(任务ID="T003", 前置任务="T404"),      # 前置不存在
            task(任务ID="T004", 阶段=""),              # 缺必填
        ])
        errs = " | ".join(messages(report, "error"))
        self.assertIn("重复", errs)
        self.assertIn("不存在", errs)
        self.assertIn("阶段不能为空", errs)


class TestDetectCycle(unittest.TestCase):
    @staticmethod
    def nodes(pairs):
        return [{"id": i, "preds": p} for i, p in pairs]

    def test_no_cycle(self):
        self.assertIsNone(bd.detect_cycle(self.nodes([("A", []), ("B", ["A"]), ("C", ["B"])])))

    def test_diamond_is_not_a_cycle(self):
        self.assertIsNone(bd.detect_cycle(
            self.nodes([("A", []), ("B", ["A"]), ("C", ["A"]), ("D", ["B", "C"])])))

    def test_two_node_cycle(self):
        cyc = bd.detect_cycle(self.nodes([("A", ["B"]), ("B", ["A"])]))
        self.assertIsNotNone(cyc)
        self.assertEqual(cyc[0], cyc[-1])

    def test_three_node_cycle(self):
        cyc = bd.detect_cycle(self.nodes([("A", ["C"]), ("B", ["A"]), ("C", ["B"])]))
        self.assertIsNotNone(cyc)
        self.assertEqual(len(set(cyc)), 3)

    def test_disconnected_components(self):
        cyc = bd.detect_cycle(self.nodes([("A", []), ("B", ["A"]), ("X", ["Y"]), ("Y", ["X"])]))
        self.assertIsNotNone(cyc)

    def test_unknown_predecessor_does_not_crash(self):
        self.assertIsNone(bd.detect_cycle(self.nodes([("A", ["缺失"])])))

    def test_long_chain_in_natural_order(self):
        self.assertIsNone(bd.detect_cycle([{"id": f"T{i}", "preds": [f"T{i-1}"] if i else []}
                                           for i in range(1200)]))

    def test_long_chain_reverse_order_does_not_hit_recursion_limit(self):
        """回归守卫：逆序长链会让 DFS 一次探到底。

        改成显式栈迭代前，1200 级就抛未捕获的 RecursionError；这里取 5000 级，
        远超 Python 默认递归上限 1000，确保不是靠调大 limit 蒙混过关。
        """
        rows = [{"id": f"T{i}", "preds": [f"T{i-1}"] if i else []} for i in range(5000)][::-1]
        self.assertIsNone(bd.detect_cycle(rows))

    def test_cycle_found_at_the_end_of_a_long_chain(self):
        """长链末端成环也要能揪出来，且不炸栈。"""
        n = 3000
        rows = [{"id": f"T{i}", "preds": [f"T{i-1}"] if i else [f"T{n-1}"]} for i in range(n)][::-1]
        cyc = bd.detect_cycle(rows)
        self.assertIsNotNone(cyc)
        self.assertEqual(cyc[0], cyc[-1])

    def test_cycle_is_reported_through_full_pipeline(self):
        _, report = collect([
            task(任务ID="T001", 前置任务="T003"),
            task(任务ID="T002", 前置任务="T001"),
            task(任务ID="T003", 前置任务="T002"),
        ])
        self.assertTrue(any("循环依赖" in m for m in messages(report, "error")))


class TestChainHelperSanity(unittest.TestCase):
    def test_chain_builds_a_valid_dag(self):
        tasks, report = collect(chain(20))
        self.assertEqual(len(tasks), 20)
        self.assertEqual(messages(report, "error"), [])


if __name__ == "__main__":
    unittest.main()
