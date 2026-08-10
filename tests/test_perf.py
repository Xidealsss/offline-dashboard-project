# -*- coding: utf-8 -*-
"""性能基准：PLAN 8.3 的前置步骤。

两类断言，用途不同：

- **回归护栏**（默认跑）：宽松上限，只保证「不比现在更糟」。今天就是绿的。
- **目标断言**（`KANBAN_PERF_TARGET=1` 才跑）：PLAN 8.4 定的验收线
  （2000 行 × 5 年下首屏 ≤ 2s、切片器重绘 ≤ 200ms、HTML ≤ 3MB）。
  优化没落地之前它就该是红的——那正是「完成」的定义。

跑法：
    python3 -m unittest tests.test_perf -v            # 护栏
    KANBAN_PERF_TARGET=1 python3 -m unittest tests.test_perf -v   # 验收

浏览器部分需要 playwright，没装自动跳过。
"""

import json
import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from helpers import PROJECT_ROOT, bd, make_workbook, task

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                   # pragma: no cover
    HAVE_PLAYWRIGHT = False

TARGET_MODE = os.environ.get("KANBAN_PERF_TARGET") == "1"

# 目标线（PLAN 8.4）
TARGET_FIRST_PAINT_MS = 2000
TARGET_SLICER_MS = 200
TARGET_HTML_MB = 3.0

# 回归护栏：宽松，只挡住数量级退化
GUARD_BUILD_S = 60.0
GUARD_HTML_MB = 8.0
GUARD_FIRST_PAINT_MS = 15000
GUARD_SLICER_MS = 3000

# 规模矩阵：(行数, 跨度年数)
MATRIX = [(200, 1), (500, 3), (800, 3), (2000, 5)]
BIG = (2000, 5)          # 浏览器侧只测最大的一档，省时间

STAGES = [f"阶段{i:02d}" for i in range(10)]
OWNERS = [f"成员{i:02d}" for i in range(40)]
DEPTS = ["研发", "测试", "工艺", "采购", "市场", "品质", "生产", "售后"]
STATUSES = ["未开始", "进行中", "待评审", "已完成", "阻塞"]
PRIORITIES = ["高", "中", "低"]
RISKS = ["高", "中", "低"]

_results = []


def perf_rows(n, span_years):
    """造 n 行任务，摊在 span_years 年里，维度基数贴近真实项目。

    每 20 行连成一条前置链，让 CPM 有真活干，又不至于全图一条链。
    """
    start = date(2026, 1, 1)
    span_days = int(span_years * 365)
    rows = []
    for i in range(n):
        offset = (i * span_days) // max(n, 1)
        s = start + timedelta(days=offset)
        e = s + timedelta(days=3 + (i % 12))
        rows.append(task(
            任务ID=f"T{i:05d}",
            阶段=STAGES[i % len(STAGES)],
            任务=f"任务{i:05d}",
            子任务=f"子项{i % 3}" if i % 3 else "",
            负责人=OWNERS[i % len(OWNERS)],
            协作部门=DEPTS[i % len(DEPTS)],
            计划开始=s,
            计划结束=e,
            前置任务=f"T{i - 1:05d}" if i % 20 else "",
            里程碑="是" if i % 50 == 0 else "否",
            优先级=PRIORITIES[i % 3],
            状态=STATUSES[i % 5],
            风险等级=RISKS[i % 3],
            交付物=f"交付物{i}",
            备注="",
            **{"进度%": (i * 7) % 101},
        ))
    return rows, start, start + timedelta(days=span_days)


def build_case(root, n, span_years):
    """造数据 → build → render，返回 (html, 计时字典)。"""
    rows, p_start, p_end = perf_rows(n, span_years)
    xlsx = make_workbook(root / f"perf_{n}_{span_years}.xlsx", rows, config={
        "项目名称": f"性能基准 {n}行×{span_years}年",
        "项目开始日期": p_start,
        "项目结束日期": p_end,
        "预警提前天数": 5,
        "工作日": "周一至周五",
    })

    t0 = time.perf_counter()
    payload, _report = bd.build(xlsx, today=date(2026, 6, 1),
                                max_span=3000, max_rows=5000)
    t_build = time.perf_counter() - t0

    assets = bd.load_assets()
    t1 = time.perf_counter()
    html = bd.render_html(assets, payload)
    t_render = time.perf_counter() - t1

    return html, {
        "rows": n, "years": span_years,
        "build_s": round(t_build, 3),
        "render_s": round(t_render, 3),
        "html_mb": round(len(html.encode("utf-8")) / 1024 / 1024, 2),
        "tasks": len(payload["tasks"]),
    }


