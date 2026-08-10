(() => {
  "use strict";
  const DAY_MS = 86400000;
  const pad = n => String(n).padStart(2, "0");
  const toDay = s => { const [y, m, d] = s.split("-").map(Number); return Date.UTC(y, m - 1, d) / DAY_MS; };
  const dayToStr = n => { const d = new Date(n * DAY_MS); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; };
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const $ = id => document.getElementById(id);

  const override = new URLSearchParams(location.search).get("today");
  const TODAY = /^\d{4}-\d{2}-\d{2}$/.test(override || "") ? toDay(override)
    : (() => { const n = new Date(); return Date.UTC(n.getFullYear(), n.getMonth(), n.getDate()) / DAY_MS; })();
  const warnDays = DATA.warnDays;
  const timelineStart = toDay(DATA.timelineStart);
  const totalDays = toDay(DATA.timelineEnd) - timelineStart + 1;
  const timelineW = totalDays * 28;
  const dayNum = n => n - timelineStart;

  const STATE_META = {
    completed: { label: "已完成", color: "#16a34a" },
    inprogress: { label: "进行中", color: "#2563eb" },
    review: { label: "待评审", color: "#f59e0b" },
    notstarted: { label: "未开始", color: "#9ca3af" },
    overdue: { label: "拖期", color: "#dc2626" },
    warning: { label: "预警", color: "#d97706" },
    blocked: { label: "阻塞", color: "#7c3aed" },
  };
  const LEGEND_ORDER = ["completed", "inprogress", "review", "notstarted", "warning", "overdue", "blocked"];
  const PALETTE = ["#4f8ef7", "#22b8cf", "#51cf66", "#fcc419", "#ff922b", "#e64980", "#845ef7", "#f76707", "#2f9e44", "#1971c2", "#f03e3e", "#a61e4d"];

  function computeState(t) {
    if (t.progress >= 100) return "completed";
    if (toDay(t.end) < TODAY) return "overdue";
    if (toDay(t.end) - TODAY <= warnDays && t.progress < 80) return "warning";
    if (t.status === "阻塞") return "blocked";
    if (t.status === "待评审") return "review";
    if (t.progress > 0) return "inprogress";
    return "notstarted";
  }
  const tasks = DATA.tasks.map(t => Object.assign({}, t, { s: computeState(t) }));

  const SLICER_DEFS = [
    { key: "stage", label: "阶段", get: t => [t.stage] },
    { key: "owner", label: "负责人", get: t => t.owners },
    { key: "dept", label: "协作部门", get: t => t.dept ? [t.dept] : [] },
    { key: "status", label: "状态", get: t => [t.status] },
    { key: "risk", label: "风险等级", get: t => t.risk ? [t.risk] : [] },
    { key: "overdue", label: "拖期状态", get: t => [t.s === "overdue" ? "拖期" : t.s === "warning" ? "预警" : "正常"] },
    { key: "critical", label: "关键路径", get: t => [t.critical ? "关键" : "非关键"] },
  ];
  const filters = {};
  SLICER_DEFS.forEach(d => { filters[d.key] = new Set(); });

  const stageOrder = [];
  tasks.forEach(t => { if (!stageOrder.includes(t.stage)) stageOrder.push(t.stage); });

  let searchQuery = "";
  const haystack = t => [t.id, t.task, t.subtask, t.ownerText, t.dept,
                         t.deliverable, t.note].join(" ").toLowerCase();

  function filteredTasks() {
    const q = searchQuery.trim().toLowerCase();
    return tasks.filter(t => {
      for (const def of SLICER_DEFS) {
        const set = filters[def.key];
        if (set.size && !def.get(t).some(v => set.has(v))) return false;
      }
      return !q || haystack(t).includes(q);
    });
  }
  // ---------- 指标卡 ----------
  function renderStats(list) {
    const cnt = s => list.filter(t => t.s === s).length;
    const ms = list.filter(t => t.milestone);
    const msDone = ms.filter(t => t.s === "completed").length;
    const dow = (new Date(TODAY * DAY_MS).getUTCDay() + 6) % 7;
    const weekStart = TODAY - dow;
    const weekEnd = weekStart + 6;
    const weekDue = list.filter(t => toDay(t.end) >= weekStart && toDay(t.end) <= weekEnd && t.status !== "已完成").length;
    const cards = [
      { cls: "total", label: "总任务数", val: list.length },
      { cls: "completed", label: "已完成", val: cnt("completed") },
      { cls: "inprogress", label: "进行中", val: cnt("inprogress") },
      { cls: "review", label: "待评审", val: cnt("review") },
      { cls: "blocked", label: "阻塞", val: cnt("blocked") },
      { cls: "overdue", label: "拖期数", val: cnt("overdue") },
      { cls: "msrate", label: "里程碑完成率", val: ms.length ? Math.round(msDone / ms.length * 100) + "%" : "—" },
      { cls: "weekdue", label: "本周到期", val: weekDue },
    ];
    $("stats").innerHTML = cards.map(c => `<div class="stat ${c.cls}"><div class="num">${esc(c.val)}</div><div class="label">${esc(c.label)}</div></div>`).join("");
  }

  // ---------- 切片器 ----------
  function renderSlicers() {
    const container = $("slicers");
    container.innerHTML = "";
    SLICER_DEFS.forEach(def => {
      const values = [];
      tasks.forEach(t => def.get(t).forEach(v => { if (v && !values.includes(v)) values.push(v); }));
      const group = document.createElement("div");
      group.className = "slicer-group";
      group.innerHTML = `<div class="slicer-head"><div class="slicer-label">${esc(def.label)}</div><div class="slicer-tools"><button type="button" class="tool-btn" data-act="all">全选</button><button type="button" class="tool-btn" data-act="clear">清空</button></div></div><div class="slicer-chips"></div>`;
      const chipsBox = group.querySelector(".slicer-chips");
      values.forEach(v => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        chip.textContent = v;
        chip.dataset.value = v;
        chip.addEventListener("click", () => {
          const set = filters[def.key];
          if (set.has(v)) set.delete(v); else set.add(v);
          refresh(); syncHash();
        });
        chipsBox.appendChild(chip);
      });
      group.querySelector('[data-act="all"]').addEventListener("click", () => {
        filters[def.key] = new Set(values);
        refresh(); syncHash();
      });
      group.querySelector('[data-act="clear"]').addEventListener("click", () => {
        filters[def.key].clear();
        refresh(); syncHash();
      });
      container.appendChild(group);
    });
  }

  function renderSlicerChips() {
    document.querySelectorAll(".slicer-group").forEach(group => {
      const label = group.querySelector(".slicer-label").textContent;
      const def = SLICER_DEFS.find(d => d.label === label);
      group.querySelectorAll(".chip").forEach(chip => {
        chip.classList.toggle("active", filters[def.key].has(chip.dataset.value));
      });
    });
  }

  // ---------- 柱状图（内联 SVG） ----------
  function drawBarChart(svg, items, opts = {}) {
    // viewBox 尺寸贴近实际渲染宽度，避免 800 宽被压到 ~450 后字号缩成 6px 看不清
    const W = 460, H = 280, mL = 42, mR = 12, mT = 20, mB = 62;
    const plotW = W - mL - mR, plotH = H - mT - mB;
    const fmt = opts.format || (v => String(v));
    svg.innerHTML = "";
    if (!items.length) {
      svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="#9ca3af" font-size="14">暂无数据</text>`;
      return;
    }
    const maxV = opts.max != null ? opts.max : Math.max(1, ...items.map(i => i.value));
    const n = items.length;
    const slot = plotW / n;
    const barW = Math.min(slot * 0.62, 54);
    [0, .25, .5, .75, 1].forEach(frac => {
      const y = mT + plotH - plotH * frac;
      svg.innerHTML += `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#e5e7eb" stroke-width="1"/>`;
      svg.innerHTML += `<text x="${mL - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="#6b7280">${esc(fmt(maxV * frac))}</text>`;
    });
    items.forEach((it, i) => {
      const x = mL + i * slot + (slot - barW) / 2;
      const h = maxV > 0 ? it.value / maxV * plotH : 0;
      const y = mT + plotH - h;
      const color = it.color || PALETTE[i % PALETTE.length];
      svg.innerHTML += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(h, it.value > 0 ? 1 : 0)}" rx="3" fill="${color}"><title>${esc(it.label)}：${esc(fmt(it.value))}</title></rect>`;
      svg.innerHTML += `<text x="${x + barW / 2}" y="${y - 5}" text-anchor="middle" font-size="11" fill="#374151">${esc(fmt(it.value))}</text>`;
      const rot = n > 5 ? -30 : 0;
      const ty = mT + plotH + 16;
      svg.innerHTML += `<text x="${x + barW / 2}" y="${ty}" text-anchor="${rot ? "end" : "middle"}" font-size="11" fill="#6b7280" transform="${rot ? `rotate(${rot} ${x + barW / 2} ${ty})` : ""}">${esc(it.label)}</text>`;
    });
  }

  function renderCharts(list) {
    const charts = [
      {
        title: "各阶段任务数",
        items: list => stageOrder.filter(st => list.some(t => t.stage === st))
          .map(st => ({ label: st, value: list.filter(t => t.stage === st).length })),
        format: v => String(v),
      },
      {
        title: "各阶段平均进度",
        items: list => stageOrder.filter(st => list.some(t => t.stage === st)).map(st => {
          const arr = list.filter(t => t.stage === st);
          return { label: st, value: Math.round(arr.reduce((a, t) => a + t.progress, 0) / arr.length * 10) / 10 };
        }),
        format: v => v.toFixed(1),
        max: 100,
      },
      {
        title: "负责人待办负载",
        items: list => {
          const owners = [];
          list.forEach(t => t.owners.forEach(o => { if (!owners.includes(o)) owners.push(o); }));
          return owners.map(o => ({ label: o, value: list.filter(t => t.owners.includes(o) && t.status !== "已完成").length }));
        },
        format: v => String(v),
      },
    ];
    $("charts").innerHTML = "";
    charts.forEach(c => {
      const card = document.createElement("div");
      card.className = "chart-card";
      card.innerHTML = `<div class="chart-title">${esc(c.title)}</div><div class="chart-body"><svg viewBox="0 0 460 280" role="img" aria-label="${esc(c.title)}"></svg></div>`;
      $("charts").appendChild(card);
      drawBarChart(card.querySelector("svg"), c.items(list), { format: c.format, max: c.max });
    });
  }

  // ---------- 透视表 ----------
  const PIVOT_ROW_OPTS = [["stage", "阶段"], ["owner", "负责人"], ["dept", "协作部门"], ["priority", "优先级"], ["risk", "风险等级"], ["critical", "关键路径"]];
  const PIVOT_COL_OPTS = [["status", "状态"], ["stage", "阶段"], ["priority", "优先级"], ["risk", "风险等级"], ["milestone", "里程碑"], ["critical", "关键路径"]];
  const PIVOT_VAL_OPTS = [["count", "任务计数"], ["avg", "平均进度%"], ["days", "总工期(天)"], ["tf", "平均浮动(天)"]];

  function pivotVal(t, key) {
    if (key === "stage") return t.stage;
    if (key === "owner") return t.ownerText;
    if (key === "dept") return t.dept || "—";
    if (key === "priority") return t.priority;
    if (key === "risk") return t.risk;
    if (key === "status") return t.status;
    if (key === "milestone") return t.milestone ? "是" : "否";
    if (key === "critical") return t.critical ? "关键" : "非关键";
    return "";
  }

  // 透视表累加器：一个格子一份，边扫边加。
  // 旧实现是每个格子 list.filter 重扫一遍全表，行合计、列合计再各扫一遍——
  //「负责人(40) × 阶段(10)」在 2000 行下约 80 万次比较，且每次筛选都重来。
  const pivotAcc = () => ({ n: 0, prog: 0, dur: 0, tf: 0, tfN: 0 });
  const PIVOT_EMPTY = pivotAcc();
  const EMPTY_MAP = new Map();

  function pivotAdd(acc, t) {
    acc.n += 1;
    acc.prog += t.progress;
    acc.dur += t.duration;
    if (t.tf != null) { acc.tf += t.tf; acc.tfN += 1; }
  }

  function pivotValue(acc, valKey) {
    if (!acc) acc = PIVOT_EMPTY;
    if (valKey === "count") return acc.n;
    if (valKey === "avg") return acc.n ? Math.round(acc.prog / acc.n * 10) / 10 : null;
    if (valKey === "tf") return acc.tfN ? Math.round(acc.tf / acc.tfN * 10) / 10 : null;
    return acc.dur;
  }

  function renderPivot(list) {
    const rowKey = $("pivotRow").value;
    const colKey = $("pivotCol").value;
    const valKey = $("pivotVal").value;
    const fmt = (valKey === "avg" || valKey === "tf")
      ? v => v == null ? "—" : v.toFixed(1)
      : v => String(v);

    // 单遍聚合：格子、行合计、列合计、总计一次扫完。行列顺序仍按首次出现，
    // 与旧实现一致；去重从 Array.includes 换成 Set。
    // 格子用「行 Map 套列 Map」而不是拼接复合键——维度值里带什么字符都不会串味。
    const rows = [], cols = [];
    const cells = new Map(), rowTotal = new Map(), colTotal = new Map();
    const grand = pivotAcc();
    const bucket = (map, key, make) => {
      let v = map.get(key);
      if (v === undefined) map.set(key, v = make());
      return v;
    };
    const newMap = () => new Map();
    list.forEach(t => {
      const rv = pivotVal(t, rowKey), cv = pivotVal(t, colKey);
      if (!cells.has(rv)) rows.push(rv);
      if (!colTotal.has(cv)) cols.push(cv);
      pivotAdd(bucket(bucket(cells, rv, newMap), cv, pivotAcc), t);
      pivotAdd(bucket(rowTotal, rv, pivotAcc), t);
      pivotAdd(bucket(colTotal, cv, pivotAcc), t);
      pivotAdd(grand, t);
    });

    const wrap = $("pivotWrap");
    if (!list.length || !rows.length) {
      wrap.innerHTML = `<div class="pivot-empty">暂无数据</div>`;
      return;
    }
    let html = `<table class="pivot-table"><thead><tr><th>行 \\ 列</th>`;
    cols.forEach(c => { html += `<th>${esc(c)}</th>`; });
    html += `<th>合计</th></tr></thead><tbody>`;
    rows.forEach(r => {
      html += `<tr><th>${esc(r)}</th>`;
      cols.forEach(c => {
        html += `<td>${esc(fmt(pivotValue((cells.get(r) || EMPTY_MAP).get(c), valKey)))}</td>`;
      });
      html += `<td class="total">${esc(fmt(pivotValue(rowTotal.get(r), valKey)))}</td></tr>`;
    });
    html += `<tr class="total-row"><th>合计</th>`;
    cols.forEach(c => { html += `<td class="total">${esc(fmt(pivotValue(colTotal.get(c), valKey)))}</td>`; });
    html += `<td class="total">${esc(fmt(pivotValue(grand, valKey)))}</td></tr></tbody></table>`;
    wrap.innerHTML = html;
  }

  // ---------- 甘特图 ----------
  // 三档时间粒度共用同一坐标系：所有位置都由「距时间轴首日的天数 × 每日像素」算出，
  // 表头分段宽度也由各段实际天数乘同一系数得到，因此刻度与条形在任何粒度下都严格对齐。
  const UNITS = {
    day:   { label: "日", pxPerDay: 28 },
    week:  { label: "周", pxPerDay: 8 },
    month: { label: "月", pxPerDay: 2.6 },
  };
  let unit = "day";
  const px = d => d * UNITS[unit].pxPerDay;
  const dateAt = i => new Date((timelineStart + i) * DAY_MS);
  const offsetOf = iso => dayNum(toDay(iso));

  let headerCache = null;     // {unitKey, labels, time}：粒度不变就不重建表头
  let ganttCentered = false;

  /** 按 keyOf 把时间轴切成连续段，返回 [{start, days, label}]。 */
  function segments(keyOf, labelOf) {
    const out = [];
    for (let i = 0; i < totalDays; i++) {
      const d = dateAt(i);
      const key = keyOf(d);
      if (!out.length || out[out.length - 1].key !== key) {
        out.push({ key, start: i, days: 0, label: labelOf(d) });
      }
      out[out.length - 1].days += 1;
    }
    return out;
  }

  const isoWeekKey = d => {
    const t = new Date(d.getTime());
    t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));   // 回退到本周一
    return t.getTime();
  };

  /** 当前粒度下的两行表头分段：[上行, 下行]。 */
  function headerRows() {
    if (unit === "day") {
      return [segments(isoWeekKey, d => `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`),
              segments(d => d.getTime(), d => d.getUTCDate() === 1 ? `${d.getUTCMonth() + 1}月` : String(d.getUTCDate()))];
    }
    if (unit === "week") {
      return [segments(d => `${d.getUTCFullYear()}-${d.getUTCMonth()}`, d => `${d.getUTCFullYear()} 年 ${d.getUTCMonth() + 1} 月`),
              segments(isoWeekKey, d => `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`)];
    }
    return [segments(d => d.getUTCFullYear(), d => `${d.getUTCFullYear()} 年`),
            segments(d => `${d.getUTCFullYear()}-${d.getUTCMonth()}`, d => `${d.getUTCMonth() + 1}月`)];
  }

  function buildHeader() {
    if (headerCache && headerCache.unitKey === unit) return headerCache;

    const labels = document.createElement("div");
    labels.className = "thead";
    const labelHead = document.createElement("div");
    labelHead.className = "label-head";
    labelHead.textContent = "任务清单";
    labels.appendChild(labelHead);

    const time = document.createElement("div");
    time.className = "thead";
    const timeHead = document.createElement("div");
    timeHead.className = "time-head";

    const [upper, lower] = headerRows();
    [["week-row", upper], ["day-row", lower]].forEach(([cls, segs]) => {
      const row = document.createElement("div");
      row.className = cls;
      segs.forEach(seg => {
        const cell = document.createElement("div");
        cell.className = cls === "week-row" ? "week" : "day";
        const w = px(seg.days);
        cell.style.width = w + "px";
        cell.style.flex = `0 0 ${w}px`;
        if (unit === "day" && cls === "day-row") {
          const dow = dateAt(seg.start).getUTCDay();
          if (dow === 0 || dow === 6) cell.classList.add("we");
          if (dateAt(seg.start).getUTCDate() === 1) cell.classList.add("m1");
        }
        // 段太窄放不下标签就留白：按标签实际字数估宽，而不是拍一个固定阈值
        // （首尾残段最容易踩到，比如 3 天的残周放不下 "07-10"）
        if (w >= seg.label.length * 6.6 + 8) cell.textContent = seg.label;
        cell.title = seg.label;
        row.appendChild(cell);
      });
      timeHead.appendChild(row);
    });

    time.appendChild(timeHead);
    headerCache = { unitKey: unit, labels, time, timeHead };
    return headerCache;
  }

  /** 泳道背景：日视图用 CSS 渐变画每日网格 + 周末底色；周视图按周画；月视图交给分隔线。 */
  function laneStyle(gantt) {
    const per = UNITS[unit].pxPerDay;
    gantt.style.setProperty("--tl-w", px(totalDays) + "px");
    gantt.style.setProperty("--grid-w", (unit === "week" ? per * 7 : per) + "px");
    const dow0 = dateAt(0).getUTCDay();
    if (unit === "day") {
      gantt.style.setProperty("--wk-x", px((6 - dow0 + 7) % 7) + "px");
      gantt.style.setProperty("--wk-w", px(2) + "px");
      gantt.style.setProperty("--wk-period", px(7) + "px");
    } else {
      gantt.style.setProperty("--wk-x", "0px");
      gantt.style.setProperty("--wk-w", "0px");
      gantt.style.setProperty("--wk-period", px(7) + "px");
    }
    // 周/月视图的网格线相位要落在周一 / 月初
    gantt.style.setProperty("--grid-x", unit === "week" ? px((8 - dow0) % 7) + "px" : "0px");
  }

  function renderGantt(list) {
    const gantt = $("gantt");
    const scroller = gantt.parentElement;
    const keepX = scroller ? scroller.scrollLeft : 0;
    const keepY = scroller ? scroller.scrollTop : 0;

    const groups = stageOrder
      .map(st => ({ stage: st, tasks: list.filter(t => t.stage === st) }))
      .filter(g => g.tasks.length);

    if (!groups.length) {
      gantt.className = "gantt is-empty";
      gantt.removeAttribute("style");
      gantt.innerHTML = `<div class="empty">没有符合条件的任务</div>`;
      return;
    }
    gantt.className = "gantt";
    laneStyle(gantt);

    const head = buildHeader();
    const colLabels = document.createElement("div");
    colLabels.className = "col-labels";
    const colTime = document.createElement("div");
    colTime.className = "col-timeline";
    colLabels.appendChild(head.labels);      // 复用缓存的表头节点，不再逐次重建刻度
    colTime.appendChild(head.time);

    const todayX = px(dayNum(TODAY) + 0.5);
    const todayIn = todayX >= 0 && todayX <= px(totalDays);
    head.timeHead.querySelectorAll(".today-flag").forEach(n => n.remove());
    if (todayIn) {
      const flag = document.createElement("div");
      flag.className = "today-flag";
      flag.style.left = todayX + "px";
      flag.textContent = "今天 " + dayToStr(TODAY).slice(5);
      head.timeHead.appendChild(flag);
    }

    groups.forEach(g => {
      const hL = document.createElement("div");
      hL.className = "group-row";
      hL.textContent = `${g.stage}（${g.tasks.length}）`;
      const hT = document.createElement("div");
      hT.className = "group-row-time";
      colLabels.appendChild(hL);
      colTime.appendChild(hT);

      g.tasks.forEach(t => {
        const sel = t.id === selectedId ? " selected" : "";
        const rowL = document.createElement("div");
        rowL.className = "row" + sel;
        const label = document.createElement("div");
        label.className = "row-label";
        label.tabIndex = 0;
        label.setAttribute("role", "button");
        label.setAttribute("aria-label", `查看任务 ${t.id} ${t.name}`);
        const title = document.createElement("div");
        title.className = "row-title";
        title.innerHTML = `<span class="id-chip">${esc(t.id)}</span><span class="name">${esc(t.name)}</span>`
          + (t.milestone ? `<span class="milestone-chip">里程碑</span>` : "")
          + (t.critical ? `<span class="crit-chip" title="总浮动时差 0 天">关键</span>` : "");
        const sub = document.createElement("div");
        sub.className = "row-sub";
        sub.textContent = `负责人：${t.ownerText} · 进度 ${Math.round(t.progress)}% · ${STATE_META[t.s].label}`
          + (t.tf != null && !t.critical ? ` · 浮动 ${t.tf} 天` : "");
        label.appendChild(title);
        label.appendChild(sub);
        label.addEventListener("click", () => showDetail(t.id));
        label.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showDetail(t.id); } });
        rowL.appendChild(label);
        colLabels.appendChild(rowL);

        const rowT = document.createElement("div");
        rowT.className = "row" + sel;
        const cell = document.createElement("div");
        cell.className = "lane";
        const startX = px(offsetOf(t.start));
        const width = px(offsetOf(t.end) + 1) - startX;
        if (t.milestone) {
          if (width > px(1.5)) {
            const span = document.createElement("div");
            span.className = "ms-span";
            span.style.left = startX + "px";
            span.style.width = Math.max(width, 2) + "px";
            cell.appendChild(span);
          }
          const m = document.createElement("div");
          m.className = "milestone " + t.s + (t.critical ? " critical" : "");
          m.style.left = (px(offsetOf(t.end) + 0.5) - 7) + "px";
          m.title = `${t.id} ${t.name} · ${STATE_META[t.s].label}`;
          m.addEventListener("click", () => showDetail(t.id));
          cell.appendChild(m);
        } else {
          const bar = document.createElement("div");
          bar.className = "bar " + t.s + (t.critical ? " critical" : "");
          bar.style.left = startX + "px";
          bar.style.width = Math.max(width, 6) + "px";
          if (t.progress > 0 && t.progress < 100) {
            const fill = document.createElement("div");
            fill.className = "fill";
            fill.style.width = t.progress + "%";
            bar.appendChild(fill);
          }
          if (width >= 34) {
            const text = document.createElement("span");
            text.className = "text";
            text.textContent = `${Math.round(t.progress)}%`;
            bar.appendChild(text);
          }
          bar.title = `${t.id} ${t.name} · ${STATE_META[t.s].label} · ${Math.round(t.progress)}%`
            + (t.tf != null ? ` · 浮动 ${t.tf} 天${t.critical ? "（关键路径）" : ""}` : "");
          bar.addEventListener("click", () => showDetail(t.id));
          cell.appendChild(bar);
        }
        rowT.appendChild(cell);
        colTime.appendChild(rowT);
      });
    });

    // 月视图的分隔线不是等距的，改用少量绝对定位竖线，保证与表头月格严格对齐
    if (unit === "month") {
      segments(d => `${d.getUTCFullYear()}-${d.getUTCMonth()}`, () => "").forEach((seg, i) => {
        if (!i) return;
        const line = document.createElement("div");
        line.className = "unit-line";
        line.style.left = px(seg.start) + "px";
        colTime.appendChild(line);
      });
    }

    if (todayIn) {
      const tl = document.createElement("div");
      tl.className = "today-line";
      tl.style.left = (todayX - 1) + "px";
      colTime.appendChild(tl);
    }

    gantt.innerHTML = "";
    gantt.appendChild(colLabels);
    gantt.appendChild(colTime);

    if (scroller) {
      if (!ganttCentered && todayIn) {
        ganttCentered = true;
        scroller.scrollLeft = Math.max(0, todayX - scroller.clientWidth / 2);
      } else {
        scroller.scrollLeft = keepX;
      }
      scroller.scrollTop = keepY;
    }
  }

  function setUnit(next) {
    if (!UNITS[next] || next === unit) return;
    const scroller = $("gantt").parentElement;
    const centerDay = scroller
      ? (scroller.scrollLeft + scroller.clientWidth / 2) / UNITS[unit].pxPerDay : 0;
    unit = next;
    document.querySelectorAll("#unitSwitch .tool-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.unit === unit));
    refresh();
    if (scroller) {   // 切粒度后把原先屏幕中心的那一天重新放回中间
      scroller.scrollLeft = Math.max(0, px(centerDay) - scroller.clientWidth / 2);
    }
    syncHash();
  }

  function renderUnitSwitch() {
    const box = $("unitSwitch");
    box.innerHTML = Object.entries(UNITS).map(([k, v]) =>
      `<button type="button" class="tool-btn${k === unit ? " active" : ""}" data-unit="${k}">${v.label}</button>`).join("");
    box.querySelectorAll(".tool-btn").forEach(b =>
      b.addEventListener("click", () => setUnit(b.dataset.unit)));
  }

  // ---------- 数据提示 ----------
  function renderIssues() {
    const list = (DATA.issues || []).filter(i => i.level === "warning");
    const chip = $("issueChip"), panel = $("issuePanel");
    if (!list.length) return;
    chip.hidden = false;
    chip.textContent = `数据提示 ${list.length}`;
    panel.innerHTML = "<ol>" + list.map(i =>
      `<li>${i.where ? `<span class="where">${esc(i.where)}</span>` : ""}${esc(i.message)}`
      + (i.hint ? `<span class="hint">${esc(i.hint)}</span>` : "") + "</li>").join("") + "</ol>";
    chip.addEventListener("click", () => {
      const open = chip.getAttribute("aria-expanded") === "true";
      chip.setAttribute("aria-expanded", String(!open));
      panel.hidden = open;
    });
  }

  // ---------- URL 状态 ----------
  // 全部状态编码进 location.hash（file:// 下同样有效），可收藏、可分享某个筛选视图。
  let hashLoading = false;

  function syncHash() {
    if (hashLoading) return;
    const parts = [];
    SLICER_DEFS.forEach(def => {
      const set = filters[def.key];
      if (set.size) parts.push(`${def.key}=${[...set].map(encodeURIComponent).join(",")}`);
    });
    if (searchQuery.trim()) parts.push("q=" + encodeURIComponent(searchQuery.trim()));
    if (unit !== "day") parts.push("unit=" + unit);
    const pv = [$("pivotRow").value, $("pivotCol").value, $("pivotVal").value].join(".");
    if (pv !== "stage.status.count") parts.push("pivot=" + pv);
    if (selectedId) parts.push("sel=" + encodeURIComponent(selectedId));
    const hash = parts.length ? "#" + parts.join("&") : "";
    if (hash !== location.hash) history.replaceState(null, "", location.pathname + location.search + hash);
  }

  function applyHash() {
    const raw = location.hash.replace(/^#/, "");
    if (!raw) return false;
    hashLoading = true;
    let selected = null;
    raw.split("&").forEach(chunk => {
      const eq = chunk.indexOf("=");
      if (eq < 0) return;
      const key = chunk.slice(0, eq), val = decodeURIComponent(chunk.slice(eq + 1));
      if (filters[key] !== undefined) {
        filters[key] = new Set(chunk.slice(eq + 1).split(",").map(decodeURIComponent).filter(Boolean));
      } else if (key === "q") {
        searchQuery = val;
        $("search").value = val;
      } else if (key === "unit" && UNITS[val]) {
        unit = val;
      } else if (key === "pivot") {
        const [r, c, v] = val.split(".");
        if (r) $("pivotRow").value = r;
        if (c) $("pivotCol").value = c;
        if (v) $("pivotVal").value = v;
      } else if (key === "sel") {
        selected = val;
      }
    });
    hashLoading = false;
    if (selected && tasks.some(t => t.id === selected)) selectedId = selected;
    return true;
  }

  function renderSearch() {
    const box = $("search");
    let timer = null;
    box.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        searchQuery = box.value;
        refresh();
        syncHash();
      }, 120);
    });
    $("searchClear").addEventListener("click", () => {
      box.value = ""; searchQuery = ""; refresh(); syncHash(); box.focus();
    });
  }

  function renderLegend() {
    $("legend").innerHTML = LEGEND_ORDER.map(k =>
      `<span><span class="dot" style="background:${STATE_META[k].color}"></span>${STATE_META[k].label}</span>`
    ).join("");
  }

  let selectedId = null;
  function showDetail(id) {
    selectedId = selectedId === id ? null : id;
    const detail = $("detail");
    const t = tasks.find(x => x.id === id);
    if (!t || !selectedId) {
      detail.className = "detail";
      detail.innerHTML = "";
      renderGantt(filteredTasks());
      syncHash();
      return;
    }
    detail.className = "detail show";
    const items = [
      ["任务ID", t.id], ["阶段", t.stage], ["任务", t.task], ["子任务", t.subtask || "—"],
      ["负责人", t.ownerText], ["协作部门", t.dept || "—"], ["计划开始", t.start], ["计划结束", t.end],
      ["工期", t.duration + " 天"], ["进度", t.progress + "%"], ["前置任务", t.preds.length ? t.preds.join("、") : "—"],
      ["里程碑", t.milestone ? "是" : "否"], ["优先级", t.priority], ["状态", t.status],
      ["计算状态", STATE_META[t.s].label], ["风险等级", t.risk], ["交付物", t.deliverable || "—"], ["备注", t.note || "—"],
      ["最早开始", t.es || "—"], ["最早完成", t.ef || "—"], ["最晚开始", t.ls || "—"], ["最晚完成", t.lf || "—"],
      ["总浮动时差", t.tf == null ? "—" : `${t.tf} 天`], ["关键路径", t.critical ? "是" : "否"],
    ];
    detail.innerHTML = `<h3>${esc(t.id)} · ${esc(t.name)}</h3><div class="grid">${items.map(([k, v]) => `<div class="item"><span>${esc(k)}</span>${esc(v)}</div>`).join("")}</div>`;
    renderGantt(filteredTasks());
    syncHash();
  }

  function refresh() {
    const list = filteredTasks();
    renderStats(list);
    renderSlicerChips();
    renderCharts(list);
    renderPivot(list);
    renderGantt(list);
    renderLegend();
    $("ganttSub").textContent =
      `共 ${list.length} 条任务 · 里程碑 ${list.filter(t => t.milestone).length} 个`
      + ` · 拖期 ${list.filter(t => t.s === "overdue").length} 条`
      + ` · 关键路径 ${list.filter(t => t.critical).length} 条`
      + (DATA.criticalDuration ? `（关键链工期 ${DATA.criticalDuration} 天）` : "");
  }

  $("projectName").textContent = "项目：" + DATA.project;
  $("todayText").textContent = "今日：" + dayToStr(TODAY) + (override ? "（测试日期）" : "") + " · 打开即按系统日期自动计算拖期/预警";
  $("warnDays").textContent = DATA.warnDays;
  $("workdays").textContent = DATA.workdays || "周一至周五";
  $("buildDate").textContent = DATA.buildDate;

  renderIssues();
  renderSlicers();
  renderSearch();
  renderUnitSwitch();
  $("pivotRow").innerHTML = PIVOT_ROW_OPTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  $("pivotCol").innerHTML = PIVOT_COL_OPTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  $("pivotVal").innerHTML = PIVOT_VAL_OPTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  $("pivotRow").value = "stage";
  $("pivotCol").value = "status";
  $("pivotVal").value = "count";
  // 透视维度只影响透视表，不必连带重建甘特/图表/指标卡（旧实现走的是整表 refresh）
  ["pivotRow", "pivotCol", "pivotVal"].forEach(id =>
    $(id).addEventListener("change", () => { renderPivot(filteredTasks()); syncHash(); }));

  applyHash();
  document.querySelectorAll("#unitSwitch .tool-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.unit === unit));
  refresh();
})();
