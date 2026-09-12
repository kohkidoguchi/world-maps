/* 世界政治・地政学マップ — front-end
   data.json is produced by build.py. Times are UTC. */
(async function () {
  const [D, WORLD] = await Promise.all([
    fetch("data.json").then(r => r.json()),
    fetch("world-110m.json").then(r => r.json()),
  ]);

  // ------------------------------------------------------------------ helpers
  const el = id => document.getElementById(id);
  const tooltip = el("tooltip");
  const fmtN = (v, d = 0) => v == null || isNaN(v) ? "–" : v.toLocaleString("ja-JP", { maximumFractionDigits: d, minimumFractionDigits: d });
  const fmtPt = (v, d = 1) => v == null || isNaN(v) ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}pt`;
  const fmtUsd = v => v == null ? "–" : v >= 1e12 ? `$${fmtN(v / 1e12, 2)}兆` : v >= 1e9 ? `$${fmtN(v / 1e9, 0)}0億` : `$${fmtN(v / 1e6, 0)}00万`;
  const fmtBig = v => v == null ? "–" : v >= 1e8 ? `${fmtN(v / 1e8, 2)}億` : v >= 1e4 ? `${fmtN(v / 1e4, 0)}万` : fmtN(v);
  const ja = iso => (D.countries[iso] && D.countries[iso].ja) || iso || "–";
  const jaOr = (iso, fallback) => iso ? ja(iso) : (fallback || "–");
  const gTime = s => s && s.length >= 12 ? `${s.slice(4, 6)}/${s.slice(6, 8)} ${s.slice(8, 10)}:${s.slice(10, 12)}` : "";
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function showTip(html, ev) { tooltip.innerHTML = html; tooltip.hidden = false; moveTip(ev); }
  function moveTip(ev) { const x = Math.min(ev.clientX + 14, window.innerWidth - 360), y = Math.min(ev.clientY + 14, window.innerHeight - 140); tooltip.style.left = x + "px"; tooltip.style.top = y + "px"; }
  function hideTip() { tooltip.hidden = true; }
  const G = D.gdelt, ST = D.structure, WB = ST.wb.data, MK = (D.markets && D.markets.events) || [], SA = D.sanctions || {};
  const quadClass = q => `q${q}`;
  const QUAD_JA = { 1: "言語的協調", 2: "実質的協調", 3: "言語的対立", 4: "実質的対立" };
  const QUAD_COLOR = { 1: "#9ec5e8", 2: "#2b6cb0", 3: "#e9a06b", 4: "#c0392b" };
  let selectedIso = null;
  const listeners = [];
  const onSelect = fn => listeners.push(fn);
  function select(iso) { selectedIso = iso === selectedIso ? null : iso; listeners.forEach(f => f(selectedIso)); }

  // ------------------------------------------------------------------ clocks
  el("clock-events").querySelector("ul").innerHTML = D.clocks.events.map(([k, v]) => `<li><span>${esc(k)}</span><span>${esc(v) || "–"}</span></li>`).join("");
  el("clock-structure").querySelector("ul").innerHTML = D.clocks.structure.map(([k, v]) => `<li><span>${esc(k)}</span><span>${esc(v) || "–"}</span></li>`).join("");

  // ------------------------------------------------------------------ world map
  const byNum = {};
  for (const [iso, c] of Object.entries(D.countries)) if (c.num != null) byNum[String(c.num).padStart(3, "0")] = iso;
  const features = topojson.feature(WORLD, WORLD.objects.countries).features;
  const centroid = {};
  for (const f of features) { const iso = byNum[f.id]; if (iso) centroid[iso] = d3.geoCentroid(f); }
  for (const [iso, c] of Object.entries(D.countries)) if (c.lat != null) centroid[iso] = [c.lon, c.lat];
  const svg = d3.select("#map");
  const W = svg.node().clientWidth || 1000, H = 520;
  svg.attr("viewBox", `0 0 ${W} ${H}`);
  const proj = d3.geoNaturalEarth1().fitExtent([[8, 8], [W - 8, H - 8]], { type: "Sphere" });
  const path = d3.geoPath(proj);
  svg.append("path").attr("d", path({ type: "Sphere" })).attr("fill", "#eaf1f8").attr("stroke", "#d5dde6");
  const gLand = svg.append("g"), gBloc = svg.append("g"), gArc = svg.append("g"), gBub = svg.append("g"), gDot = svg.append("g");
  svg.append("defs").append("marker").attr("id", "arr").attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5).attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
    .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", "#4a5568");

  const METRICS = {
    NEWS: { name: "24時間のニュース重要度（上位記事の重みの合計 × 新規性）", kind: "seq", log: true, get: iso => G.country[iso]?.news ?? G.country[iso]?.score_adj, fmt: v => fmtN(v) },
    SCORE: { name: "24時間の事象スコア（注目度×強度×主体規模×新規性）", kind: "seq", log: true, get: iso => G.country[iso]?.score_adj, fmt: v => fmtN(v) },
    RATIO: { name: "注目度：直近24時間 ÷ 過去28日の中央値（倍）", kind: "div1", get: iso => D.baseline[iso]?.ratio, fmt: v => `${fmtN(v, 2)}倍` },
    CONF: { name: "対立の比率（対立記事 ÷ 全記事, 24時間）", kind: "seq", get: iso => G.country[iso]?.conf_share, fmt: v => `${fmtN(v * 100, 0)}%` },
    MAT: { name: "実質的対立の比率（暴力・強制の実行, 24時間）", kind: "seq", get: iso => G.country[iso]?.mat_share, fmt: v => `${fmtN(v * 100, 0)}%` },
    GOLD: { name: "平均トーン（Goldstein尺度 −10〜+10, 24時間）", kind: "div", get: iso => G.country[iso]?.gold, fmt: v => fmtN(v, 2) },
    MILEX_GDP: { name: "軍事費（対GDP比）", kind: "seq", get: iso => WB.milex_gdp?.[iso]?.[0], fmt: v => `${fmtN(v, 1)}%`, year: iso => WB.milex_gdp?.[iso]?.[1] },
    MILEX_USD: { name: "軍事費（米ドル）", kind: "seq", log: true, get: iso => WB.milex_usd?.[iso]?.[0], fmt: fmtUsd, year: iso => WB.milex_usd?.[iso]?.[1] },
    POWER: { name: "勢力指数（GDPと軍事費の対最大国比の平均）", kind: "seq", get: iso => ST.power?.[iso], fmt: v => fmtN(v, 2) },
    WGI_PV: { name: "政治的安定・非暴力（世銀WGI, −2.5〜+2.5）", kind: "div", get: iso => WB.wgi_pv?.[iso]?.[0], fmt: v => fmtN(v, 2), year: iso => WB.wgi_pv?.[iso]?.[1] },
    WGI_VA: { name: "発言力と説明責任（民主的統治, 世銀WGI）", kind: "div", get: iso => WB.wgi_va?.[iso]?.[0], fmt: v => fmtN(v, 2), year: iso => WB.wgi_va?.[iso]?.[1] },
    WGI_RL: { name: "法の支配（世銀WGI）", kind: "div", get: iso => WB.wgi_rl?.[iso]?.[0], fmt: v => fmtN(v, 2), year: iso => WB.wgi_rl?.[iso]?.[1] },
    SANC: { name: "米国OFAC制裁の指定件数（対象国別）", kind: "seq", log: true, get: iso => SA.by_iso?.[iso]?.ofac, fmt: v => `${fmtN(v)}件` },
  };
  const metricSel = el("map-metric");
  metricSel.innerHTML = Object.entries(METRICS).map(([k, m]) => `<option value="${k}">${m.name}</option>`).join("");
  const blocSel = el("map-bloc");
  blocSel.innerHTML += Object.entries(ST.blocs).map(([k, b]) => `<option value="${k}">${b.name}（${b.members.length}）</option>`).join("");

  function colorScale(key) {
    const m = METRICS[key];
    const vals = Object.keys(D.countries).map(iso => m.get(iso)).filter(v => v != null && !isNaN(v) && (!m.log || v > 0));
    if (!vals.length) return { scale: () => "#e5e9ee", legend: [] };
    if (m.kind === "seq") {  // quantile classes: readable spread even when a few countries dominate
      const K = 7, colors = d3.range(K).map(i => d3.interpolateYlOrRd(0.08 + 0.9 * i / (K - 1)));
      const s = d3.scaleQuantile(vals, colors);
      return { scale: v => (v == null || (m.log && v <= 0)) ? "#e5e9ee" : s(v), legend: [d3.min(vals), ...s.quantiles(), d3.max(vals)], seq: true, colors };
    }
    if (m.kind === "div1") { const s = d3.scaleDivergingLog(d3.interpolateRdBu).domain([4, 1, 0.25]); return { scale: v => v == null ? "#e5e9ee" : s(Math.max(0.25, Math.min(4, v))), legend: [0.25, 1, 4] }; }
    const ext = Math.max(Math.abs(d3.min(vals)), Math.abs(d3.max(vals)));
    const s = d3.scaleDiverging(key === "GOLD" ? d3.interpolateRdBu : d3.interpolateBrBG).domain([-ext, 0, ext]);
    return { scale: v => v == null ? "#e5e9ee" : s(v), legend: [-ext, 0, ext] };
  }
  const bubbleColor = d3.scaleLinear().domain([0, 0.5, 1]).range(["#2b6cb0", "#c7ccd3", "#c0392b"]);

  function drawArrows() {
    const mode = el("map-arrows").value;
    let pairs = (G.dyads || []).filter(p => centroid[p.a1] && centroid[p.a2]);
    if (mode === "conf") pairs = pairs.filter(p => p.conf > p.coop); else if (mode === "coop") pairs = pairs.filter(p => p.coop >= p.conf); else if (!mode) pairs = [];
    if (selectedIso) pairs = pairs.filter(p => p.a1 === selectedIso || p.a2 === selectedIso).slice(0, 30); else pairs = pairs.slice(0, 40);
    const w = d3.scaleSqrt([0, d3.max(pairs, p => p.score) || 1], [0.8, 8]);
    const arcPath = p => { const a = proj(centroid[p.a1]), b = proj(centroid[p.a2]); const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1; const k = 0.22 * L; return `M${a[0]},${a[1]} Q${mx - dy / L * k},${my + dx / L * k} ${b[0]},${b[1]}`; };
    gArc.selectAll("path").data(pairs, p => p.a1 + p.a2).join("path").attr("class", "arc").attr("d", arcPath)
      .attr("stroke", p => p.conf > p.coop ? "#c0392b" : "#2b6cb0").attr("stroke-opacity", selectedIso ? .8 : .5).attr("stroke-width", p => w(p.score)).attr("marker-end", "url(#arr)")
      .on("mousemove", (ev, p) => showTip(`<b>${ja(p.a1)} → ${ja(p.a2)}</b><br>対立 ${fmtN(p.conf)} ／ 協調 ${fmtN(p.coop)}<br><span class="muted">記事 ${fmtN(p.articles)} 件・事象 ${fmtN(p.events)}・平均トーン ${fmtN(p.gold, 1)}</span>`, ev)).on("mouseleave", hideTip);
  }
  const BUBBLE = {
    news: { ja: "ニュース重要度", get: c => c.news ?? c.score_adj },
    score_adj: { ja: "事象スコア", get: c => c.score_adj },
    articles: { ja: "記事数", get: c => c.articles },
    mat: { ja: "実質的対立スコア", get: c => c.score * c.mat_share },
  };
  const bubbleVal = c => BUBBLE[el("map-bubble-metric").value].get(c) || 0;
  function drawBubbles() {
    const on = el("map-bubbles").checked;
    const rows = on ? Object.entries(G.country).filter(([iso]) => centroid[iso]).map(([iso, c]) => ({ iso, ...c, v: bubbleVal(c) })).filter(d => d.v > 0).sort((a, b) => b.v - a.v).slice(0, 120) : [];
    const r = d3.scaleSqrt([0, d3.max(rows, d => d.v) || 1], [0, 34]);
    gBub.selectAll("circle").data(rows, d => d.iso).join("circle").attr("cx", d => proj(centroid[d.iso])[0]).attr("cy", d => proj(centroid[d.iso])[1]).attr("r", d => r(d.v))
      .attr("fill", d => bubbleColor(d.conf_share)).attr("fill-opacity", .45).attr("stroke", d => bubbleColor(d.conf_share)).attr("stroke-width", 1).style("cursor", "pointer")
      .on("mousemove", (ev, d) => showTip(countryTip(d.iso) + `<br><span class="muted">クリックで主要ニュースを表示</span>`, ev)).on("mouseleave", hideTip)
      .on("click", (ev, d) => { ev.stopPropagation(); hideTip(); openNews(d.iso, ev); });
  }
  // ---- news popup (bubble click): the country's top stories of the last 24h, real headlines with links
  const pop = el("news-pop");
  let popIso = null, popN = 8;
  function newsOf(iso) {
    const seen = new Set();
    return G.events.filter(e => (e.loc === iso || e.a1 === iso || e.a2 === iso) && e.url && !seen.has(e.url) && seen.add(e.url));
  }
  function openNews(iso, ev) {
    if (popIso !== iso) popN = 8;
    popIso = iso;
    const c = G.country[iso] || {}, rows = newsOf(iso);
    const wrap = pop.parentElement.getBoundingClientRect();
    let x = ev.clientX - wrap.left + 12, y = ev.clientY - wrap.top - 20;
    if (x + 390 > wrap.width) x = Math.max(4, ev.clientX - wrap.left - 392);
    y = Math.max(4, Math.min(y, wrap.height - 300));
    pop.style.left = x + "px"; pop.style.top = y + "px";
    pop.innerHTML = `<h3><span>${ja(iso)} <span class="kv-note" style="font-weight:400;color:#5f6b7a;font-size:11.5px">${esc(D.countries[iso]?.en || "")}</span></span><button class="close" title="閉じる">×</button></h3>
      <div class="sum">直近24時間のニュース重要度 <b>${fmtN(c.news ?? c.score_adj)}</b>（記事群 ${fmtN(c.news_n || rows.length)} 件・記事 ${fmtN(c.articles)} 本・対立 ${fmtN((c.conf_share || 0) * 100, 0)}%${c.ratio ? `・注目度は28日中央値の ${fmtN(c.ratio, 1)} 倍` : ""}）</div>` +
      (rows.slice(0, popN).map(e => `<div class="n"><div class="w">${fmtN(e.score)}<small>${fmtN(e.sources)}媒体</small></div><div><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.domain)}</a><div class="m"><span class="tag-q${e.quad}">${esc(e.label)}</span> ${jaOr(e.a1, e.a1name)} → ${jaOr(e.a2, e.a2name)} ・ ${esc(e.domain)} ・ ${gTime(e.last)}Z</div></div></div>`).join("") || `<div class="n"><div></div><div class="m">この国に紐づく記事群はありません（事象は集計のみ）。</div></div>`) +
      (rows.length > popN ? `<div class="more"><button id="pop-more">さらに表示（残り ${rows.length - popN} 件）</button> <button id="pop-all">一覧で見る</button></div>` : rows.length ? `<div class="more"><button id="pop-all">一覧で見る</button></div>` : "");
    pop.hidden = false;
    pop.querySelector(".close").onclick = closeNews;
    const more = el("pop-more"); if (more) more.onclick = e => { popN += 10; openNews(iso, { clientX: wrap.left + x - 12, clientY: wrap.top + y + 20 }); };
    const all = el("pop-all"); if (all) all.onclick = () => { closeNews(); if (selectedIso !== iso) select(iso); el("ev-selected").checked = true; el("ev-intl").checked = false; drawEvents(); el("events").scrollIntoView({ behavior: "smooth" }); };
  }
  function closeNews() { pop.hidden = true; popIso = null; }
  svg.on("click", closeNews);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeNews(); });
  function drawDots() {
    const on = el("map-dots").checked;
    const rows = on ? G.events.filter(e => e.lat != null && e.lon != null).slice(0, 250) : [];
    const r = d3.scaleSqrt([0, d3.max(rows, d => d.articles) || 1], [1.5, 9]);
    gDot.selectAll("circle").data(rows, (d, i) => i).join("circle").attr("class", "dot").attr("cx", d => proj([d.lon, d.lat])[0]).attr("cy", d => proj([d.lon, d.lat])[1]).attr("r", d => r(d.articles))
      .attr("fill", d => QUAD_COLOR[d.quad]).attr("fill-opacity", .85)
      .on("mousemove", (ev, d) => showTip(eventTip(d), ev)).on("mouseleave", hideTip).on("click", (ev, d) => { if (d.url) window.open(d.url, "_blank"); });
  }
  function drawBloc() {
    const key = blocSel.value; const b = key ? ST.blocs[key] : null;
    const members = new Set(b ? b.members : []);
    gBloc.selectAll("path").data(features.filter(f => members.has(byNum[f.id])), f => f.id).join("path").attr("class", "bloc").attr("d", path);
    gBloc.selectAll("circle").data(b ? b.members.filter(m => centroid[m] && !features.some(f => byNum[f.id] === m)) : []).join("circle").attr("class", "bloc").attr("cx", m => proj(centroid[m])[0]).attr("cy", m => proj(centroid[m])[1]).attr("r", 5);
  }
  function countryTip(iso) {
    const c = G.country[iso], b = D.baseline[iso] || {};
    if (!c) return `<b>${ja(iso)}</b><br><span class="muted">直近24時間に記録された事象なし</span>`;
    return `<b>${ja(iso)}</b><br>ニュース重要度 <b>${fmtN(c.news ?? c.score_adj)}</b> <span class="muted">（記事群 ${fmtN(c.news_n || 0)} 件の重みの合計 × 新規性 ${c.boost}）</span><br>事象スコア ${fmtN(c.score_adj)}<br>記事 ${fmtN(c.articles)} 件（28日中央値の ${b.ratio != null ? fmtN(b.ratio, 1) + "倍" : "–"}）・事象 ${fmtN(c.events)}<br>対立の比率 ${fmtN(c.conf_share * 100, 0)}%（うち実質的対立 ${fmtN(c.mat_share * 100, 0)}%）・平均トーン ${fmtN(c.gold, 1)}`;
  }
  function eventTip(e) {
    return `<b>${esc(e.label)}</b> <span class="muted">${QUAD_JA[e.quad]}</span><br>${jaOr(e.a1, e.a1name)} → ${jaOr(e.a2, e.a2name)}<br>${esc(e.place)}<br>${esc(e.title || e.domain)}<br><span class="muted">記事 ${fmtN(e.articles)}・スコア ${fmtN(e.score)}・${gTime(e.last)} UTC</span>`;
  }
  function drawMap() {
    const key = metricSel.value, m = METRICS[key], cs = colorScale(key);
    gLand.selectAll("path").data(features, f => f.id).join("path").attr("class", f => "country" + (byNum[f.id] === selectedIso ? " selected" : "")).attr("d", path)
      .attr("fill", f => { const iso = byNum[f.id]; return iso ? cs.scale(m.get(iso)) : "#e5e9ee"; })
      .on("mousemove", (ev, f) => { const iso = byNum[f.id]; if (!iso) return; const v = m.get(iso); showTip(`${countryTip(iso)}<br><span class="muted">${esc(m.name)}：</span><b>${v == null ? "–" : m.fmt(v)}</b>${m.year ? ` <span class="muted">(${m.year(iso) || ""})</span>` : ""}`, ev); })
      .on("mouseleave", hideTip).on("click", (ev, f) => { const iso = byNum[f.id]; if (iso) select(iso); });
    gLand.selectAll("path").sort((a, b) => (byNum[a.id] === selectedIso) - (byNum[b.id] === selectedIso));
    const lg = el("map-legend");
    if (cs.legend.length) {
      let sw, lab;
      if (cs.seq) { sw = cs.colors.map(c => `<span style="background:${c}"></span>`).join(""); lab = `${m.fmt(cs.legend[0])} 〜 ${m.fmt(cs.legend[cs.legend.length - 1])}（7分位、境界 ${cs.legend.slice(1, -1).map(v => m.fmt(v)).join(" / ")}）`; }
      else { const n = 8, stops = d3.range(n).map(i => { const t = i / (n - 1); if (m.kind === "div1") return 0.25 * Math.pow(16, t); return cs.legend[0] + t * (cs.legend[2] - cs.legend[0]); }); sw = stops.map(v => `<span style="background:${cs.scale(v)}"></span>`).join(""); lab = `${m.fmt(stops[0])} 〜 ${m.fmt(stops[n - 1])}`; }
      lg.innerHTML = `<span>${esc(m.name)}</span><span class="swatches">${sw}</span><span>${lab}</span><span style="margin-left:12px">円の色：<i style="display:inline-block;width:10px;height:10px;background:#2b6cb0;border-radius:50%"></i> 協調優勢 〜 <i style="display:inline-block;width:10px;height:10px;background:#c0392b;border-radius:50%"></i> 対立優勢</span>`;
    } else lg.innerHTML = "";
    el("map-caption").textContent = `GDELT 直近24時間（${G.window_start.replace("T", " ")} 〜 ${G.latest.replace("T", " ")} UTC、${G.window_files}/96 スロット）：事象 ${fmtN(G.global.events)} 件・記事 ${fmtN(G.global.articles)} 件。世界の対立比率 ${fmtN((G.global.q[2] + G.global.q[3]) / G.global.articles * 100, 0)}%。構造指標は世銀の最新年。`;
    drawBloc(); drawArrows(); drawBubbles(); drawDots();
  }
  metricSel.onchange = drawMap; el("map-arrows").onchange = drawArrows; el("map-bubbles").onchange = drawBubbles; el("map-bubble-metric").onchange = drawBubbles; el("map-dots").onchange = drawDots; blocSel.onchange = drawBloc;
  onSelect(() => { drawMap(); });
  drawMap();

  // ------------------------------------------------------------------ country card
  function countryCard(iso) {
    const card = el("country-card");
    if (!iso) { card.innerHTML = `<p class="hint">地図上の国をクリックすると、その国の事象・相手国・予測市場・制裁・構造指標を表示します。</p>`; return; }
    const c = G.country[iso], b = D.baseline[iso] || {}, name = ja(iso);
    let h = `<h3>${name} <span class="kv-note">${esc(D.countries[iso]?.en || "")}</span></h3>`;
    if (c) {
      h += `<table><tr><td>ニュース重要度（24h）</td><td>${fmtN(c.news ?? c.score_adj)}</td></tr><tr><td>事象スコア（24h）</td><td>${fmtN(c.score_adj)}</td></tr><tr><td>記事数 ／ 28日中央値比</td><td>${fmtN(c.articles)} ／ ${b.ratio != null ? fmtN(b.ratio, 1) + "倍" : "–"}</td></tr><tr><td>事象数</td><td>${fmtN(c.events)}</td></tr><tr><td>平均トーン（Goldstein）</td><td>${fmtN(c.gold, 2)}</td></tr></table>`;
      const tot = c.q.reduce((a, x) => a + x, 0) || 1;
      h += `<div class="quad">${c.q.map((q, i) => `<span class="q${i + 1}" style="width:${q / tot * 100}%" title="${QUAD_JA[i + 1]} ${fmtN(q / tot * 100, 0)}%"></span>`).join("")}</div><div class="kv-note">${c.q.map((q, i) => `<span class="tag-q${i + 1}">${QUAD_JA[i + 1]} ${fmtN(q / tot * 100, 0)}%</span>`).join(" ・ ")}</div>`;
    } else h += `<p class="kv-note">直近24時間に記録された事象なし</p>`;
    const evs = newsOf(iso).slice(0, 8);
    if (evs.length) h += `<h4>主な事象（24h）</h4>` + evs.map(e => `<div class="ev"><span class="tag-q${e.quad}">${esc(e.label)}</span> ${jaOr(e.a1, e.a1name)} → ${jaOr(e.a2, e.a2name)}<br><span class="t">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.domain)}</a>` : esc(e.title)} ・ 記事${fmtN(e.articles)} ・ ${gTime(e.last)}Z</span></div>`).join("");
    const out = G.dyads.filter(p => p.a1 === iso).slice(0, 6), inn = G.dyads.filter(p => p.a2 === iso).slice(0, 6);
    if (out.length || inn.length) h += `<h4>相手国（24h）</h4><div class="kv-note">この国が主体 →</div>${out.map(p => `<span class="pill" style="border-color:${p.conf > p.coop ? "#c0392b" : "#2b6cb0"}">${ja(p.a2)} ${fmtN(p.score)}</span>`).join("")}<div class="kv-note">→ この国が対象</div>${inn.map(p => `<span class="pill" style="border-color:${p.conf > p.coop ? "#c0392b" : "#2b6cb0"}">${ja(p.a1)} ${fmtN(p.score)}</span>`).join("")}`;
    const mk = MK.filter(e => e.countries.includes(iso)).slice(0, 5);
    if (mk.length) h += `<h4>予測市場</h4>` + mk.map(e => `<div class="ev"><a href="https://polymarket.com/event/${esc(e.slug)}" target="_blank" rel="noopener">${esc(e.title)}</a><br><span class="t">${e.markets.slice(0, 2).map(m => `${e.multi ? esc(m.q) + " " : ""}<b>${fmtN(m.p * 100, 0)}%</b> <span class="${(m.d7 || 0) > 0 ? "up" : (m.d7 || 0) < 0 ? "down" : ""}">${fmtPt(m.d7)}/7d</span>`).join(" ・ ")}</span></div>`).join("");
    const s = SA.by_iso?.[iso];
    if (s) h += `<h4>制裁の対象（指定件数）</h4><table>${s.ofac ? `<tr><td>米国 OFAC SDN</td><td>${fmtN(s.ofac)}</td></tr>` : ""}${s.eu ? `<tr><td>EU</td><td>${fmtN(s.eu)}</td></tr>` : ""}${s.un ? `<tr><td>国連安保理</td><td>${fmtN(s.un)}</td></tr>` : ""}</table>`;
    const w = k => WB[k]?.[iso];
    h += `<h4>構造（世銀・年次）</h4><table>
      <tr><td>軍事費（対GDP比）</td><td>${w("milex_gdp") ? fmtN(w("milex_gdp")[0], 2) + "% <small>" + w("milex_gdp")[1] + "</small>" : "–"}</td></tr>
      <tr><td>軍事費</td><td>${w("milex_usd") ? fmtUsd(w("milex_usd")[0]) : "–"}</td></tr>
      <tr><td>兵員</td><td>${w("forces") ? fmtBig(w("forces")[0]) + "人" : "–"}</td></tr>
      <tr><td>名目GDP ／ 人口</td><td>${w("gdp_usd") ? fmtUsd(w("gdp_usd")[0]) : "–"} ／ ${w("pop") ? fmtBig(w("pop")[0]) + "人" : "–"}</td></tr>
      <tr><td>勢力指数（0〜1）</td><td>${fmtN(ST.power?.[iso], 3)}</td></tr>
      <tr><td>政治的安定（WGI）</td><td>${w("wgi_pv") ? fmtN(w("wgi_pv")[0], 2) : "–"}</td></tr>
      <tr><td>発言力と説明責任（WGI）</td><td>${w("wgi_va") ? fmtN(w("wgi_va")[0], 2) : "–"}</td></tr>
      <tr><td>法の支配 ／ 政府の有効性 ／ 腐敗抑制</td><td>${["wgi_rl", "wgi_ge", "wgi_cc"].map(k => w(k) ? fmtN(w(k)[0], 1) : "–").join(" ／ ")}</td></tr></table>`;
    const blocs = Object.values(ST.blocs).filter(bl => bl.members.includes(iso)).map(bl => bl.name);
    if (blocs.length) h += `<h4>所属ブロック</h4>${blocs.map(x => `<span class="pill">${esc(x)}</span>`).join("")}`;
    const today = new Date().toISOString().slice(0, 10);
    const els = (ST.elections || []).filter(e => e.iso === iso && e.date >= today).slice(0, 3);
    if (els.length) h += `<h4>選挙予定</h4>` + els.map(e => `<div class="ev${e.tentative ? " tentative" : ""}">${e.date} ${esc(e.what)}</div>`).join("");
    card.innerHTML = h;
  }
  onSelect(countryCard);

  // ------------------------------------------------------------------ events list
  function drawEvents() {
    const kind = el("ev-kind").value, n = +el("ev-n").value, onlySel = el("ev-selected").checked && selectedIso;
    let rows = G.events;
    if (el("ev-intl").checked) rows = rows.filter(e => e.intl);
    if (kind === "conf") rows = rows.filter(e => e.quad >= 3); else if (kind === "mat") rows = rows.filter(e => e.quad === 4); else if (kind === "coop") rows = rows.filter(e => e.quad <= 2);
    if (onlySel) rows = rows.filter(e => e.loc === selectedIso || e.a1 === selectedIso || e.a2 === selectedIso);
    el("events-list").innerHTML = rows.slice(0, n).map((e, i) => `<div class="ev-row ${quadClass(e.quad)}">
      <div class="rank">${i + 1}</div>
      <div class="score">${fmtN(e.score)}<small>${fmtN(e.sources)}媒体 × 強度${e.intensity} × 主体${e.actor_w}${e.intl ? " × 国際1.5" : ""}</small></div>
      <div class="where">${ja(e.loc)}<small>${esc(e.place)}</small></div>
      <div class="what"><span class="title" style="font-size:13px;margin-bottom:2px">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.url)}</a>` : esc(e.title)}</span><span class="label tag-q${e.quad}">${esc(e.label)}</span> <span class="actors">${jaOr(e.a1, e.a1name || "（主体不明）")} → ${jaOr(e.a2, e.a2name || "（対象不明）")} ・ ${esc(e.domain)}${e.real_title ? "" : " ・ <i>見出しはURLから推定</i>"}</span></div>
      <div class="meta">${QUAD_JA[e.quad]}・Goldstein ${fmtN(e.gold, 1)}<br>${e.events}行・${fmtN(e.articles)}記事<br>${gTime(e.first)}〜${gTime(e.last)} UTC</div></div>`).join("") || `<p class="caption">該当する事象はありません。</p>`;
  }
  ["ev-kind", "ev-n", "ev-selected", "ev-intl"].forEach(id => el(id).onchange = drawEvents);
  onSelect(drawEvents); drawEvents();

  // ------------------------------------------------------------------ dyad matrix
  const MAJORS = ["USA", "CHN", "RUS", "UKR", "IRN", "ISR", "PSE", "GBR", "FRA", "DEU", "JPN", "KOR", "PRK", "TWN", "IND", "PAK", "SAU", "TUR", "BRA", "ZAF"];
  function dyadTable(win) {
    const t = {};
    if (win === "24h") { for (const p of G.dyads) t[p.a1 + "|" + p.a2] = { coop: p.coop, conf: p.conf, articles: p.articles }; return t; }
    const days = win === "7d" ? 7 : 28, dates = G.dates, from = dates.length - days;
    for (const [k, ser] of Object.entries(G.dyad_daily)) {
      const sum = f => ser[f].slice(Math.max(0, from)).reduce((a, v) => a + (v || 0), 0);
      t[k] = { coop: sum("coop"), conf: sum("conf"), articles: sum("articles") };
    }
    return t;
  }
  function drawMatrix() {
    const win = el("mx-window").value, mode = el("mx-mode").value, t = dyadTable(win);
    const val = c => !c ? null : mode === "net" ? c.coop - c.conf : mode === "conf" ? c.conf : c.coop;
    const vals = MAJORS.flatMap(a => MAJORS.map(b => val(t[a + "|" + b]))).filter(v => v != null);
    const ext = d3.max(vals, v => Math.abs(v)) || 1;
    const col = mode === "net" ? d3.scaleDiverging(d3.interpolateRdBu).domain([-ext, 0, ext]) : d3.scaleSequential(mode === "conf" ? d3.interpolateReds : d3.interpolateBlues).domain([0, ext]);
    let h = `<thead><tr><th class="rh">主体 ＼ 対象</th>${MAJORS.map(b => `<th>${ja(b)}</th>`).join("")}</tr></thead><tbody>`;
    for (const a of MAJORS) {
      h += `<tr><th class="rh">${ja(a)}</th>`;
      for (const b of MAJORS) {
        if (a === b) { h += `<td class="diag"></td>`; continue; }
        const c = t[a + "|" + b], v = val(c);
        if (v == null) { h += `<td class="empty">·</td>`; continue; }
        const bg = col(v), dark = mode !== "net" ? v > ext * 0.55 : Math.abs(v) > ext * 0.55;
        h += `<td style="background:${bg};color:${dark ? "#fff" : "#1f2933"}" data-a="${a}" data-b="${b}">${fmtN(v)}</td>`;
      }
      h += `</tr>`;
    }
    const tbl = el("mx"); tbl.innerHTML = h + `</tbody>`;
    tbl.querySelectorAll("td[data-a]").forEach(td => {
      td.onmousemove = ev => { const c = t[td.dataset.a + "|" + td.dataset.b]; showTip(`<b>${ja(td.dataset.a)} → ${ja(td.dataset.b)}</b>（${win === "24h" ? "24時間" : win === "7d" ? "7日" : "28日"}）<br>協調 ${fmtN(c.coop)} ／ 対立 ${fmtN(c.conf)}<br><span class="muted">記事 ${fmtN(c.articles)} 件</span>`, ev); };
      td.onmouseleave = hideTip;
    });
  }
  el("mx-window").onchange = drawMatrix; el("mx-mode").onchange = drawMatrix; drawMatrix();

  // ------------------------------------------------------------------ markets
  function drawMarkets() {
    const kind = el("mk-kind").value, onlySel = el("mk-selected").checked && selectedIso;
    let rows = MK;
    if (kind !== "all") rows = rows.filter(e => e.kind === kind);
    if (onlySel) rows = rows.filter(e => e.countries.includes(selectedIso));
    const tb = el("mk-table").querySelector("tbody");
    tb.innerHTML = rows.slice(0, 80).flatMap(e => e.markets.slice(0, e.multi ? 3 : 1).map((m, i) => `<tr>
      <td>${i === 0 ? e.countries.map(ja).join("・") || "<span class='kv-note'>–</span>" : ""}</td>
      <td>${i === 0 ? `<a href="https://polymarket.com/event/${esc(e.slug)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : ""}${e.multi ? `<div class="kv-note" style="color:#5f6b7a;font-size:11.5px">${esc(m.q)}</div>` : ""}</td>
      <td class="num"><span class="prob"><i style="width:${m.p * 100}%"></i></span>${fmtN(m.p * 100, 1)}%</td>
      <td class="num ${(m.d1 || 0) > 0 ? "up" : (m.d1 || 0) < 0 ? "down" : ""}">${fmtPt(m.d1)}</td>
      <td class="num ${(m.d7 || 0) > 0 ? "up" : (m.d7 || 0) < 0 ? "down" : ""}">${fmtPt(m.d7)}</td>
      <td class="num">${i === 0 ? "$" + fmtN(e.vol24) : ""}</td><td class="num">${i === 0 ? e.end : ""}</td></tr>`)).join("") || `<tr><td colspan="7" class="caption">該当なし（Polymarket の取得に失敗した場合もここが空になります）</td></tr>`;
  }
  el("mk-kind").onchange = drawMarkets; el("mk-selected").onchange = drawMarkets; onSelect(drawMarkets); drawMarkets();

  // ------------------------------------------------------------------ sanctions
  (function sanctions() {
    const o = SA.ofac || {}, e = SA.eu || {}, u = SA.un || {};
    el("ofac-badge").textContent = o.entries ? `${fmtN(o.entries)} 件・取得 ${(o.fetched || "").slice(0, 16)}` : (o.error ? "取得失敗" : "");
    const progs = (o.by_program || []).slice(0, 18), mx = progs[0]?.[1] || 1;
    el("ofac-bars").innerHTML = progs.map(([p, n]) => `<div class="bar"><span>${esc(p)}</span><span class="track"><i style="width:${n / mx * 100}%"></i></span><span class="n">${fmtN(n)}</span></div>`).join("");
    el("ofac-actions").innerHTML = (o.actions || []).slice(0, 10).map(a => `<div class="item"><span class="d">${a.date}</span>${a.countries.map(i => `<span class="k">${ja(i)}</span>`).join("")}<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></div>`).join("");
    const diff = [];
    if (o.prev_vintage) {
      diff.push(`<div class="item"><b>SDNリストの差分</b> <span class="d">前回 ${o.prev_vintage} → 今回</span> 追加 ${fmtN((o.added || []).length)} ・ 削除 ${fmtN((o.removed || []).length)}</div>`);
      for (const a of (o.added || []).slice(0, 12)) diff.push(`<div class="item"><span class="k">追加</span>${a.iso ? `<span class="k">${ja(a.iso)}</span>` : ""}${esc(a.name)} <span class="names">${esc(a.programs)}</span></div>`);
      for (const a of (o.removed || []).slice(0, 6)) diff.push(`<div class="item"><span class="k">削除</span>${a.iso ? `<span class="k">${ja(a.iso)}</span>` : ""}${esc(a.name)} <span class="names">${esc(a.programs)}</span></div>`);
    } else diff.push(`<div class="item kv-note" style="color:#5f6b7a">SDNリストの日次差分は次回の更新から表示されます（初回取得 vintage を保存済み）。</div>`);
    el("ofac-diff").innerHTML = diff.join("");
    el("eu-badge").textContent = e.entities ? `${fmtN(e.entities)} 件・生成 ${e.generated}` : (e.error ? "取得失敗" : "");
    el("eu-recent").innerHTML = (e.recent || []).slice(0, 14).map(r => `<div class="item"><span class="d">${r.date}</span><span class="k">${r.iso ? ja(r.iso) : esc(r.programme)}</span>${fmtN(r.n)} 件 <span class="names">${esc(r.reg)} ・ ${esc(r.names.filter(Boolean).slice(0, 3).join("、"))}</span></div>`).join("") || `<div class="item kv-note">直近45日の追加なし</div>`;
    el("un-badge").textContent = u.entries ? `${fmtN(u.entries)} 件・生成 ${u.generated}` : (u.error ? "取得失敗" : "");
    el("un-recent").innerHTML = (u.recent || []).slice(0, 14).map(r => `<div class="item"><span class="d">${r.date}</span><span class="k">${esc(r.list)}</span>${esc(r.name)} <span class="names">${r.kind === "entity" ? "団体" : "個人"}</span></div>`).join("") || `<div class="item kv-note">直近60日の追加なし</div>`;
  })();

  // ------------------------------------------------------------------ time series
  function lineChart(svgId, legendId, dates, series, yLabel) {
    const s = d3.select(svgId); s.selectAll("*").remove();
    const w = s.node().clientWidth || 600, h = 240, m = { t: 12, r: 12, b: 26, l: 48 };
    s.attr("viewBox", `0 0 ${w} ${h}`);
    const x = d3.scalePoint(dates, [m.l, w - m.r]);
    const ymax = d3.max(series, sr => d3.max(sr.values, v => v || 0)) || 1;
    const y = d3.scaleLinear([0, ymax * 1.05], [h - m.b, m.t]);
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickValues(dates.filter((d, i) => i % Math.ceil(dates.length / 7) === 0)).tickFormat(d => d.slice(5)));
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(d => d >= 1000 ? `${d / 1000}k` : d));
    for (const sr of series) {
      const pts = dates.map((d, i) => [d, sr.values[i]]).filter(p => p[1] != null);
      s.append("path").attr("d", d3.line().x(p => x(p[0])).y(p => y(p[1]))(pts)).attr("fill", "none").attr("stroke", sr.color).attr("stroke-width", 2).attr("stroke-dasharray", sr.dash ? "5 4" : null);
      s.selectAll(null).data(pts).join("circle").attr("cx", p => x(p[0])).attr("cy", p => y(p[1])).attr("r", 2.4).attr("fill", sr.color)
        .on("mousemove", (ev, p) => showTip(`<b>${p[0]}</b><br>${esc(sr.name)}：${fmtN(p[1])}${yLabel}`, ev)).on("mouseleave", hideTip);
    }
    el(legendId).innerHTML = series.map(sr => `<span><i style="background:${sr.color}"></i>${esc(sr.name)}</span>`).join("");
  }
  const gd = G.global_daily || {};
  if (G.dates?.length && gd.q1) {
    lineChart("#ts-global", "ts-global-legend", G.dates, [
      { name: "対立（言語＋実質）記事数/日", color: "#c0392b", values: G.dates.map((d, i) => gd.q3[i] == null ? null : gd.q3[i] + gd.q4[i]) },
      { name: "うち実質的対立", color: "#8e1b12", dash: true, values: gd.q4 },
      { name: "協調（言語＋実質）記事数/日", color: "#2b6cb0", values: G.dates.map((d, i) => gd.q1[i] == null ? null : gd.q1[i] + gd.q2[i]) },
    ], " 件");
  }
  function drawCountryTs(iso) {
    el("ts-country-title").textContent = iso ? `${ja(iso)} ── 記事数と対立記事数（日次）` : "選択国（地図で国をクリック）";
    const ser = iso && G.country_daily[iso];
    if (!ser) { d3.select("#ts-country").selectAll("*").remove(); el("ts-country-legend").innerHTML = ""; return; }
    lineChart("#ts-country", "ts-country-legend", G.dates, [
      { name: "記事数/日", color: "#4a5568", values: ser.articles },
      { name: "対立記事数/日", color: "#c0392b", values: G.dates.map((d, i) => ser.q3[i] == null ? null : ser.q3[i] + ser.q4[i]) },
      { name: "うち実質的対立", color: "#8e1b12", dash: true, values: ser.q4 },
    ], " 件");
  }
  onSelect(drawCountryTs); drawCountryTs(null);

  // ------------------------------------------------------------------ calendar + UN press
  (function calendar() {
    const today = new Date().toISOString().slice(0, 10), past = new Date(Date.now() - 14 * 86400e3).toISOString().slice(0, 10), fut = new Date(Date.now() + 120 * 86400e3).toISOString().slice(0, 10);
    const rows = (ST.elections || []).filter(e => e.date >= past && e.date <= fut);
    el("elections").innerHTML = rows.map(e => `<div class="item${e.tentative ? " tentative" : ""}"><span class="d">${e.date}${e.date < today ? "（実施済）" : ""}</span><b>${e.iso ? ja(e.iso) : esc(e.country)}</b> ${esc(e.what)}</div>`).join("") || `<div class="item kv-note">該当なし</div>`;
    el("un-press").innerHTML = (ST.un_press || []).slice(0, 14).map(p => `<div class="item"><span class="d">${p.date}</span><span class="k">${esc(p.kind)}</span><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></div>`).join("");
  })();

  // ------------------------------------------------------------------ provenance
  el("sources-table").querySelector("tbody").innerHTML = D.sources.map(r => `<tr>${r.map((c, i) => `<td>${i === 0 ? "<b>" + esc(c) + "</b>" : esc(c)}</td>`).join("")}</tr>`).join("");
  el("blocs-table").innerHTML = Object.values(ST.blocs).map(b => `<div class="item"><b>${esc(b.name)}</b> <span class="names">${esc(b.note)}</span><br>${b.members.map(ja).join("・")}</div>`).join("");
  el("generated").textContent = `生成 ${D.generated}（ローカル時刻）。GDELT のスロット時刻は UTC。`;
})();