class TestBuildScale(unittest.TestCase):
    """Python 侧：解析 + 校验 + CPM + 渲染的耗时与产物体积。"""

    def test_matrix(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for n, years in MATRIX:
                with self.subTest(rows=n, years=years):
                    html, m = build_case(root, n, years)
                    _results.append(m)
                    self.assertEqual(m["tasks"], n)
                    self.assertEqual(bd.external_refs(html), [],
                                     "产物必须自包含，不得有外部引用")
                    self.assertLess(m["build_s"], GUARD_BUILD_S)
                    self.assertLess(m["html_mb"], GUARD_HTML_MB)
                    if TARGET_MODE and (n, years) == BIG:
                        self.assertLessEqual(
                            m["html_mb"], TARGET_HTML_MB,
                            f"HTML {m['html_mb']}MB 超出目标 {TARGET_HTML_MB}MB（PLAN 8.2 瓶颈 5）")

    def test_workbook_is_not_read_twice_when_there_are_no_formulas(self):
        """瓶颈 4：无公式的表不该把工作簿读两遍。"""
        opens = []
        real = bd.openpyxl.load_workbook

        def counting(*a, **kw):
            opens.append(kw.get("data_only"))
            return real(*a, **kw)

        with tempfile.TemporaryDirectory() as d:
            rows, p_start, p_end = perf_rows(50, 1)
            xlsx = make_workbook(Path(d) / "plain.xlsx", rows, config={
                "项目名称": "无公式", "项目开始日期": p_start,
                "项目结束日期": p_end, "预警提前天数": 5, "工作日": "周一至周五",
            })
            bd.openpyxl.load_workbook = counting
            try:
                bd.build(xlsx, today=date(2026, 6, 1), max_span=3000, max_rows=5000)
            finally:
                bd.openpyxl.load_workbook = real
        self.assertEqual(len(opens), 1,
                         f"无公式时应只打开一次工作簿，实际 {len(opens)} 次：{opens}")


@unittest.skipUnless(HAVE_PLAYWRIGHT, "未安装 playwright，跳过浏览器性能基准")
class TestBrowserScale(unittest.TestCase):
    """浏览器侧：首屏与切片器重绘。只测最大的一档。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        html, cls.metrics = build_case(root, *BIG)
        out = root / "perf.html"
        out.write_text(html, encoding="utf-8")
        cls.url = out.resolve().as_uri()
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch()
        except Exception as exc:                       # pragma: no cover
            cls._pw.stop()
            cls._tmp.cleanup()
            raise unittest.SkipTest(f"无法启动 Chromium：{exc}")

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls._tmp.cleanup()

    def _page(self):
        page = self._browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: self.fail(f"页面异常：{e}"))
        return page

    def test_first_paint(self):
        page = self._page()
        try:
            t0 = time.perf_counter()
            page.goto(self.url, wait_until="load")
            page.wait_for_selector("#gantt .col-labels .row", timeout=60000)
            ms = round((time.perf_counter() - t0) * 1000)
        finally:
            page.close()
        _results.append({"rows": BIG[0], "years": BIG[1],
                         "first_paint_ms": ms})
        self.assertLess(ms, GUARD_FIRST_PAINT_MS)
        if TARGET_MODE:
            self.assertLessEqual(ms, TARGET_FIRST_PAINT_MS,
                                 f"首屏 {ms}ms 超出目标 {TARGET_FIRST_PAINT_MS}ms")

    def test_slicer_redraw(self):
        """测最坏情况的同步重绘——瓶颈 1、2 的主战场。

        注意：点一个阶段 chip 是「筛下去」，剩的行反而少，测出来偏乐观。
        真正的最坏情况是**取消筛选、2000 行全量回来**那一次，所以先点一次
        再测第二次。透视维度同时切到基数最高的 负责人(40) × 阶段(10)。
        """
        page = self._page()
        try:
            page.goto(self.url, wait_until="load")
            page.wait_for_selector("#gantt .col-labels .row", timeout=60000)
            page.select_option("#pivotRow", "owner")
            page.select_option("#pivotCol", "stage")
            page.wait_for_timeout(200)
            ms = page.evaluate("""() => {
                const chip = document.querySelector('.slicer-chips .chip');
                chip.click();                 // 先筛掉，只剩一个阶段
                const t0 = performance.now();
                chip.click();                 // 再取消：全部任务回到视图，最坏情况
                return performance.now() - t0;
            }""")
            ms = round(ms)
        finally:
            page.close()
        _results.append({"rows": BIG[0], "years": BIG[1], "slicer_ms": ms})
        self.assertLess(ms, GUARD_SLICER_MS)
        if TARGET_MODE:
            self.assertLessEqual(ms, TARGET_SLICER_MS,
                                 f"切片器重绘 {ms}ms 超出目标 {TARGET_SLICER_MS}ms")

    def test_pivot_dimension_switch(self):
        """切透视维度的耗时。当前实现会连带走一遍完整 refresh()。"""
        page = self._page()
        try:
            page.goto(self.url, wait_until="load")
            page.wait_for_selector("#gantt .col-labels .row", timeout=60000)
            ms = page.evaluate("""() => {
                const row = document.getElementById('pivotRow');
                const col = document.getElementById('pivotCol');
                col.value = 'stage';
                col.dispatchEvent(new Event('change'));
                row.value = 'owner';
                const t0 = performance.now();
                row.dispatchEvent(new Event('change'));
                return performance.now() - t0;
            }""")
            ms = round(ms)
        finally:
            page.close()
        _results.append({"rows": BIG[0], "years": BIG[1], "pivot_ms": ms})
        self.assertLess(ms, GUARD_SLICER_MS)

    def test_pivot_matches_a_brute_force_count(self):
        """瓶颈 1 的正确性护栏：聚合改写后，格子数字必须和暴力统计一致。"""
        page = self._page()
        try:
            page.goto(self.url, wait_until="load")
            page.wait_for_selector("#gantt .col-labels .row", timeout=60000)
            page.select_option("#pivotRow", "owner")
            page.select_option("#pivotCol", "stage")
            page.wait_for_timeout(200)
            ok = page.evaluate("""() => {
                const rows = [...document.querySelectorAll('.pivot-table tbody tr')]
                    .filter(tr => !tr.classList.contains('total-row'));
                const cols = [...document.querySelectorAll('.pivot-table thead th')]
                    .slice(1, -1).map(th => th.textContent);
                const brute = {};
                DATA.tasks.forEach(t => {
                    brute[t.ownerText + '\\u0000' + t.stage] =
                        (brute[t.ownerText + '\\u0000' + t.stage] || 0) + 1;
                });
                for (const tr of rows) {
                    const name = tr.querySelector('th').textContent;
                    const tds = [...tr.querySelectorAll('td')].slice(0, cols.length);
                    for (let i = 0; i < cols.length; i++) {
                        const want = brute[name + '\\u0000' + cols[i]] || 0;
                        if (Number(tds[i].textContent) !== want) {
                            return `${name} × ${cols[i]}: 页面 ${tds[i].textContent} ≠ 实际 ${want}`;
                        }
                    }
                }
                return true;
            }""")
        finally:
            page.close()
        self.assertIs(ok, True, ok)


def tearDownModule():
    """把基线数字打出来，方便回填 PLAN 8.3。"""
    if not _results:
        return
    merged = {}
    for r in _results:
        merged.setdefault((r["rows"], r["years"]), {}).update(r)
    print("\n" + "=" * 62)
    print("性能基线（KANBAN_PERF_TARGET=1 可切到 PLAN 8.4 的验收线）")
    print("-" * 62)
    print(f"{'规模':>12} {'build':>8} {'render':>8} {'HTML':>9}"
          f" {'首屏':>8} {'切片器':>8} {'切透视':>8}")
    for (n, y), m in sorted(merged.items()):
        print(f"{n:>5}行×{y}年 {m.get('build_s', '-'):>8} {m.get('render_s', '-'):>8}"
              f" {str(m.get('html_mb', '-')) + 'MB':>9}"
              f" {str(m.get('first_paint_ms', '-')) + 'ms':>8}"
              f" {str(m.get('slicer_ms', '-')) + 'ms':>8}"
              f" {str(m.get('pivot_ms', '-')) + 'ms':>8}")
    print("=" * 62)
    (PROJECT_ROOT / "tests" / "perf-baseline.json").write_text(
        json.dumps([merged[k] for k in sorted(merged)], ensure_ascii=False, indent=2),
        encoding="utf-8")
