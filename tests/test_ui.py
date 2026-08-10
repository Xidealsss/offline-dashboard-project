# -*- coding: utf-8 -*-
"""浏览器端回归：几何对齐、类名污染、规则引擎、响应式、交互联动。

需要 playwright；没装就整体跳过，不影响其余单测：
    pip install playwright && playwright install chromium
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import REAL_XLSX, make_workbook, run_cli, task

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                                   # pragma: no cover
    HAVE_PLAYWRIGHT = False

SKIP_REASON = "未安装 playwright，跳过 UI 回归（pip install playwright && playwright install chromium）"

# 固定「今天」，让状态判定完全可预期
TODAY = "2026-03-15"          # 周日；warnDays = 5
DAY_W = 28

_ctx = {}


def setUpModule():
    if not HAVE_PLAYWRIGHT:
        raise unittest.SkipTest(SKIP_REASON)
    _ctx["tmp"] = tempfile.TemporaryDirectory()
    root = Path(_ctx["tmp"].name)

    # 每条任务对应规则引擎的一个分支，见 computeState 的判定顺序
    rows = [
        task(任务ID="A", 阶段="一期", 任务="已完工", 负责人="张三",
             计划开始=date(2026, 2, 10), 计划结束=date(2026, 3, 1), **{"进度%": 100}),
        task(任务ID="B", 阶段="一期", 任务="已拖期", 负责人="张三",
             计划开始=date(2026, 3, 2), 计划结束=date(2026, 3, 10), **{"进度%": 0}),
        task(任务ID="C", 阶段="一期", 任务="临期预警", 负责人="李四",
             计划开始=date(2026, 3, 5), 计划结束=date(2026, 3, 18), **{"进度%": 50}),
        task(任务ID="D", 阶段="二期", 任务="被阻塞", 负责人="李四", 状态="阻塞",
             计划开始=date(2026, 4, 1), 计划结束=date(2026, 4, 30), **{"进度%": 0}),
        task(任务ID="E", 阶段="二期", 任务="待评审", 负责人="王五", 状态="待评审",
             计划开始=date(2026, 4, 1), 计划结束=date(2026, 4, 30), **{"进度%": 0}),
        task(任务ID="F", 阶段="二期", 任务="进行中", 负责人="王五",
             计划开始=date(2026, 4, 1), 计划结束=date(2026, 4, 30), **{"进度%": 30}),
        task(任务ID="G", 阶段="二期", 任务="未开始", 负责人="王五",
             计划开始=date(2026, 4, 1), 计划结束=date(2026, 4, 30), **{"进度%": 0}),
        task(任务ID="H", 阶段="二期", 任务="里程碑", 负责人="赵六", 里程碑="是",
             计划开始=date(2026, 4, 10), 计划结束=date(2026, 4, 20), **{"进度%": 0}),
    ]
    xlsx = make_workbook(root / "fixture.xlsx", rows, config={
        "项目名称": "UI 回归项目",
        "项目开始日期": date(2026, 2, 1),
        "项目结束日期": date(2026, 5, 31),
        "预警提前天数": 5,
        "工作日": "周一至周五",
    })
    out = root / "fixture.html"
    code, _, stderr = run_cli("--input", xlsx, "--output", out)
    if code != 0:
        raise RuntimeError(f"夹具看板构建失败：{stderr}")
    _ctx["url"] = out.resolve().as_uri()

    real_out = root / "real.html"
    if run_cli("--input", REAL_XLSX, "--output", real_out)[0] == 0:
        _ctx["real_url"] = real_out.resolve().as_uri()

    _ctx["pw"] = sync_playwright().start()
    try:
        _ctx["browser"] = _ctx["pw"].chromium.launch()
    except Exception as exc:                          # pragma: no cover
        _ctx["pw"].stop()
        raise unittest.SkipTest(f"无法启动 Chromium：{exc}")


def tearDownModule():
    if _ctx.get("browser"):
        _ctx["browser"].close()
    if _ctx.get("pw"):
        _ctx["pw"].stop()
    if _ctx.get("tmp"):
        _ctx["tmp"].cleanup()


class UITestCase(unittest.TestCase):
    """每个用例开一个干净页面，并把 console error / pageerror 收集起来。"""

    width = 1440
    query = f"?today={TODAY}"
    url_key = "url"

    def setUp(self):
        self.errors = []
        self.page = _ctx["browser"].new_page(viewport={"width": self.width, "height": 950})
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console",
                     lambda m: self.errors.append(f"console: {m.text}") if m.type == "error" else None)
        self.page.goto(_ctx[self.url_key] + self.query)
        self.page.wait_for_timeout(250)

    def tearDown(self):
        self.assertEqual(self.errors, [], "页面不应有任何 console error / pageerror")
        self.page.close()

    def js(self, expr):
        return self.page.evaluate(expr)


class TestStatCards(UITestCase):
    def test_all_eight_cards_render_in_the_grid(self):
        """回归守卫：曾因 .stat milestone 撞上甘特菱形的 .milestone，
        整张「里程碑完成率」卡被 position:absolute + rotate(45deg) 踢出栅格，只显示 7 张。"""
        info = self.js("""() => {
          const s=[...document.querySelectorAll('.stat')];
          return {n:s.length,
                  positions:[...new Set(s.map(e=>getComputedStyle(e).position))],
                  transforms:[...new Set(s.map(e=>getComputedStyle(e).transform))],
                  rows:[...new Set(s.map(e=>Math.round(e.getBoundingClientRect().top)))].length,
                  labels:s.map(e=>e.querySelector('.label').textContent),
                  widths:[...new Set(s.map(e=>Math.round(e.getBoundingClientRect().width)))]};
        }""")
        self.assertEqual(info["n"], 8)
        self.assertEqual(info["positions"], ["static"], "指标卡不应被任何规则改成定位元素")
        self.assertEqual(info["transforms"], ["none"], "指标卡不应被任何规则加上 transform")
        self.assertEqual(info["rows"], 1, "1440px 下 8 张卡应在同一行")
        self.assertIn("里程碑完成率", info["labels"])
        self.assertEqual(len(info["widths"]), 1, "同一行的卡片应等宽")

    def test_card_values_match_the_fixture(self):
        got = dict(self.js(
            "()=>[...document.querySelectorAll('.stat')].map(e=>"
            "[e.querySelector('.label').textContent, e.querySelector('.num').textContent])"))
        self.assertEqual(got, {
            "总任务数": "8", "已完成": "1", "进行中": "1", "待评审": "1",
            "阻塞": "1", "拖期数": "1", "里程碑完成率": "0%", "本周到期": "1",
        })

    def test_no_unexpected_absolutely_positioned_elements(self):
        """通用污染扫描：绝对定位只应出现在明确需要它的甘特/装饰元素上。"""
        strays = self.js("""() => {
          const ok = ['bar','fill','milestone','ms-span','today-line','today-flag'];
          return [...document.querySelectorAll('.page *')]
            .filter(e => getComputedStyle(e).position === 'absolute')
            .filter(e => !ok.some(c => e.classList.contains(c)))
            .map(e => e.tagName + '.' + e.className);
        }""")
        self.assertEqual(strays, [])


class TestGanttGeometry(UITestCase):
    def test_no_vertical_drift_between_the_two_columns(self):
        r = self.js("""() => {
          const L=[...document.querySelectorAll('.col-labels .row')];
          const T=[...document.querySelectorAll('.col-timeline .row')];
          const GL=[...document.querySelectorAll('.group-row')];
          const GT=[...document.querySelectorAll('.group-row-time')];
          return {rows:[L.length,T.length], groups:[GL.length,GT.length],
            rowDrift: Math.max(0,...L.map((l,i)=>Math.abs(l.getBoundingClientRect().top-T[i].getBoundingClientRect().top))),
            groupDrift: Math.max(0,...GL.map((l,i)=>Math.abs(l.getBoundingClientRect().top-GT[i].getBoundingClientRect().top))),
            heights:[document.querySelector('.col-labels').getBoundingClientRect().height,
                     document.querySelector('.col-timeline').getBoundingClientRect().height]};
        }""")
        self.assertEqual(r["rows"][0], r["rows"][1])
        self.assertEqual(r["groups"][0], r["groups"][1])
        self.assertEqual(r["rowDrift"], 0, "左右两列逐行不得有任何垂直漂移")
        self.assertEqual(r["groupDrift"], 0)
        self.assertEqual(r["heights"][0], r["heights"][1], "两列总高必须一致")

    def test_header_day_cells_align_with_lane_origin(self):
        r = self.js("""() => {
          const lane=document.querySelector('.lane').getBoundingClientRect();
          const days=[...document.querySelectorAll('.day-row .day')];
          return {offsets: days.slice(0,12).map(d=>+(d.getBoundingClientRect().left-lane.left).toFixed(2)),
                  widths:[...new Set(days.map(d=>Math.round(d.getBoundingClientRect().width)))],
                  count: days.length, laneW: Math.round(lane.width)};
        }""")
        self.assertEqual(r["widths"], [DAY_W])
        self.assertEqual(r["offsets"], [i * DAY_W for i in range(12)],
                         "表头日格必须精确落在泳道的 28px 整数倍上")
        self.assertEqual(r["laneW"], r["count"] * DAY_W)

    def test_week_segments_tile_the_whole_timeline(self):
        r = self.js("""() => {
          const w=[...document.querySelectorAll('.week')];
          return {sum: w.reduce((a,e)=>a+e.getBoundingClientRect().width,0),
                  laneW: document.querySelector('.lane').getBoundingClientRect().width,
                  overflow: w.filter(e=>e.scrollWidth>e.clientWidth+1).length};
        }""")
        self.assertAlmostEqual(r["sum"], r["laneW"], delta=1,
                               msg="周格总宽必须等于时间轴总宽")
        self.assertEqual(r["overflow"], 0, "周标签不得溢出所在周格（旧版会换行压住日号）")

    def test_weekend_shading_covers_exactly_the_header_weekend_cells(self):
        """泳道周末底色是一层按 7 天平铺、用 --wk-x 定相位的渐变。
        逐日核对「渐变算出来该不该染色」与「表头是不是周末格」完全一致——
        时间轴从周中甚至周日开始时最容易错相，这里覆盖到。"""
        r = self.js("""() => {
          const days=[...document.querySelectorAll('.day-row .day')];
          const wkx=parseFloat(getComputedStyle(document.querySelector('.gantt'))
                    .getPropertyValue('--wk-x')) || 0;
          const period=28*7, shaded=28*2;
          const mismatch=[];
          days.forEach((d,i)=>{
            const painted=(((i*28-wkx)%period)+period)%period < shaded;
            if (painted !== d.classList.contains('we')) mismatch.push(i);
          });
          const weekend=days.map((d,i)=>d.classList.contains('we')?i:-1).filter(i=>i>=0);
          return {mismatch, weekend: weekend.slice(0,5), total: days.length};
        }""")
        self.assertEqual(r["mismatch"], [],
                         "泳道渐变染色的日子必须与表头周末格逐日一致")
        self.assertGreater(len(r["weekend"]), 0, "夹具时间轴应当包含周末")

    def test_task_labels_are_not_clipped(self):
        clipped = self.js("()=>[...document.querySelectorAll('.row-label')]"
                          ".filter(e=>e.scrollHeight>e.clientHeight).length")
        self.assertEqual(clipped, 0, "任务名/副行不得被行高裁切")

    def test_milestone_diamond_centres_on_its_end_date(self):
        r = self.js("""() => {
          const rows=[...document.querySelectorAll('.col-timeline .row')];
          const idx=[...document.querySelectorAll('.col-labels .row')]
            .findIndex(r=>r.textContent.includes('里程碑'));
          const lane=rows[idx].querySelector('.lane').getBoundingClientRect();
          const m=rows[idx].querySelector('.milestone').getBoundingClientRect();
          return {centre:+((m.left+m.width/2)-lane.left).toFixed(1)};
        }""")
        # H 计划结束 2026-04-20，时间轴自 2026-02-01 起算 → 第 78 天，格中心 = 78*28+14
        self.assertAlmostEqual(r["centre"], 78 * DAY_W + DAY_W / 2, delta=1.5)

    def test_sticky_label_column_survives_horizontal_scroll(self):
        r = self.js("""async () => {
          const sc=document.querySelector('.board-scroll');
          sc.scrollLeft=900; await new Promise(r=>requestAnimationFrame(r));
          const lane=document.querySelector('.lane').getBoundingClientRect();
          return {labelsLeft:+document.querySelector('.col-labels').getBoundingClientRect().left.toFixed(1),
                  boardLeft:+sc.getBoundingClientRect().left.toFixed(1),
                  headerVsLane:+(document.querySelector('.day-row .day').getBoundingClientRect().left-lane.left).toFixed(2)};
        }""")
        self.assertEqual(r["labelsLeft"], r["boardLeft"], "任务列应横向冻结")
        self.assertEqual(r["headerVsLane"], 0, "横滚后刻度与泳道仍须同步")


class TestRuleEngine(UITestCase):
    """?today= 驱动的状态判定矩阵。"""

    def states_at(self, today):
        self.page.goto(_ctx["url"] + f"?today={today}")
        self.page.wait_for_timeout(200)
        return self.js("""()=>[...document.querySelectorAll('.lane .bar,.lane .milestone')]
            .reduce((a,e)=>{const k=[...e.classList].find(c=>c!=='bar'&&c!=='milestone');
                            a[k]=(a[k]||0)+1; return a;},{})""")

    def test_fixture_day_hits_every_branch(self):
        self.assertEqual(self.states_at(TODAY), {
            "completed": 1, "overdue": 1, "warning": 1, "blocked": 1,
            "review": 1, "inprogress": 1, "notstarted": 2,
        })

    def test_before_project_start_nothing_is_overdue(self):
        s = self.states_at("2026-02-02")
        self.assertNotIn("overdue", s)
        self.assertNotIn("warning", s)
        self.assertEqual(s.get("completed"), 1)

    def test_after_project_end_everything_unfinished_is_overdue(self):
        s = self.states_at("2026-07-01")
        self.assertEqual(s, {"completed": 1, "overdue": 7})

    def test_completion_outranks_overdue(self):
        """A 的计划结束早于今天但进度 100%，必须判已完成而不是拖期。"""
        cls = self.js("""()=>{const i=[...document.querySelectorAll('.col-labels .row')]
              .findIndex(r=>r.textContent.includes('已完工'));
            return [...document.querySelectorAll('.col-timeline .row')][i]
              .querySelector('.bar').className;}""")
        self.assertIn("completed", cls)


class TestInteraction(UITestCase):
    def test_slicer_keeps_every_component_consistent(self):
        self.page.click(".slicer-group:nth-child(1) .chip:nth-child(1)")   # 阶段 = 一期
        self.page.wait_for_timeout(200)
        r = self.js("""() => ({
          total:+document.querySelector('.stat .num').textContent,
          ganttRows: document.querySelectorAll('.col-labels .row').length,
          pivotTotal:+[...document.querySelectorAll('.pivot-table tbody tr:last-child td')].pop().textContent,
          chartSum:[...document.querySelectorAll('.chart-card')][0]
            .querySelectorAll('rect').length,
        })""")
        self.assertEqual(r["total"], 3, "一期共 3 条任务")
        self.assertEqual(r["ganttRows"], 3)
        self.assertEqual(r["pivotTotal"], 3, "透视表合计应与指标卡一致")

    def test_clear_restores_all_rows(self):
        self.page.click(".slicer-group:nth-child(1) .chip:nth-child(1)")
        self.page.wait_for_timeout(150)
        self.page.click('.slicer-group:nth-child(1) [data-act="clear"]')
        self.page.wait_for_timeout(150)
        self.assertEqual(self.js("()=>document.querySelectorAll('.col-labels .row').length"), 8)

    def test_select_all_selects_every_chip(self):
        self.page.click('.slicer-group:nth-child(1) [data-act="all"]')
        self.page.wait_for_timeout(150)
        r = self.js("""()=>{const g=document.querySelector('.slicer-group:nth-child(1)');
            return [g.querySelectorAll('.chip').length, g.querySelectorAll('.chip.active').length];}""")
        self.assertEqual(r[0], r[1])

    def test_detail_highlights_both_columns(self):
        self.page.click(".col-labels .row-label")
        self.page.wait_for_timeout(200)
        r = self.js("""()=>({shown:document.querySelector('.detail').classList.contains('show'),
             left:document.querySelectorAll('.col-labels .row.selected').length,
             right:document.querySelectorAll('.col-timeline .row.selected').length})""")
        self.assertTrue(r["shown"])
        self.assertEqual((r["left"], r["right"]), (1, 1), "选中态左右两列必须同步")

    def test_scroll_position_survives_a_refilter(self):
        r = self.js("""async () => {
          const sc=document.querySelector('.board-scroll'); sc.scrollLeft=700;
          await new Promise(r=>requestAnimationFrame(r));
          document.querySelector('.slicer-group:nth-child(1) .chip:nth-child(1)').click();
          await new Promise(r=>requestAnimationFrame(r));
          return sc.scrollLeft;
        }""")
        self.assertEqual(r, 700, "重新筛选不应把甘特滚动位置重置到最左")

    def test_pivot_dimension_switch_recomputes(self):
        self.page.select_option("#pivotVal", "avg")
        self.page.select_option("#pivotRow", "owner")
        self.page.wait_for_timeout(200)
        head = self.js("()=>document.querySelector('.pivot-table tbody th').textContent")
        self.assertIn(head, ["张三", "李四", "王五", "赵六"])

    def test_pivot_freezes_header_corner_with_row_headers(self):
        r = self.js("""async () => {
          const w=document.querySelector('.pivot-wrap');
          w.scrollLeft=w.scrollWidth; await new Promise(r=>requestAnimationFrame(r));
          const box=w.getBoundingClientRect();
          return [+(document.querySelector('thead th:first-child').getBoundingClientRect().left-box.left).toFixed(1),
                  +(document.querySelector('tbody th').getBoundingClientRect().left-box.left).toFixed(1)];
        }""")
        self.assertEqual(r[0], r[1], "透视表表头首格必须与行表头一起冻结")


class TestResponsive(unittest.TestCase):
    WIDTHS = (1440, 1280, 1100, 820, 600)

    @unittest.skipUnless(HAVE_PLAYWRIGHT, SKIP_REASON)
    def test_layout_holds_at_every_breakpoint(self):
        for w in self.WIDTHS:
            with self.subTest(width=w):
                page = _ctx["browser"].new_page(viewport={"width": w, "height": 950})
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                page.goto(_ctx["url"] + f"?today={TODAY}")
                page.wait_for_timeout(200)
                r = page.evaluate("""() => {
                  const tops=n=>[...new Set([...document.querySelectorAll(n)]
                      .map(e=>Math.round(e.getBoundingClientRect().top)))];
                  const L=[...document.querySelectorAll('.col-labels .row')];
                  const T=[...document.querySelectorAll('.col-timeline .row')];
                  const perRow={};
                  [...document.querySelectorAll('.stat')].forEach(e=>{
                    const k=Math.round(e.getBoundingClientRect().top);
                    perRow[k]=(perRow[k]||0)+1;});
                  return {overflow: document.documentElement.scrollWidth-document.documentElement.clientWidth,
                          statPerRow: Object.values(perRow),
                          chartWidths:[...new Set([...document.querySelectorAll('.chart-card')]
                              .map(e=>Math.round(e.getBoundingClientRect().width)))],
                          drift: Math.max(0,...L.map((l,i)=>Math.abs(
                              l.getBoundingClientRect().top-T[i].getBoundingClientRect().top))),
                          clipped:[...document.querySelectorAll('.row-label')]
                              .filter(e=>e.scrollHeight>e.clientHeight).length};
                }""")
                page.close()
                self.assertEqual(errs, [])
                self.assertEqual(r["overflow"], 0, f"{w}px 下页面不应横向溢出")
                self.assertEqual(len(set(r["statPerRow"][:-1])) <= 1, True,
                                 f"{w}px 下指标卡断行不均匀：{r['statPerRow']}")
                self.assertEqual(len(r["chartWidths"]), 1, f"{w}px 下三张图表应等宽")
                self.assertEqual(r["drift"], 0)
                self.assertEqual(r["clipped"], 0)


class TestRealProjectSmoke(UITestCase):
    """针对真实数据源的冒烟：只断言结构不变量，避免用户改表就红。"""

    url_key = "real_url"
    query = ""

    def setUp(self):
        if "real_url" not in _ctx:
            self.skipTest("真实数据源未能构建")
        super().setUp()

    def test_structure_is_sound(self):
        r = self.js("""() => {
          const L=[...document.querySelectorAll('.col-labels .row')];
          const T=[...document.querySelectorAll('.col-timeline .row')];
          return {stats:document.querySelectorAll('.stat').length,
                  rows:[L.length,T.length],
                  tasks: DATA.tasks.length,
                  drift: Math.max(0,...L.map((l,i)=>Math.abs(
                      l.getBoundingClientRect().top-T[i].getBoundingClientRect().top))),
                  overflow: document.documentElement.scrollWidth-document.documentElement.clientWidth};
        }""")
        self.assertEqual(r["stats"], 8)
        self.assertEqual(r["rows"][0], r["rows"][1])
        self.assertEqual(r["rows"][0], r["tasks"])
        self.assertEqual(r["drift"], 0)
        self.assertEqual(r["overflow"], 0)


if __name__ == "__main__":
    unittest.main()


class TestZoom(UITestCase):
    """日 / 周 / 月三档粒度下几何必须同样严丝合缝。"""

    UNITS = ("day", "week", "month")

    def switch(self, u):
        self.page.click(f'#unitSwitch [data-unit="{u}"]')
        self.page.wait_for_timeout(220)

    def geometry(self):
        return self.js("""() => {
          const L=[...document.querySelectorAll('.col-labels .row')];
          const T=[...document.querySelectorAll('.col-timeline .row')];
          const upper=[...document.querySelectorAll('.week')];
          const lower=[...document.querySelectorAll('.day-row .day')];
          const sum=a=>a.reduce((x,e)=>x+e.getBoundingClientRect().width,0);
          return {drift: Math.max(0,...L.map((l,i)=>Math.abs(
                    l.getBoundingClientRect().top-T[i].getBoundingClientRect().top))),
                  laneW:+document.querySelector('.lane').getBoundingClientRect().width.toFixed(1),
                  upperSum:+sum(upper).toFixed(1), lowerSum:+sum(lower).toFixed(1),
                  cells:[upper.length, lower.length],
                  clipped:[...document.querySelectorAll('.row-label')]
                      .filter(e=>e.scrollHeight>e.clientHeight).length,
                  labelOverflow: upper.concat(lower)
                      .filter(e=>e.scrollWidth>e.clientWidth+1
                              || e.scrollHeight>e.clientHeight+1).length};
        }""")

    def test_all_three_units_stay_aligned(self):
        for u in self.UNITS:
            with self.subTest(unit=u):
                self.switch(u)
                g = self.geometry()
                self.assertEqual(g["drift"], 0, f"{u} 视图两列出现漂移")
                self.assertAlmostEqual(g["upperSum"], g["laneW"], delta=1.5,
                                       msg=f"{u} 视图上行刻度总宽与泳道不符")
                self.assertAlmostEqual(g["lowerSum"], g["laneW"], delta=1.5,
                                       msg=f"{u} 视图下行刻度总宽与泳道不符")
                self.assertEqual(g["clipped"], 0)
                self.assertEqual(g["labelOverflow"], 0, f"{u} 视图刻度标签溢出格子")

    def test_coarser_units_shrink_the_timeline(self):
        widths = []
        for u in self.UNITS:
            self.switch(u)
            widths.append(self.geometry()["laneW"])
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_bar_position_tracks_the_same_date_across_units(self):
        """同一任务在不同粒度下应落在时间轴的同一比例位置。"""
        ratios = []
        for u in self.UNITS:
            self.switch(u)
            ratios.append(self.js("""() => {
              const lane=document.querySelector('.lane').getBoundingClientRect();
              const bar=document.querySelector('.lane .bar').getBoundingClientRect();
              return (bar.left-lane.left)/lane.width;
            }"""))
        for r in ratios[1:]:
            self.assertAlmostEqual(r, ratios[0], places=2)

    def test_weekend_shading_only_in_day_view(self):
        self.switch("day")
        self.assertGreater(self.js("()=>document.querySelectorAll('.day-row .day.we').length"), 0)
        self.switch("month")
        self.assertEqual(self.js("()=>document.querySelectorAll('.day-row .day.we').length"), 0)

    def test_switching_unit_keeps_the_viewport_centred_on_the_same_date(self):
        """粗粒度下整条时间轴可能比视口还窄，此时无从「保持中心」——
        只能要求滚到最左；只有仍可滚动时才校验中心比例。"""
        self.switch("day")
        before = self.js("""() => {
          const sc=document.querySelector('.board-scroll');
          sc.scrollLeft = 600;
          return (sc.scrollLeft + sc.clientWidth/2)
                 / document.querySelector('.lane').getBoundingClientRect().width;
        }""")
        self.switch("week")
        after = self.js("""() => {
          const sc=document.querySelector('.board-scroll');
          return {ratio:(sc.scrollLeft + sc.clientWidth/2)
                    / document.querySelector('.lane').getBoundingClientRect().width,
                  scrollable: sc.scrollWidth - sc.clientWidth,
                  scrollLeft: sc.scrollLeft};
        }""")
        if after["scrollable"] > 20:
            self.assertAlmostEqual(after["ratio"], before, delta=0.08)
        else:
            self.assertEqual(after["scrollLeft"], 0, "时间轴已完整可见时应停在最左")

    def test_scroll_position_stays_within_range_for_every_transition(self):
        """任意粒度切换后滚动位置都必须落在合法区间内，不能越界或变负。"""
        for src in self.UNITS:
            for dst in self.UNITS:
                if src == dst:
                    continue
                with self.subTest(src=src, dst=dst):
                    self.switch(src)
                    self.js("""()=>{const sc=document.querySelector('.board-scroll');
                        sc.scrollLeft = Math.floor((sc.scrollWidth - sc.clientWidth) * 0.6);}""")
                    self.switch(dst)
                    r = self.js("""()=>{const sc=document.querySelector('.board-scroll');
                        return {left: sc.scrollLeft, max: sc.scrollWidth - sc.clientWidth};}""")
                    self.assertGreaterEqual(r["left"], 0)
                    self.assertLessEqual(r["left"], max(r["max"], 0) + 1)


class TestCriticalPath(UITestCase):
    def test_critical_tasks_are_marked_in_the_gantt(self):
        r = self.js("""()=>({chips:document.querySelectorAll('.crit-chip').length,
                              bars:document.querySelectorAll('.lane .bar.critical, .lane .milestone.critical').length,
                              fromData: DATA.tasks.filter(t=>t.critical).length})""")
        self.assertGreater(r["fromData"], 0)
        self.assertEqual(r["chips"], r["fromData"])
        self.assertEqual(r["bars"], r["fromData"])

    def test_summary_reports_the_critical_chain(self):
        text = self.js("()=>document.getElementById('ganttSub').textContent")
        self.assertIn("关键路径", text)
        self.assertIn("关键链工期", text)

    def test_critical_slicer_filters(self):
        group = self.js("""()=>[...document.querySelectorAll('.slicer-group')]
            .findIndex(g=>g.querySelector('.slicer-label').textContent==='关键路径')""")
        self.assertGreaterEqual(group, 0, "应有「关键路径」切片器")
        self.page.click(f'.slicer-group:nth-child({group + 1}) .chip:nth-child(1)')
        self.page.wait_for_timeout(200)
        r = self.js("""()=>({shown:+document.querySelector('.stat .num').textContent,
                              expect: DATA.tasks.filter(t=>t.critical).length})""")
        self.assertEqual(r["shown"], r["expect"])

    def test_float_appears_in_the_detail_panel(self):
        self.page.click(".col-labels .row-label")
        self.page.wait_for_timeout(200)
        text = self.js("()=>document.querySelector('.detail').textContent")
        for label in ("最早开始", "最晚完成", "总浮动时差", "关键路径"):
            self.assertIn(label, text)

    def test_pivot_can_group_by_critical_path(self):
        self.page.select_option("#pivotRow", "critical")
        self.page.select_option("#pivotVal", "tf")
        self.page.wait_for_timeout(200)
        heads = self.js("()=>[...document.querySelectorAll('.pivot-table tbody th')].map(e=>e.textContent)")
        self.assertTrue({"关键", "非关键"} & set(heads))


class TestSearch(UITestCase):
    def search(self, text):
        self.page.fill("#search", text)
        self.page.wait_for_timeout(320)      # 防抖 120ms
        return self.js("()=>+document.querySelector('.stat .num').textContent")

    def test_matches_owner_name(self):
        self.assertEqual(self.search("李四"), 2)      # C 与 D

    def test_matches_task_id(self):
        self.assertEqual(self.search("H"), 1)

    def test_is_case_insensitive_and_matches_substrings(self):
        self.assertEqual(self.search("里程"), 1)

    def test_no_match_shows_empty_state(self):
        self.assertEqual(self.search("绝不存在的词"), 0)
        self.assertTrue(self.js("()=>!!document.querySelector('.gantt.is-empty')"))

    def test_clear_button_restores_everything(self):
        self.search("李四")
        self.page.click("#searchClear")
        self.page.wait_for_timeout(250)
        self.assertEqual(self.js("()=>+document.querySelector('.stat .num').textContent"), 8)

    def test_search_drives_every_component(self):
        self.search("李四")
        r = self.js("""()=>({card:+document.querySelector('.stat .num').textContent,
                              rows:document.querySelectorAll('.col-labels .row').length,
                              pivot:+[...document.querySelectorAll('.pivot-table tbody tr:last-child td')].pop().textContent})""")
        self.assertEqual(r["card"], r["rows"])
        self.assertEqual(r["card"], r["pivot"])


class TestUrlState(UITestCase):
    def test_hash_is_empty_in_the_default_view(self):
        self.assertEqual(self.js("()=>location.hash"), "")

    def test_filters_search_and_unit_round_trip(self):
        self.page.click(".slicer-group:nth-child(1) .chip:nth-child(1)")
        self.page.wait_for_timeout(150)
        self.page.click('#unitSwitch [data-unit="week"]')
        self.page.wait_for_timeout(200)
        self.page.fill("#search", "临期")
        self.page.wait_for_timeout(320)

        hash_ = self.js("()=>location.hash")
        before = self.js("()=>+document.querySelector('.stat .num').textContent")
        self.assertIn("unit=week", hash_)

        self.page.goto(_ctx["url"] + f"?today={TODAY}" + hash_)
        self.page.wait_for_timeout(400)
        state = self.js("""()=>({n:+document.querySelector('.stat .num').textContent,
            unit:document.querySelector('#unitSwitch .tool-btn.active').dataset.unit,
            q:document.getElementById('search').value,
            chips:[...document.querySelectorAll('.chip.active')].map(c=>c.textContent)})""")
        self.assertEqual(state["n"], before)
        self.assertEqual(state["unit"], "week")
        self.assertEqual(state["q"], "临期")
        self.assertEqual(state["chips"], ["一期"])

    def test_pivot_selection_round_trips(self):
        self.page.select_option("#pivotRow", "owner")
        self.page.select_option("#pivotVal", "avg")
        self.page.wait_for_timeout(200)
        hash_ = self.js("()=>location.hash")
        self.assertIn("pivot=owner", hash_)
        self.page.goto(_ctx["url"] + f"?today={TODAY}" + hash_)
        self.page.wait_for_timeout(350)
        self.assertEqual(self.js("()=>[document.getElementById('pivotRow').value,"
                                 "document.getElementById('pivotVal').value]"), ["owner", "avg"])

    def test_today_query_param_survives_hash_updates(self):
        self.page.click(".slicer-group:nth-child(1) .chip:nth-child(1)")
        self.page.wait_for_timeout(200)
        self.assertIn(f"today={TODAY}", self.js("()=>location.search"))

    def test_values_with_separators_are_encoded(self):
        self.page.fill("#search", "a,b&c=d")
        self.page.wait_for_timeout(320)
        self.page.goto(_ctx["url"] + f"?today={TODAY}" + self.js("()=>location.hash"))
        self.page.wait_for_timeout(350)
        self.assertEqual(self.js("()=>document.getElementById('search').value"), "a,b&c=d")
