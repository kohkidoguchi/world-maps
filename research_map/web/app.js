/* Research map front-end: treemap of fields / topics, papers as circles inside each rectangle. */
(async function () {
  const D = await (await fetch("data.json", { cache: "no-store" })).json();
  const T = D.topics, P = D.papers;
  const topicById = new Map(T.map(t => [t.id, t]));
  const DOMAIN_HEX = { "1": "#2a78d6", "2": "#eb6834", "3": "#1baf7a", "4": "#eda100" };
  const fmt = d3.format(","), f1 = d3.format(".1f"), f2 = d3.format("+.2f");
  const tip = document.getElementById("tip");
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------------------------------------------------------------- header
  document.getElementById("window-label").textContent =
    ` 対象期間 ${D.window.start} 〜 ${D.window.end}（${D.window.months.length}ヶ月、「直近」＝${D.window.recent_months.join("・")}）、更新 ${D.generated.replace("T", " ")}`;
  const kpi = (v, l) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  document.getElementById("kpis").innerHTML = kpi(fmt(D.totals.papers), "重要論文") + kpi(fmt(D.totals.top1), "分野・年の上位1%")
    + kpi(fmt(D.totals.topics), "トピック") + kpi(`+${fmt(D.totals.added_last_run)}`, `前回更新で追加（${D.totals.last_added || "-"}）`)
    + D.domains.map(d => kpi(fmt(d.n), d.name)).join("");

  // ---------------------------------------------------------------- scales
  const momExtent = 1.5; // log2 share ratio clamp
  const divNeg = d3.interpolateRgb("#e34948", "#f0efec"), divPos = d3.interpolateRgb("#f0efec", "#2a78d6");
  const colorMom = d3.scaleDiverging([-momExtent, 0, momExtent], t => t < .5 ? divNeg(t * 2) : divPos((t - .5) * 2));
  const colorScore = d3.scaleSequential([20, 90], d3.interpolateRgb("#cde2fb", "#0d366b"));
  const paperColor = (p, mode) => {
    const t = topicById.get(p.tp);
    if (mode === "mom") return colorMom(Math.max(-momExtent, Math.min(momExtent, t ? t.mom : 0)));
    if (mode === "score") return colorScore(p.sc);
    return DOMAIN_HEX[t ? t.domain : "1"];
  };
  // radius: relative position among the papers on screen, emphasised with a power curve so the
  // top of the distribution stands out (scores of displayed papers cluster in a narrow band)
  const R_MIN = 2, R_MAX = 17, R_POW = 2.4;
  let rLo = 0, rHi = 100;
  const rOf = sc => R_MIN + (R_MAX - R_MIN) * Math.pow(Math.max(0, Math.min(1, (sc - rLo) / Math.max(1e-6, rHi - rLo))), R_POW);
  function setRadiusDomain(papers) {
    const s = papers.map(p => p.sc).sort(d3.ascending);
    if (!s.length) { rLo = 0; rHi = 100; return; }
    rLo = d3.quantileSorted(s, 0.05); rHi = s[s.length - 1];
  }
  const LINKS = D.links || [];
  const LINK_STYLE = { ref: { stroke: "#898781", dash: null }, cite: { stroke: "#1c5cab", dash: null }, au: { stroke: "#eb6834", dash: "3,2" } };

  // papers grouped by field / topic, best first
  const papersByField = new Map(), papersByTopic = new Map();
  for (const p of Object.values(P)) {
    const t = topicById.get(p.tp); if (!t) continue;
    (papersByField.get(t.field) || papersByField.set(t.field, []).get(t.field)).push(p);
    (papersByTopic.get(t.id) || papersByTopic.set(t.id, []).get(t.id)).push(p);
  }
  for (const arr of [...papersByField.values(), ...papersByTopic.values()]) arr.sort((a, b) => b.sc - a.sc);

  // ---------------------------------------------------------------- map
  const svg = d3.select("#map");
  const wrap = document.querySelector(".map-wrap");
  const W = Math.max(600, Math.floor(wrap.getBoundingClientRect().width || 0)), H = 720;
  svg.attr("viewBox", [0, 0, W, H]).attr("width", "100%").attr("height", H);
  const g = svg.append("g");
  let level = { kind: "root" };   // or {kind:"field", id}
  const crumb = document.getElementById("crumb");

  function valueOf(t, mode) { return mode === "n" ? t.n : mode === "avg" ? t.avg : t.w; }

  function treeFor(minN, sizeMode) {
    if (level.kind === "root") {
      const dom = new Map();
      for (const t of T) {
        if (t.n < minN) continue;
        const d = dom.get(t.domain) || dom.set(t.domain, { name: t.domain_name, id: t.domain, kind: "domain", fields: new Map() }).get(t.domain);
        const f = d.fields.get(t.field) || d.fields.set(t.field, { name: t.field_name, id: t.field, kind: "field", value: 0, n: 0, topics: 0 }).get(t.field);
        f.value += valueOf(t, sizeMode); f.n += t.n; f.topics++;
      }
      return d3.hierarchy({ kind: "root", children: [...dom.values()].map(d => ({ ...d, children: [...d.fields.values()] })) })
        .sum(d => d.kind === "field" ? d.value : 0).sort((a, b) => b.value - a.value);
    }
    const subs = new Map();
    for (const t of T) {
      if (t.field !== level.id || t.n < minN) continue;
      const s = subs.get(t.sub) || subs.set(t.sub, { name: t.sub_name, id: t.sub, kind: "sub", children: [] }).get(t.sub);
      s.children.push({ name: t.name, id: t.id, kind: "topic", t, value: valueOf(t, sizeMode), n: t.n });
    }
    return d3.hierarchy({ kind: "root", children: [...subs.values()] }).sum(d => d.kind === "topic" ? d.value : 0).sort((a, b) => b.value - a.value);
  }

  // pack papers (as circles) into a rectangle; returns [{p, x, y, r}]
  function placePapers(papers, x0, y0, x1, y1) {
    const w = x1 - x0, h = y1 - y0;
    if (w < 8 || h < 8 || !papers) return [];
    const budget = w * h * 0.42;
    const chosen = [];
    let used = 0;
    for (const p of papers) {
      const r = rOf(p.sc);
      if (used + Math.PI * r * r > budget) { if (chosen.length) break; }
      chosen.push({ p, r }); used += Math.PI * r * r;
      if (chosen.length >= 400) break;
    }
    if (!chosen.length) return [];
    const circles = chosen.map(c => ({ r: c.r + 0.6, p: c.p, r0: c.r }));
    d3.packSiblings(circles);
    let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
    for (const c of circles) { minx = Math.min(minx, c.x - c.r); maxx = Math.max(maxx, c.x + c.r); miny = Math.min(miny, c.y - c.r); maxy = Math.max(maxy, c.y + c.r); }
    const bw = maxx - minx, bh = maxy - miny;
    const s = Math.min(1, (w - 2) / bw, (h - 2) / bh);          // uniform shrink so the cluster fits
    // then spread positions along the longer axis so the cluster fills the rectangle (positions only
    // grow, so circles never overlap); capped at 1.8x so a wide rect does not become a sparse line
    const sx = Math.min(s * 1.8, Math.max(s, (w - 2) / bw)), sy = Math.min(s * 1.8, Math.max(s, (h - 2) / bh));
    const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
    return circles.map(c => ({ p: c.p, x: x0 + w / 2 + (c.x - cx) * sx, y: y0 + h / 2 + (c.y - cy) * sy, r: c.r0 * s }));
  }

  function drawMap() {
    const minN = +document.getElementById("min-n").value || 1;
    const sizeMode = document.getElementById("size-mode").value;
    const colorMode = document.getElementById("color-mode").value;
    const rootLevel = level.kind === "root";
    const root = d3.treemap().size([W, H]).paddingOuter(3).paddingInner(2)
      .paddingTop(d => d.depth === 1 ? 18 : 0)(treeFor(minN, sizeMode));
    g.selectAll("*").remove();

    const groups = root.children || [];          // domains or subfields
    const leaves = root.leaves().filter(d => d.data.kind === "field" || d.data.kind === "topic");

    // group frames
    g.append("g").selectAll("rect").data(groups).join("rect")
      .attr("x", d => d.x0).attr("y", d => d.y0).attr("width", d => Math.max(0, d.x1 - d.x0)).attr("height", d => Math.max(0, d.y1 - d.y0))
      .attr("fill", d => rootLevel ? DOMAIN_HEX[d.data.id] + "14" : "rgba(11,11,11,0.03)")
      .attr("stroke", d => rootLevel ? DOMAIN_HEX[d.data.id] : "rgba(11,11,11,0.18)").attr("stroke-width", rootLevel ? 1.2 : 0.8).attr("rx", 3);
    g.append("g").selectAll("text").data(groups).join("text")
      .attr("x", d => d.x0 + 5).attr("y", d => d.y0 + 13).style("font-size", "11.5px").style("font-weight", 600)
      .style("fill", d => rootLevel ? DOMAIN_HEX[d.data.id] : "#52514e")
      .text(d => (d.x1 - d.x0) > 60 ? d.data.name : "");

    // leaf rects (fields / topics)
    const leafG = g.append("g");
    leafG.selectAll("rect").data(leaves).join("rect")
      .attr("x", d => d.x0).attr("y", d => d.y0).attr("width", d => Math.max(0, d.x1 - d.x0)).attr("height", d => Math.max(0, d.y1 - d.y0))
      .attr("fill", "#ffffff").attr("stroke", "rgba(11,11,11,0.16)").attr("stroke-width", 0.7).attr("rx", 2)
      .style("cursor", "pointer")
      .on("mousemove", (ev, d) => showLeafTip(ev, d))
      .on("mouseleave", () => tip.hidden = true)
      .on("click", (ev, d) => { ev.stopPropagation(); if (d.data.kind === "field") { level = { kind: "field", id: d.data.id, name: d.data.name }; drawMap(); } else showDetail(d.data.t); });

    // papers
    const placed = [];
    setRadiusDomain(leaves.flatMap(d => (d.data.kind === "field" ? papersByField.get(d.data.id) : papersByTopic.get(d.data.id)) || []));
    for (const d of leaves) {
      const labelH = (d.x1 - d.x0) > 40 && (d.y1 - d.y0) > 26 ? 13 : 0;
      const papers = d.data.kind === "field" ? papersByField.get(d.data.id) : papersByTopic.get(d.data.id);
      for (const c of placePapers(papers, d.x0 + 2, d.y0 + 2 + labelH, d.x1 - 2, d.y1 - 2)) { c.leaf = d; placed.push(c); }
      d.labelH = labelH;
    }
    // relations between displayed papers (drawn under the circles)
    const pos = new Map(placed.map(c => [c.p.id, c]));
    const showLinks = document.getElementById("show-links").checked;
    const minW = +document.getElementById("link-min").value || 0;
    const linkKinds = new Set([...document.querySelectorAll(".link-kind:checked")].map(e => e.value));
    const shown = showLinks ? LINKS.filter(l => l.w >= minW && linkKinds.has(l.k) && pos.has(l.a) && pos.has(l.b)) : [];
    const byPaper = new Map();
    const lineSel = g.append("g").attr("class", "links").selectAll("line").data(shown).join("line")
      .attr("x1", l => pos.get(l.a).x).attr("y1", l => pos.get(l.a).y).attr("x2", l => pos.get(l.b).x).attr("y2", l => pos.get(l.b).y)
      .attr("stroke", l => LINK_STYLE[l.k].stroke).attr("stroke-dasharray", l => LINK_STYLE[l.k].dash)
      .attr("stroke-width", l => 0.5 + 1.6 * l.w).attr("stroke-opacity", 0.35).attr("pointer-events", "none");
    lineSel.each(function (l) { for (const id of [l.a, l.b]) (byPaper.get(id) || byPaper.set(id, []).get(id)).push(this); });

    const circleSel = g.append("g").selectAll("circle").data(placed).join("circle")
      .attr("cx", c => c.x).attr("cy", c => c.y).attr("r", c => c.r)
      .attr("fill", c => paperColor(c.p, colorMode)).attr("stroke", "rgba(11,11,11,0.35)").attr("stroke-width", 0.6)
      .style("cursor", "pointer")
      .on("mousemove", (ev, c) => { showPaperTip(ev, c.p, (byPaper.get(c.p.id) || []).length); highlight(c.p.id); })
      .on("mouseleave", () => { tip.hidden = true; highlight(null); })
      .on("click", (ev, c) => { ev.stopPropagation(); showPaper(c.p); });
    function highlight(id) {
      if (!shown.length) return;
      if (!id) { lineSel.attr("stroke-opacity", 0.35).attr("stroke-width", l => 0.5 + 1.6 * l.w); circleSel.attr("opacity", 1); return; }
      const mine = new Set(byPaper.get(id) || []);
      const nb = new Set([id]);
      for (const l of shown) if (l.a === id || l.b === id) { nb.add(l.a); nb.add(l.b); }
      lineSel.attr("stroke-opacity", function () { return mine.has(this) ? 0.95 : 0.06; }).attr("stroke-width", function (l) { return mine.has(this) ? 1.5 + 2 * l.w : 0.5 + 1.6 * l.w; });
      circleSel.attr("opacity", c => nb.has(c.p.id) ? 1 : 0.35);
    }
    document.getElementById("link-count").textContent = showLinks ? `${fmt(shown.length)} 本の関係線` : "";

    // leaf labels (clipped by width)
    g.append("g").attr("pointer-events", "none").selectAll("text").data(leaves.filter(d => d.labelH)).join("text")
      .attr("x", d => d.x0 + 4).attr("y", d => d.y0 + 11).style("font-size", d => d.data.kind === "field" ? "11px" : "9.5px")
      .style("font-weight", d => d.data.kind === "field" ? 600 : 400).style("fill", "#0b0b0b")
      .text(d => fitLabel(d.data.name, (d.x1 - d.x0 - 6) / (d.data.kind === "field" ? 6.2 : 5.4)));

    svg.on("click", () => { if (!rootLevel) { level = { kind: "root" }; drawMap(); } });
    crumb.innerHTML = rootLevel ? `全体（26分野・${fmt(placed.length)} 論文を表示）`
      : `<a href="#" id="crumb-root">全体</a> › <b>${esc(level.name)}</b>（${fmt(leaves.length)} トピック・${fmt(placed.length)} 論文を表示。余白クリックで戻る）`;
    const a = document.getElementById("crumb-root"); if (a) a.onclick = ev => { ev.preventDefault(); level = { kind: "root" }; drawMap(); };
    drawLegend(colorMode);
  }
  function fitLabel(s, maxChars) { return maxChars < 3 ? "" : s.length <= maxChars ? s : s.slice(0, Math.max(1, Math.floor(maxChars) - 1)) + "…"; }

  function placeTip(ev) {
    tip.hidden = false;
    tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 340) + "px";
    tip.style.top = Math.min(ev.clientY + 14, window.innerHeight - 130) + "px";
  }
  function showLeafTip(ev, d) {
    if (d.data.kind === "topic") {
      const t = d.data.t;
      tip.innerHTML = `<div class="t">${esc(t.name)}</div><div class="m">${esc(t.field_name)} › ${esc(t.sub_name)}</div>
        <div>重要論文 ${fmt(t.n)}（直近2ヶ月 ${fmt(t.n_recent)}）　被引用計 ${fmt(t.cited)}　平均スコア ${f1(t.avg)}</div>
        <div>勢い ${f2(t.mom)}　国: ${t.cc.slice(0, 3).map(c => c[0] + " " + c[1]).join(" · ")}</div><div class="m">クリックで詳細</div>`;
    } else {
      tip.innerHTML = `<div class="t">${esc(d.data.name)}</div><div class="m">分野 · ${esc(d.parent.data.name)}</div>
        <div>重要論文 ${fmt(d.data.n)}　重みつき量 ${f1(d.data.value)}　トピック ${d.data.topics}</div><div class="m">クリックでトピック単位に展開</div>`;
    }
    placeTip(ev);
  }
  function showPaperTip(ev, p, nLinks) {
    const t = topicById.get(p.tp);
    tip.innerHTML = `<div class="t">${esc(p.t)}</div><div class="m">${esc(p.au || "?")}${p.na > 1 ? ` ほか${p.na - 1}名` : ""}${p.inst ? " · " + esc(p.inst) : ""}${p.cc ? " (" + p.cc + ")" : ""}</div>
      <div class="m">${esc(p.src || "")} · ${p.d}</div><div>スコア <b>${p.sc}</b>　被引用 ${fmt(p.c)}${p.fw != null ? `　FWCI ${p.fw}` : ""}${p.p1 ? "　上位1%" : ""}${nLinks ? `　関係 ${nLinks}` : ""}</div><div class="m">${t ? esc(t.name) : ""}</div>`;
    placeTip(ev);
  }

  function drawLegend(mode) {
    const L = document.getElementById("legend");
    const steps = [rLo, rLo + (rHi - rLo) * 0.5, rLo + (rHi - rLo) * 0.8, rHi].map(v => Math.round(v));
    const size = `<span>円の大きさ＝推定インパクト（表示中の論文内の相対）：</span>${steps.map(s => `<svg width="${rOf(s) * 2 + 2}" height="36" style="vertical-align:middle"><circle cx="${rOf(s) + 1}" cy="18" r="${rOf(s)}" fill="none" stroke="#52514e"/></svg><span>${s}</span>`).join("")}
      　<span>線：</span><span><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#898781" stroke-width="1.5"/></svg> 同じ文献を引用</span>
      <span><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#1c5cab" stroke-width="1.5"/></svg> 直接引用</span>
      <span><svg width="26" height="10"><line x1="0" y1="5" x2="26" y2="5" stroke="#eb6834" stroke-width="1.5" stroke-dasharray="3,2"/></svg> 著者が共通</span>`;
    if (mode === "domain") { L.innerHTML = D.domains.map(d => `<span><i class="sw" style="background:${DOMAIN_HEX[d.id]}"></i>${d.name}</span>`).join("") + "　" + size; return; }
    const stops = d3.range(0, 1.01, .1).map(t => mode === "mom" ? colorMom(-momExtent + t * 2 * momExtent) : colorScore(20 + t * 70)).join(",");
    L.innerHTML = (mode === "mom"
      ? `<span>トピックの勢い：直近2ヶ月の構成比が 1/3 以下</span><div class="ramp" style="background:linear-gradient(90deg,${stops})"></div><span>3倍以上</span><span class="muted">（灰＝それ以前と同じ）</span>`
      : `<span>スコア 20</span><div class="ramp" style="background:linear-gradient(90deg,${stops})"></div><span>90</span>`) + "　" + size;
  }

  // ---------------------------------------------------------------- detail
  const paperItem = (p, withTopic) => {
    const t = topicById.get(p.tp);
    return `<li><a href="${esc(p.u || "#")}" target="_blank" rel="noopener">${esc(p.t)}</a>
      <div class="meta">${esc(p.au || "?")}${p.na > 1 ? ` ほか${p.na - 1}名` : ""}${p.inst ? " · " + esc(p.inst) : ""}${p.cc ? " (" + p.cc + ")" : ""}${p.src ? " · " + esc(p.src) : ""} · ${p.d}
      · スコア <b>${p.sc}</b> · 被引用 ${fmt(p.c)}${p.fw != null ? ` · FWCI ${p.fw}` : ""}${p.p1 ? " · 上位1%" : ""}${withTopic && t ? ` · ${esc(t.name)}` : ""}</div></li>`;
  };
  function showDetail(t) {
    const el = document.getElementById("detail");
    const maxc = t.cc.length ? t.cc[0][1] : 1;
    const tops = (papersByTopic.get(t.id) || []).slice(0, 8);
    el.innerHTML = `<h3>${esc(t.name)}</h3><div class="path">${esc(t.domain_name)} › ${esc(t.field_name)} › ${esc(t.sub_name)}</div>
      <div>${t.kw.map(k => `<span class="kw">${esc(k)}</span>`).join("")}</div>
      <div class="stats">
        <div><span>重要論文</span><b>${fmt(t.n)}</b></div><div><span>うち直近2ヶ月</span><b>${fmt(t.n_recent)}</b></div>
        <div><span>被引用合計</span><b>${fmt(t.cited)}</b></div><div><span>月別</span><b style="font-weight:400">${t.months.join(" · ")}</b></div>
        <div><span>重みつき量</span><b>${f1(t.w)}</b></div><div><span>勢い（log2比）</span><b class="${t.mom >= 0 ? "pos" : "neg"}">${f2(t.mom)}</b></div>
        <div><span>平均スコア</span><b>${f1(t.avg)}</b></div><div><span>最高スコア</span><b>${f1(t.max)}</b></div>
      </div>
      <h3 style="font-size:12.5px">著者所属国（論文数）</h3>
      <div class="bars">${t.cc.map(([c, n]) => `<div class="bar"><span>${c}</span><i style="width:${100 * n / maxc}%"></i><span>${n}</span></div>`).join("")}</div>
      <h3 style="font-size:12.5px">推定インパクト上位</h3><ol>${tops.map(p => paperItem(p)).join("")}</ol>
      <a href="https://openalex.org/topics/${t.id}" target="_blank" rel="noopener" class="muted">OpenAlex でトピックを見る ↗</a>`;
  }
  function showPaper(p) {
    const t = topicById.get(p.tp);
    const el = document.getElementById("detail");
    el.innerHTML = `<div class="path">論文</div><h3><a href="${esc(p.u || "#")}" target="_blank" rel="noopener">${esc(p.t)}</a></h3>
      <div class="meta" style="margin:4px 0 8px">${esc(p.au || "?")}${p.na > 1 ? ` ほか${p.na - 1}名` : ""}${p.inst ? " · " + esc(p.inst) : ""}${p.cc ? " (" + p.cc + ")" : ""}<br>${esc(p.src || "")} · ${p.d} · ${p.ty}${p.oa ? " · OA" : ""}</div>
      <div class="stats"><div><span>スコア</span><b>${p.sc}</b></div><div><span>被引用（現時点）</span><b>${fmt(p.c)}</b></div>
        <div><span>FWCI（分野・年正規化）</span><b>${p.fw ?? "-"}</b></div><div><span>分野・年の上位1%</span><b>${p.p1 ? "はい" : "—"}</b></div>
        <div><span>マップ追加日</span><b>${p.add || "-"}</b></div><div><span>著者数</span><b>${p.na}</b></div></div>
      ${t ? `<h3 style="font-size:12.5px;margin-top:8px">トピック：<a href="#" id="paper-topic-link">${esc(t.name)}</a></h3><div class="meta">${esc(t.field_name)} › ${esc(t.sub_name)} · 重要論文 ${fmt(t.n)} 本 · 勢い ${f2(t.mom)}</div>` : ""}`;
    const a = document.getElementById("paper-topic-link"); if (a && t) a.onclick = ev => { ev.preventDefault(); showDetail(t); };
  }

  // ---------------------------------------------------------------- momentum table
  function drawMom() {
    const minN = +document.getElementById("mom-min").value || 5;
    const rows = T.filter(t => t.n >= minN).sort((a, b) => b.mom - a.mom);
    const up = rows.slice(0, 15), down = rows.slice(-8).reverse();
    const tr = t => `<tr class="clickable" data-id="${t.id}"><td>${esc(t.name)}<div class="cc">${esc(t.field_name)}</div></td>
      <td class="num">${fmt(t.n)}</td><td class="num">${fmt(t.n_recent)}</td><td class="num ${t.mom >= 0 ? "pos" : "neg"}">${f2(t.mom)}</td>
      <td class="cc">${t.cc.slice(0, 3).map(c => c[0] + " " + c[1]).join(" · ")}</td></tr>`;
    document.getElementById("mom-table").innerHTML = `<tr><th>トピック</th><th class="num">重要論文</th><th class="num">直近2ヶ月</th><th class="num">勢い</th><th>主な国</th></tr>`
      + up.map(tr).join("") + `<tr><td colspan="5" class="muted" style="text-align:center">…　構成比が下がったトピック　…</td></tr>` + down.map(tr).join("");
    document.querySelectorAll("#mom-table tr.clickable").forEach(r => r.onclick = () => { showDetail(topicById.get(r.dataset.id)); window.scrollTo({ top: 0, behavior: "smooth" }); });
  }

  // ---------------------------------------------------------------- field table
  function drawFields() {
    const maxn = d3.max(D.fields, f => f.n);
    const rows = D.fields.map(f => `<tr class="clickable" data-id="${f.id}"><td><i class="sw" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${DOMAIN_HEX[f.domain]};margin-right:6px"></i>${esc(f.name)}</td>
      <td class="num">${fmt(f.n)} <span class="mini" style="width:${60 * f.n / maxn}px"></span></td><td class="num">${f1(f.w)}</td><td class="num">${f1(100 * f.w / f.n)}</td>
      <td class="cc">${f.cc.slice(0, 4).map(c => `${c[0]} ${Math.round(100 * c[1] / f.n)}%`).join(" · ")}</td></tr>`);
    document.getElementById("field-table").innerHTML = `<tr><th>分野</th><th class="num">論文数</th><th class="num">重みつき量</th><th class="num">平均</th><th>著者所属国（論文比）</th></tr>` + rows.join("");
    document.querySelectorAll("#field-table tr.clickable").forEach(r => r.onclick = () => {
      const f = D.fields.find(x => x.id === r.dataset.id); level = { kind: "field", id: f.id, name: f.name }; drawMap(); window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // ---------------------------------------------------------------- papers
  const sel = document.getElementById("paper-field");
  D.fields.slice().sort((a, b) => a.name.localeCompare(b.name)).forEach(f => sel.insertAdjacentHTML("beforeend", `<option value="${f.id}">${esc(f.name)}</option>`));
  function drawPapers() {
    const fid = sel.value, q = document.getElementById("paper-q").value.trim().toLowerCase();
    let rows = D.top.map(id => P[id]).filter(p => p && (!fid || topicById.get(p.tp)?.field === fid)
      && (!q || [p.t, p.src, p.inst, p.au].join(" ").toLowerCase().includes(q)));
    document.getElementById("paper-count").textContent = `${rows.length} 件`;
    rows = rows.slice(0, 120);
    document.getElementById("paper-table").innerHTML = paperRows(rows);
    document.querySelectorAll("#paper-table .clickable").forEach(r => r.onclick = () => { showDetail(topicById.get(r.dataset.id)); window.scrollTo({ top: 0, behavior: "smooth" }); });
  }
  function paperRows(rows) {
    return `<tr><th>#</th><th>論文</th><th>トピック</th><th class="num">スコア</th><th class="num">被引用</th><th class="num">FWCI</th><th>上位1%</th><th>追加</th></tr>`
      + rows.map((p, i) => { const t = topicById.get(p.tp); return `<tr><td class="num">${i + 1}</td>
        <td class="title"><a href="${esc(p.u || "#")}" target="_blank" rel="noopener">${esc(p.t)}</a>${p.ty === "preprint" ? '<span class="pill">preprint</span>' : ""}${p.oa ? '<span class="pill">OA</span>' : ""}
          <div class="src">${esc(p.au || "?")}${p.na > 1 ? ` ほか${p.na - 1}名` : ""}${p.inst ? " · " + esc(p.inst) : ""}${p.cc ? " (" + p.cc + ")" : ""}${p.src ? " · " + esc(p.src) : ""} · ${p.d}</div></td>
        <td><span class="clickable" data-id="${p.tp}" style="cursor:pointer;color:var(--accent)">${t ? esc(t.name) : ""}</span><div class="cc">${t ? esc(t.field_name) : ""}</div></td>
        <td class="num"><b>${p.sc}</b></td><td class="num">${fmt(p.c)}</td><td class="num">${p.fw ?? "-"}</td><td>${p.p1 ? "●" : ""}</td><td class="cc">${p.add || ""}</td></tr>`; }).join("");
  }
  function drawNew() {
    const rows = (D.new || []).map(id => P[id]).filter(Boolean);
    document.getElementById("new-count").textContent = rows.length ? `${rows.length} 件（${D.totals.last_added}）` : "追加なし";
    document.getElementById("new-table").innerHTML = rows.length ? paperRows(rows.slice(0, 60)) : "";
    document.querySelectorAll("#new-table .clickable").forEach(r => r.onclick = () => { showDetail(topicById.get(r.dataset.id)); window.scrollTo({ top: 0, behavior: "smooth" }); });
  }

  // ---------------------------------------------------------------- footer
  document.getElementById("foot").innerHTML = `データ: OpenAlex（CC0）。対象タイプ ${D.method.types}。採用基準: ${D.method.admission}。スコア: ${D.method.score}。勢い: ${D.method.momentum}。線: ${D.method.links}。
    直近の月ほど被引用が蓄積していないため採用数が少なく見える（構成比ベースの勢いで補正）。上位掲載誌: ${D.top_sources.slice(0, 8).map(s => esc(s[0])).join("、")}。`;

  // ---------------------------------------------------------------- wire up
  ["size-mode", "color-mode", "min-n", "show-links", "link-min"].forEach(id => document.getElementById(id).addEventListener("change", drawMap));
  document.querySelectorAll(".link-kind").forEach(e => e.addEventListener("change", drawMap));
  document.getElementById("zoom-out").onclick = () => { level = { kind: "root" }; drawMap(); };
  document.getElementById("mom-min").addEventListener("change", drawMom);
  sel.addEventListener("change", drawPapers);
  document.getElementById("paper-q").addEventListener("input", drawPapers);
  drawMap(); drawMom(); drawFields(); drawPapers(); drawNew();
})();
