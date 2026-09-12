/* 世界企業活動マップ — フロントエンド（D3） */
(async function () {
  // Artifact 版はデータを HTML に埋め込む（window.__DATA__ / __WORLD__）。ローカル版は fetch
  const [D, WORLD] = window.__DATA__ && window.__WORLD__ ? [window.__DATA__, window.__WORLD__]
    : await Promise.all([fetch("data.json").then(r => r.json()), fetch("world-110m.json").then(r => r.json())]);
  const el = id => document.getElementById(id);
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  // ── 分類 → 色（3スロット＋縮小は赤の中抜き＝形でも区別） ──────────────────────
  const GROUP = { ma: "capital", finance: "capital", capex: "real", alliance: "alliance", regulation: "alliance", contraction: "contraction" };
  const GROUP_VAR = { capital: "--c-capital", real: "--c-real", alliance: "--c-alliance", contraction: "--c-contraction" };
  const colorOf = t => css(GROUP_VAR[GROUP[t]]);
  const hollow = t => t === "contraction";
  const TYPES = Object.keys(D.action_labels);
  const REGIONS = {
    "北米": ["US", "CA"], "中南米": ["MX", "BR", "AR", "CL", "CO", "PE", "VE", "EC", "UY", "PA", "CR", "GT", "DO", "CU", "BO", "PY"],
    "欧州": ["GB", "DE", "FR", "IT", "ES", "NL", "BE", "CH", "AT", "SE", "NO", "DK", "FI", "IE", "PT", "PL", "CZ", "HU", "RO", "GR", "LU", "SK", "SI", "HR", "BG", "LT", "LV", "EE", "RS", "UA", "IS", "CY", "MT"],
    "ロシア・CIS": ["RU", "KZ", "UZ", "BY", "AZ", "GE", "AM", "TM", "KG", "TJ", "MD"],
    "中東": ["SA", "AE", "QA", "KW", "BH", "OM", "IL", "TR", "IR", "IQ", "JO", "LB", "EG"],
    "アフリカ": ["ZA", "NG", "KE", "ET", "GH", "MA", "DZ", "TN", "TZ", "UG", "AO", "CD", "CI", "SN", "MZ", "ZM", "ZW", "NA", "BW", "RW", "CM", "LY", "SD"],
    "日本": ["JP"], "中国・香港・台湾": ["CN", "HK", "TW", "MO"], "韓国": ["KR"],
    "インド・南アジア": ["IN", "PK", "BD", "LK", "NP"],
    "東南アジア": ["SG", "ID", "MY", "TH", "VN", "PH", "MM", "KH", "LA", "BN"],
    "オセアニア": ["AU", "NZ", "PG", "FJ"],
  };
  const regionOf = cc => Object.keys(REGIONS).find(k => REGIONS[k].includes(cc)) || "その他";
  const NAMES = new Intl.DisplayNames(["ja"], { type: "region" });
  const cname = cc => { try { return cc ? NAMES.of(cc) : "－"; } catch { return cc; } };
  const fmtUsd = v => v == null ? "不明" : v >= 1e9 ? `${(v / 1e9).toFixed(v >= 1e10 ? 0 : 1)}0億ドル`.replace("0億", "億").replace(/\.0億/, "億") : v >= 1e6 ? `${Math.round(v / 1e6)}00万ドル` : `${Math.round(v)}ドル`;
  const fmtUsdB = v => v == null ? "不明" : v >= 1e12 ? `${(v / 1e12).toFixed(2)}兆ドル` : v >= 1e9 ? `${(v / 1e9).toFixed(v >= 1e10 ? 0 : 1)}0億ドル`.replace(/\.?0*0億/, "0億").replace(/(\d)0億/, "$10億") : v >= 1e6 ? `${(v / 1e6).toFixed(0)}00万ドル` : "1億ドル未満";
  const usd = v => { // 見やすい日本語表記: 1.2兆ドル / 340億ドル / 8.5億ドル / 3000万ドル
    if (v == null) return "不明";
    if (v >= 1e12) return `${(v / 1e12).toFixed(2)}兆ドル`;
    if (v >= 1e8) return `${(v / 1e8).toFixed(v >= 1e10 ? 0 : 1)}億ドル`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(0)}00万ドル`.replace(/^(\d+)00万/, (m, a) => `${a}00万`);
    return `${Math.round(v / 1e4)}万ドル`;
  };

  // ── 状態 ────────────────────────────────────────────────────────────────────
  const days = D.days;
  let dateIdx = days.length - 1;
  const off = new Set();          // 非表示の活動種別
  let selected = null;            // 詳細表示中のイベント id
  let regionFilter = "", themeFilter = "", q = "", wmin = 1, noAI = false;   // noAI: AI・半導体テーマを除外

  // 日付ボタン
  const datesBox = el("dates");
  days.forEach((d, i) => {
    const b = document.createElement("button"); b.textContent = d.date.slice(5).replace("-", "/"); b.title = `${d.date}（${d.events.length}件）`;
    b.onclick = () => { dateIdx = i; selected = null; render(); }; datesBox.appendChild(b);
  });
  // 種別チップ
  const chips = el("type-chips");
  TYPES.forEach(t => {
    const b = document.createElement("button"); b.className = "chip" + (hollow(t) ? " hollow" : ""); b.style.setProperty("--sw", colorOf(t));
    b.innerHTML = `<i></i>${D.action_labels[t]}`; b.onclick = () => { off.has(t) ? off.delete(t) : off.add(t); render(); }; chips.appendChild(b);
  });
  D.themes.forEach(t => el("theme-sel").insertAdjacentHTML("beforeend", `<option>${t}</option>`));
  Object.keys(REGIONS).forEach(r => el("region-sel").insertAdjacentHTML("beforeend", `<option>${r}</option>`));
  el("theme-sel").onchange = e => { themeFilter = e.target.value; render(); };
  el("no-ai").onclick = () => { noAI = !noAI; el("no-ai").setAttribute("aria-pressed", String(noAI)); render(); };
  el("region-sel").onchange = e => { regionFilter = e.target.value; render(); };
  el("w-min").oninput = e => { wmin = +e.target.value; el("w-min-v").textContent = wmin.toFixed(1); render(); };
  el("arcs").onchange = el("cumul").onchange = () => render();
  el("q").oninput = e => { q = e.target.value.trim().toLowerCase(); render(); };
  el("method").textContent = `重みの式：${D.weight_formula}。主体規模＝年間売上（金融は運用資産）の桁、資本量＝動く金額（米ドル換算）の桁、将来影響＝不可逆性・波及範囲・時間軸による1〜5の評価。円の半径は重みの2.6乗に比例（重み5は重み3の約6倍の半径）、表示中の円の総面積が地図の12%になるよう全体を拡縮する。最終更新 ${new Date(D.built_at).toLocaleString("ja-JP")}。`;

  function visibleEvents() {
    const cumul = el("cumul").checked;
    const list = [];
    const from = cumul ? Math.max(0, dateIdx - 6) : dateIdx;
    for (let i = from; i <= dateIdx; i++) days[i].events.forEach(e => list.push({ ...e, age: dateIdx - i }));
    return list.filter(e => !off.has(e.action_type) && e.weight.total >= wmin
      && (!themeFilter || e.theme === themeFilter) && !(noAI && e.theme === "AI・半導体")
      && (!regionFilter || regionOf(e.location.country) === regionFilter || regionOf(e.origin.country) === regionFilter)
      && (!q || `${e.actor.name} ${e.actor.name_ja} ${e.title_ja} ${e.summary_ja} ${e.counterparty.name} ${e.location.city} ${e.actor.sector}`.toLowerCase().includes(q)));
  }

  // ── 地図 ─────────────────────────────────────────────────────────────────────
  const svg = d3.select("#map");
  const countries = topojson.feature(WORLD, WORLD.objects.countries);
  svg.append("defs").html(`<marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0,1 L9,5 L0,9 z" fill="context-stroke"/></marker>`);
  const gRoot = svg.append("g");
  const gLand = gRoot.append("g"), gArc = gRoot.append("g"), gLead = gRoot.append("g"),
        gBub = gRoot.append("g"), gPulse = gRoot.append("g"), gLabel = gRoot.append("g");
  const gSizeLeg = svg.append("g").attr("class", "size-leg");   // ズームの影響を受けない凡例
  // ズーム・パン（ホイール／ドラッグ／ダブルクリック）。円と線の太さは拡大しても画面上で肥大しないよう補正する
  let zt = d3.zoomIdentity;
  const zoom = d3.zoom().scaleExtent([1, 12]).on("zoom", ev => { zt = ev.transform; applyZoom(); });
  svg.call(zoom);
  function applyZoom() {
    const k = zt.k, s = Math.sqrt(k);   // 円は面積を √k だけ大きくして、拡大しても画面を埋め尽くさないようにする
    gRoot.attr("transform", zt);
    gBub.selectAll("circle").attr("r", e => e._r / s).attr("stroke-width", e => (hollow(e.action_type) ? 2 : 1) / s);
    gArc.selectAll("path").attr("stroke-width", e => e._sw / k);
    gLead.selectAll("line").attr("stroke-width", .7 / k);
    gLand.selectAll("path.c").attr("stroke-width", .5 / k);
    gPulse.selectAll("circle").attr("r", e => e._r / s + 4 / k).attr("stroke-width", 2 / k);
    gLabel.selectAll("text").attr("font-size", e => e._fs / k).attr("stroke-width", 3 / k)
      .attr("y", e => e._below ? e._p[1] + e._r / s + e._fs * 1.1 / k : e._p[1] - e._r / s - 4 / k);
  }
  el("zoom-reset").onclick = () => svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity);
  const tip = el("tip");
  const showTip = (html, ev) => { tip.innerHTML = html; tip.hidden = false; const x = Math.min(ev.clientX + 14, innerWidth - 340), y = Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 10); tip.style.left = x + "px"; tip.style.top = y + "px"; };
  const hideTip = () => { tip.hidden = true; };
  const tipHtml = e => `<b>${e.title_ja}</b><br>${e.actor.name_ja || e.actor.name} <span class="muted">(${cname(e.actor.country)}・${e.actor.sector})</span><br>
    <span class="muted">${D.action_labels[e.action_type]}・${e.theme}・${e.location.city}${e.cross_border ? ` ← ${e.origin.city}` : ""}</span><br>
    資本量 <b>${e.amount_text || usd(e.amount_usd)}</b>　重み <b>${e.weight.total.toFixed(2)}</b> <span class="muted">(規模${e.weight.scale} 資本${e.weight.capital.toFixed(1)} 影響${e.weight.impact})</span>`;

  function drawMap(list) {
    const W = svg.node().clientWidth;
    // 地図の高さは投影の縦横比（約1.95:1）に合わせる。上下に円のための余白を残す
    const H = Math.max(320, Math.min(720, Math.round(W / 1.95) + 64));
    svg.style("height", H + "px");
    const proj = d3.geoNaturalEarth1().fitExtent([[4, 30], [W - 4, H - 30]], { type: "Sphere" });
    const path = d3.geoPath(proj);
    gLand.selectAll("path.sphere").data([{ type: "Sphere" }]).join("path").attr("class", "sphere").attr("d", path).attr("fill", css("--sea")).attr("stroke", css("--line"));
    gLand.selectAll("path.c").data(countries.features).join("path").attr("class", "c").attr("d", path).attr("fill", css("--land")).attr("stroke", css("--border")).attr("stroke-width", .5);

    // 円の大きさ：重み1.0〜5.0を指数1.9で引き伸ばし、重要なものだけが際立つようにする
    //（面積比例だと重み5と重み3の差が2.8倍にしかならず、地図上で「今日の重心」が見えない）
    const EXP = 2.6, UMIN = 0.055;   // 単位半径：重み1で0.055、重み5で1.0
    const unit = w => UMIN + (1 - UMIN) * Math.pow(Math.max(0, Math.min(1, (w - 1) / 4)), EXP);
    // 円の総面積が地図の 12% になるよう最大半径を決める。件数を絞れば残った円が自動的に大きくなる
    const sumU = d3.sum(list, e => Math.pow(unit(e.weight.total), 2)) || 1;
    const RMAX = Math.max(14, Math.min(W / 12, Math.sqrt(0.12 * W * H / (Math.PI * sumU))));
    const rad = w => RMAX * unit(w);

    // 重なりの解消：本来の投影位置に引き戻す力と、円どうしが重ならない力を釣り合わせる
    const nodes = list.map(e => {
      const p = proj([e.location.lon, e.location.lat]);
      return p ? { e, r: rad(e.weight.total), ax: p[0], ay: p[1], x: p[0], y: p[1] } : null;
    }).filter(Boolean);
    // 完全には引き離さない（重なりを許す）。地理的な位置を大きく歪めないため
    d3.forceSimulation(nodes).stop()
      .force("x", d3.forceX(d => d.ax).strength(0.5))
      .force("y", d3.forceY(d => d.ay).strength(0.5))
      .force("collide", d3.forceCollide(d => d.r * 0.62 + 0.6).strength(0.6).iterations(2))
      .tick(160);
    const pos = new Map(nodes.map(d => [d.e.id, [d.x, d.y]]));
    const anchor = new Map(nodes.map(d => [d.e.id, [d.ax, d.ay]]));
    const alpha = e => 0.85 - 0.1 * e.age;

    // 国境を越える矢印（本社 → 活動地）
    const arcs = el("arcs").checked ? list.filter(e => e.cross_border && pos.has(e.id)) : [];
    arcs.forEach(e => { e._sw = 0.4 + e.weight.total / 6; });
    gArc.selectAll("path").data(arcs, e => e.id).join("path")
      .attr("d", e => { const a = proj([e.origin.lon, e.origin.lat]), b = pos.get(e.id); if (!a) return ""; const dx = b[0] - a[0], dy = b[1] - a[1], dr = Math.hypot(dx, dy) * 1.3; return `M${a[0]},${a[1]}A${dr},${dr} 0 0,1 ${b[0]},${b[1]}`; })
      .attr("fill", "none").attr("stroke", e => colorOf(e.action_type)).attr("stroke-opacity", e => (e.id === selected ? 0.9 : 0.28) - 0.03 * e.age)
      .attr("marker-end", "url(#arr)").attr("stroke-dasharray", e => hollow(e.action_type) ? "4 3" : null);

    const bub = list.filter(e => pos.has(e.id));
    bub.forEach(e => { e._r = rad(e.weight.total); e._p = pos.get(e.id); e._a = anchor.get(e.id); });

    // 重なり解消でずれた円は、本来の位置へ細い引き出し線を引く
    const moved = bub.filter(e => Math.hypot(e._p[0] - e._a[0], e._p[1] - e._a[1]) > e._r + 3);
    gLead.selectAll("line").data(moved, e => e.id).join("line")
      .attr("x1", e => e._a[0]).attr("y1", e => e._a[1]).attr("x2", e => e._p[0]).attr("y2", e => e._p[1])
      .attr("stroke", css("--axis")).attr("stroke-opacity", .55);

    const sel = gBub.selectAll("circle").data(bub, e => e.id).join("circle")
      .attr("cx", e => e._p[0]).attr("cy", e => e._p[1])
      .attr("fill", e => hollow(e.action_type) ? "transparent" : colorOf(e.action_type))
      // 大きい円ほど薄く：後ろに隠れた小さい円が透けて見えるように
      .attr("fill-opacity", e => alpha(e) * (0.82 - 0.28 * (e._r / RMAX)))
      .attr("stroke", e => hollow(e.action_type) ? colorOf(e.action_type) : css("--sea"))
      .style("cursor", "pointer")
      .on("mousemove", (ev, e) => showTip(tipHtml(e), ev)).on("mouseleave", hideTip)
      .on("click", (ev, e) => { select(e.id); });
    sel.sort((a, b) => b.weight.total - a.weight.total);   // 大きい円を下に
    gPulse.selectAll("circle").data(bub.filter(e => e.id === selected), e => e.id).join("circle").attr("class", "pulse")
      .attr("cx", e => e._p[0]).attr("cy", e => e._p[1]);

    // とても重要なもの（重み上位）は地図上に名前を出す
    const labeled = [...bub].sort((a, b) => b.weight.total - a.weight.total)
      .filter(e => e.weight.total >= 4.0).slice(0, 6);
    // ラベルどうしが重なる場合は円の下に回し、それでも重なるなら出さない
    const placed = [];
    labeled.forEach(e => {
      e._fs = 10.5 + 2.5 * (e.weight.total - 4);
      e._txt = (e.actor.name_ja || e.actor.name).slice(0, 14);
      const w = e._txt.length * e._fs * 0.95, h = e._fs * 1.2;
      const tryBox = below => { const y = below ? e._p[1] + e._r + h : e._p[1] - e._r - 4; return { x0: e._p[0] - w / 2, x1: e._p[0] + w / 2, y0: y - h, y1: y, below }; };
      const hit = b => placed.some(p => b.x0 < p.x1 && b.x1 > p.x0 && b.y0 < p.y1 && b.y1 > p.y0);
      let box = tryBox(false); if (hit(box)) box = tryBox(true);
      e._below = hit(box) ? null : box.below; if (e._below !== null) placed.push(box);
    });
    gLabel.selectAll("text").data(labeled.filter(e => e._below !== null), e => e.id).join("text")
      .attr("x", e => e._p[0]).attr("text-anchor", "middle")
      .attr("fill", css("--ink")).attr("stroke", css("--sea")).attr("paint-order", "stroke")
      .attr("font-weight", 600).style("pointer-events", "none")
      .text(e => e._txt);

    // 大きさの凡例（地図の左下、ズームしても大きさが変わらない基準）
    const legW = [5, 3.5, 2], r5 = rad(5), baseY = H - 14, cx = 18 + r5;
    gSizeLeg.attr("transform", null).selectAll("g").data(legW).join(
      enter => { const g = enter.append("g"); g.append("circle"); g.append("line"); g.append("text"); return g; })
      .each(function (w) {
        const r = rad(w), g = d3.select(this);
        g.select("circle").attr("cx", cx).attr("cy", baseY - r).attr("r", r)
          .attr("fill", "none").attr("stroke", css("--axis")).attr("stroke-width", 1);
        g.select("line").attr("x1", cx).attr("y1", baseY - 2 * r).attr("x2", cx + r5 + 8).attr("y2", baseY - 2 * r)
          .attr("stroke", css("--axis")).attr("stroke-width", .7).attr("stroke-dasharray", "2 2");
        g.select("text").attr("x", cx + r5 + 11).attr("y", baseY - 2 * r + 3).attr("font-size", 10)
          .attr("fill", css("--muted")).text(w === 5 ? "重み 5.0" : w.toFixed(1));
      });

    el("legend").innerHTML = [["capital", "資本取引（M&A・出資・資金調達）", false], ["real", "実物投資（工場・拠点・インフラ）", false], ["alliance", "提携・契約・規制", false], ["contraction", "縮小・撤退・破綻", true]]
      .map(([g, l, h]) => `<span class="sw${h ? " hollow" : ""}" style="--sw:${css(GROUP_VAR[g])}"><i></i>${l}</span>`).join("")
      + `<span>円の大きさ＝重み（左下の目盛が基準／上位8件は名前を表示）</span>`
      + `<span>矢印＝本社所在地から活動地へ（国境を越えるもの）</span>`;
    applyZoom();
  }

  // ── ランキング ─────────────────────────────────────────────────────────────
  const bars = e => `<div class="bars"><div class="bar s" title="主体規模 ${e.weight.scale}"><span style="width:${e.weight.scale * 20}%"></span></div><div class="bar c" title="資本量 ${e.weight.capital.toFixed(1)}"><span style="width:${e.weight.capital * 20}%"></span></div><div class="bar f" title="将来影響 ${e.weight.impact}"><span style="width:${e.weight.impact * 20}%"></span></div></div>`;
  function drawRank(list) {
    const top = [...list].sort((a, b) => b.weight.total - a.weight.total).slice(0, 40);
    el("count").textContent = `${list.length} 件中 上位 ${top.length}${noAI ? "（AI・半導体を除く）" : ""}`;
    el("rank").innerHTML = top.map(e => `<li data-id="${e.id}" class="${e.id === selected ? "active" : ""}">
      <span class="dot${hollow(e.action_type) ? " hollow" : ""}" style="--sw:${colorOf(e.action_type)}"></span>
      <div><div class="t">${e.title_ja}</div><div class="m">${e.actor.name_ja || e.actor.name}・${cname(e.location.country)}${e.cross_border ? ` ← ${cname(e.origin.country)}` : ""}・${e.amount_text || usd(e.amount_usd)}${e.age ? `・${e.age}日前` : ""}</div></div>
      <div><div class="wtot">${e.weight.total.toFixed(2)}</div>${bars(e)}</div></li>`).join("");
    el("rank").querySelectorAll("li").forEach(li => li.onclick = () => select(li.dataset.id));
    const d = days[dateIdx];
    el("note").innerHTML = `<b>${d.date} の構造</b>${d.daily_note_ja || ""}`;
  }

  // ── 詳細 ───────────────────────────────────────────────────────────────────
  function select(id) {
    selected = selected === id ? null : id; render();
    const box = el("detail");
    const e = visibleEvents().find(x => x.id === selected);
    if (!e) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div>
      <button class="close" title="閉じる">×</button>
      <h3>${e.title_ja}</h3>
      <div class="meta"><b>${e.actor.name_ja || e.actor.name}</b> <span>${e.actor.name}</span>・${cname(e.actor.country)}・${e.actor.sector}${e.counterparty.name ? `　→　<b>${e.counterparty.name}</b>${e.counterparty.country ? `（${cname(e.counterparty.country)}）` : ""}` : ""}<br>
        ${D.action_labels[e.action_type]}・${e.theme}・${e.date}${e.is_followup ? "・続報" : ""}</div>
      <p>${e.summary_ja}</p>
      <div class="why"><b>将来影響 ${"★".repeat(e.impact.score)}${"☆".repeat(5 - e.impact.score)}</b>（${e.impact.scope}・${e.impact.horizon}）　${e.impact.rationale_ja}</div>
      <ul>${e.sources.map(s => `<li><a href="${s.link}" target="_blank" rel="noopener">${s.title}</a> <span style="color:var(--muted)">${s.source}</span></li>`).join("")}</ul>
    </div>
    <div><table class="wtable">
      <tr><td>主体規模</td><td>${e.actor.scale_note || ""}</td><td class="n">${e.weight.scale.toFixed(1)}</td></tr>
      <tr><td>資本量</td><td>${e.amount_text || ""}${e.amount_usd ? ` ≒ ${usd(e.amount_usd)}` : "（不明→2.0）"}</td><td class="n">${e.weight.capital.toFixed(1)}</td></tr>
      <tr><td>将来影響</td><td>${e.impact.scope}・${e.impact.horizon}</td><td class="n">${e.weight.impact.toFixed(1)}</td></tr>
      <tr><td>重み</td><td>0.3×規模 + 0.3×資本 + 0.4×影響</td><td class="n">${e.weight.total.toFixed(2)}</td></tr>
      <tr><td>活動地</td><td colspan="2">${e.location.city}（${cname(e.location.country)}）<span style="color:var(--muted)">・${{ site: "拠点の立地", target: "対象の所在地", hq: "主体の本社", market: "対象市場・当局" }[e.location.kind] || ""}</span></td></tr>
      <tr><td>資本の出所</td><td colspan="2">${e.origin.city}（${cname(e.origin.country)}）${e.cross_border ? "　国境を越える動き" : ""}</td></tr>
    </table></div>`;
    box.querySelector(".close").onclick = () => { selected = null; render(); box.hidden = true; };
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ── 集計 ───────────────────────────────────────────────────────────────────
  function hbars(id, rows, onClick, cap) {
    const mx = d3.max(rows, r => r.total) || 1;
    el(id).innerHTML = rows.map(r => `<div class="hbar" data-k="${r.key}"><span class="lab" title="${r.label}">${r.label}</span><span class="trk">${TYPES.filter(t => r.by[t]).map(t => `<span style="width:${r.by[t] / mx * 100}%;background:${colorOf(t)};${hollow(t) ? "opacity:.5" : ""}"></span>`).join("")}</span><span class="v">${r.n}件</span></div>`).join("")
      + `<p class="cap">${cap}</p>`;
    el(id).querySelectorAll(".hbar").forEach(h => h.onclick = () => onClick(h.dataset.k));
  }
  function drawAgg(list) {
    const roll = (keyFn, labelFn) => {
      const m = new Map();
      list.forEach(e => { const k = keyFn(e); if (!k) return; const r = m.get(k) || { key: k, label: labelFn(k), n: 0, total: 0, usd: 0, by: {} }; r.n++; r.total += e.weight.total; r.usd += e.amount_usd || 0; r.by[e.action_type] = (r.by[e.action_type] || 0) + e.weight.total; m.set(k, r); });
      return [...m.values()].sort((a, b) => b.total - a.total).slice(0, 12);
    };
    const setQ = v => { el("q").value = v; q = v.toLowerCase(); render(); };
    hbars("agg-loc", roll(e => e.location.country, cname), k => { el("region-sel").value = regionOf(k); regionFilter = regionOf(k); render(); }, "棒の長さ＝重みの合計、色＝活動種別の内訳。クリックでその地域に絞る。");
    hbars("agg-origin", roll(e => e.origin.country, cname), k => { el("region-sel").value = regionOf(k); regionFilter = regionOf(k); render(); }, "主体の本社所在国で集計。活動地との差が「資本の輸出入」を示す。");
    hbars("agg-theme", roll(e => e.theme, t => t), k => { el("theme-sel").value = k; themeFilter = k; render(); }, "クリックでそのテーマに絞る。");
  }

  function render() {
    datesBox.querySelectorAll("button").forEach((b, i) => b.classList.toggle("active", i === dateIdx));
    chips.querySelectorAll(".chip").forEach((c, i) => c.classList.toggle("off", off.has(TYPES[i])));
    const list = visibleEvents();
    drawMap(list); drawRank(list); drawAgg(list);
    const d = days[dateIdx];
    el("caption").textContent = `${d.date}（${el("cumul").checked ? "直近7日を重ねて表示、古いものほど薄く" : "直近24時間の見出しから抽出"}）：${d.headline_count} 本の見出し → ${d.events.length} 件の企業活動。表示 ${list.length} 件。円をクリックすると詳細と出典。`;
    if (selected && !list.some(e => e.id === selected)) { selected = null; el("detail").hidden = true; }
  }
  window.addEventListener("resize", render);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  // Artifact のテーマ切替（<html data-theme="…">）でも D3 が描いた色を引き直す
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  render();
})();
