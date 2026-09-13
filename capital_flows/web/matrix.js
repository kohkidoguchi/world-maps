/* グローバル資本フローマップ
   三つの尺度を厳密に分ける：
     ① TX   実取引フロー   期間中に実際に取引された資金（資金循環統計の金融取引／国際収支のフロー）
     ② POS  保有残高       ある時点のストック
     ③ DPOS 残高変化       ストックの増減 ＝ 取引 ＋ 価格変動 ＋ 為替変動 ＋ その他
   どの尺度でも、図の中のすべての矢印は同じ期間・同じ時点に揃える。
   データ: matrix_data.json（build.py が生成） */
(async function () {
  const D = await (window.__MX__ ? Promise.resolve(window.__MX__) : fetch("matrix_data.json").then(r => r.json()));

  // ---------------------------------------------------------------- 語彙
  const REG = D.regions, REG_ORDER = Object.keys(REG);
  const SHORT = { JPN: "日本", USA: "米国", EA: "ユーロ圏", GBR: "英国", CHN: "中国", FC: "金融センター", ADV: "他先進国", EM: "新興国", OTHER: "その他海外" };
  const REG_COLOR = { JPN: "#c0504d", USA: "#34497a", EA: "#5b7db1", GBR: "#7b5ea7", CHN: "#b8860b", FC: "#7f8c8d", ADV: "#2a9d8f", EM: "#e08e45", OTHER: "#9aa5b1" };
  const SECT = { GOV: "政府", FIN: "金融", NFC: "企業", HH: "家計", ALL: "全主体" };
  const GSECT = ["GOV", "FIN", "NFC", "HH"];
  const SECT_ORDER = ["HH", "NFC", "GOV", "FIN", "ROW"];
  const SECT_JA = { HH: "家計", NFC: "企業", GOV: "政府", FIN: "金融機関", ROW: "海外" };
  const SECT_COLOR = { HH: "#2a9d8f", NFC: "#3f51b5", GOV: "#7b5ea7", FIN: "#e09f3e", ROW: "#5b87b8" };
  const INSTR_JA = { F2: "現金・預金", F3: "債券", F4: "貸出", F51: "株式・出資金", F52: "投資信託", F6: "保険・年金", F5: "株式・投信", F7: "デリバティブ", F8: "その他" };
  // PIP holder sector -> grid sector ; PIP issuer sector -> grid sector
  const H2G = { GOVCB: "GOV", BANK: "FIN", OFC: "FIN", NFP: "NFP_SPLIT" };
  const I2G = { GOV: "GOV", FIN: "FIN", NFP: "NFC", TOTAL: "SPLIT" };
  const DOM_MEMBERS = { JPN: ["JPN"], USA: ["USA"], EA: ["EA"], GBR: ["GBR"], ADV: ["CAN", "KOR"] };
  const REG_FX = { JPN: ["JPN"], EA: ["EA"], GBR: ["GBR"], USA: ["USA"], ADV: ["CAN", "KOR"] };

  const MEASURE = {
    TX: { ja: "実取引フロー", flow: true, pos: "#1f8a70", neg: "#c8502c", netJa: ["資金余剰", "資金不足"], gross: ["資金運用", "資金調達"] },
    POS: { ja: "保有残高", flow: false, pos: null, neg: null, netJa: ["純資産", "純負債"], gross: ["保有", "被保有"] },
    DPOS: { ja: "残高変化", flow: true, pos: "#4a76c4", neg: "#b5651d", netJa: ["純資産の増加", "純負債の増加"], gross: ["保有の増加", "被保有の増加"] },
    VAL: { ja: "評価変動", flow: true, pos: "#6b4fa0", neg: "#b5651d", netJa: ["評価益（純）", "評価損（純）"], gross: ["保有資産の評価変動", "発行負債の時価変動"] },
  };
  const INSTRS = {
    ALL: { ja: "すべての金融商品", dom: ["F2", "F3", "F4", "F51", "F52", "F6", "F7", "F8"], cross: "PI", split: ["F3", "F51", "F52"], issuer: null },
    DEBT: { ja: "債券", dom: ["F3"], cross: "PI_F3", split: ["F3"], issuer: null },
    GOVDEBT: { ja: "うち政府の債務（国債等）", dom: ["F3", "F4"], cross: "PI_F3", split: ["F3"], issuer: "GOV" },
    EQUITY: { ja: "株式・投資信託", dom: ["F51", "F52"], cross: "PI_F51", split: ["F51", "F52"], issuer: null },
    LOAN: { ja: "貸出（国内のみ）", dom: ["F4"], cross: null, split: ["F3"], issuer: null },
    DEPOSIT: { ja: "現金・預金（国内のみ）", dom: ["F2"], cross: null, split: ["F3"], issuer: null },
    INSPEN: { ja: "保険・年金（国内のみ）", dom: ["F6"], cross: null, split: ["F3"], issuer: null },
  };
  const BOP_KEY = { PI: "FA_PI_A", PI_F3: "FA_PI_F3_A", PI_F51: "FA_PI_F5_A" };

  // ---------------------------------------------------------------- 小道具
  const $ = id => document.getElementById(id);
  const tip = $("tip");
  const showTip = (html, ev) => { tip.innerHTML = html; tip.hidden = false; tip.style.left = Math.min(ev.clientX + 14, innerWidth - 380) + "px"; tip.style.top = Math.min(ev.clientY + 14, innerHeight - 150) + "px"; };
  const hideTip = () => { tip.hidden = true; };
  const fmtN = (v, d = 0) => v == null || isNaN(v) ? "–" : v.toLocaleString("ja-JP", { maximumFractionDigits: d, minimumFractionDigits: d });
  const usd = (bn, signed = false) => { if (bn == null || isNaN(bn)) return "–"; const s = signed && bn > 0 ? "+" : ""; const a = Math.abs(bn); return a >= 1000 ? `${s}${(bn / 1000).toFixed(a >= 10000 ? 0 : 1)}兆$` : a >= 1 ? `${s}${fmtN(bn * 10, 0)}億$` : `${s}${fmtN(bn * 10, 1)}億$`; };
  const local = (econ, bn, signed = false) => {
    if (bn == null) return "–"; const s = signed && bn > 0 ? "+" : ""; const e = D.economies[econ]; const a = Math.abs(bn);
    if (econ === "JPN") return `${s}${fmtN(bn / 1000, a < 10000 ? 1 : 0)}兆円`;
    if (econ === "KOR") return `${s}${fmtN(bn / 1000, 0)}兆₩`;
    return a >= 1000 ? `${s}${(bn / 1000).toFixed(1)}兆${e.cur_sym}` : `${s}${fmtN(bn, a < 10 ? 1 : 0)}0億${e.cur_sym}`;
  };
  const qIdx = p => { const m = /^(\d{4})-Q([1-4])$/.exec(p); return m ? +m[1] * 4 + (+m[2] - 1) : null; };
  const qLabel = i => `${Math.floor(i / 4)}-Q${(i % 4) + 1}`;
  const qShift = (q, n) => qLabel(qIdx(q) + n);
  const qJa = q => `${q.slice(0, 4)}年${q.slice(5)}`;
  const toMap = ser => new Map((ser || []).map(x => [x[0], x[1]]));
  const at = (ser, p) => { const v = toMap(ser).get(p); return v == null ? null : v; };
  const sum4 = (ser, endP) => { const m = toMap(ser); const i = qIdx(endP); if (i == null) return null; let s = 0, n = 0; for (let k = 0; k < 4; k++) { const v = m.get(qLabel(i - k)); if (v != null) { s += v; n++; } } return n === 4 ? s : null; };
  const last = ser => ser && ser.length ? ser[ser.length - 1] : null;
  // PIP は6月末・12月末。四半期 -> PIP 時点
  const pipDate = q => { const y = q.slice(0, 4), n = +q.slice(6); return n === 2 ? `${y}-06` : n === 4 ? `${y}-12` : null; };
  const pipQ = p => `${p.slice(0, 4)}-Q${p.slice(5, 7) === "06" ? 2 : 4}`;
  const pipHas = (kind, q) => { const d = pipDate(q); return d && D.matrix[kind] && D.matrix[kind][d]; };
  const pipPoints = kind => Object.keys(D.matrix[kind] || {}).sort();
  const fxAt = (e, P) => { const fx = e.fx_usd || {}; if (fx[P]) return fx[P]; const ks = Object.keys(fx).filter(k => k <= P).sort(); return ks.length ? fx[ks[ks.length - 1]] : null; };

  // ---------------------------------------------------------------- 状態・操作
  const state = { measure: "TX", scope: "GRID", instr: "ALL", period: null, show: "80", econ: "JPN", lock: null };
  $("sel-instr").innerHTML = Object.entries(INSTRS).map(([k, v]) => `<option value="${k}">${v.ja}</option>`).join("");
  $("sel-econ").innerHTML = Object.entries(D.economies).map(([k, e]) => `<option value="${k}">${e.name}</option>`).join("");
  document.querySelectorAll(".mode-btn").forEach(b => b.onclick = () => {
    state.measure = b.dataset.measure; document.querySelectorAll(".mode-btn").forEach(x => x.classList.toggle("active", x === b));
    document.body.dataset.measure = state.measure; fillPeriods(); render();
  });
  $("scope").querySelectorAll("button").forEach(b => b.onclick = () => { state.scope = b.dataset.scope; $("scope").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b)); fillPeriods(); render(); });
  $("sel-instr").onchange = e => { state.instr = e.target.value; render(); };
  $("sel-period").onchange = e => { state.period = e.target.value; render(); };
  $("sel-show").onchange = e => { state.show = e.target.value; render(); };
  $("sel-econ").onchange = e => { state.econ = e.target.value; fillPeriods(); render(); };
  $("chk-dom").onchange = $("chk-other").onchange = () => render();
  window.addEventListener("resize", () => render());

  /** 選べる期間：国内統計が存在する四半期。尺度ごとに「実績／推計」を判定してラベルに出す。 */
  function periodInfo(q) {
    const kind = INSTRS[state.instr].cross;
    if (!kind) return { actual: true, why: "国内統計のみ" };
    if (state.measure === "TX") return { actual: false, why: "国際分は金額＝実績、相手先＝按分推計" };
    if (state.measure === "POS") return pipHas(kind, q) ? { actual: true, why: "" } : { actual: false, why: "国際分は直近PIP＋国際収支フローの推計" };
    if (state.measure === "VAL") return pipHas(kind, q) && pipHas(kind, qShift(q, -4)) ? { actual: false, why: "国際分の相手先は按分推計（残高は実績）" } : { actual: false, why: "国際分は推計を含む" };
    return pipHas(kind, q) && pipHas(kind, qShift(q, -4)) ? { actual: true, why: "" } : { actual: false, why: "国際分は推計を含む" };
  }
  function availablePeriods() {
    const jp = D.economies.JPN; const st = (jp.fin.stock.HH?.A?.F || []).map(x => x[0]);
    const fl = (jp.fin.flow.HH?.A?.F || []).map(x => x[0]);
    const pipStart = pipPoints("PI")[0] || "2019-06";
    const needFlow = state.measure === "TX" || state.measure === "VAL", needLag = state.measure === "DPOS" || state.measure === "VAL";
    return st.filter(q => q >= pipQ(pipStart) && (!needFlow || sum4(jp.fin.flow.HH?.A?.F || [], q) != null) && (!needLag || st.includes(qShift(q, -4))));
  }
  function fillPeriods() {
    const sel = $("sel-period");
    if (state.scope === "DOM") {
      const e = D.economies[state.econ]; const ser = state.measure === "TX" ? (e.fin.flow.HH?.A?.F || []) : (e.fin.stock.HH?.A?.F || []);
      const ps = ser.map(x => x[0]).slice(-24).reverse();
      sel.innerHTML = ps.map(p => `<option value="${p}">${state.measure === "TX" ? `${qShift(p, -3)}〜${p}` : state.measure !== "POS" ? `${qShift(p, -4)}→${p}` : `${p} 末`}</option>`).join("");
      sel.value = ps[0] || ""; state.period = sel.value; return;
    }
    const ps = availablePeriods();
    sel.innerHTML = ps.slice().reverse().map(q => {
      const i = periodInfo(q);
      const lab = state.measure === "TX" ? `${qShift(q, -3)}〜${q}（12か月）` : state.measure !== "POS" ? `${qShift(q, -4)}末 → ${q}末（12か月）` : `${q}末`;
      return `<option value="${q}">${lab}｜${i.actual ? "実績" : "推計含む"}</option>`;
    }).join("");
    const actualOnes = ps.filter(q => periodInfo(q).actual);
    state.period = state.measure === "TX" ? ps[ps.length - 1] : (actualOnes[actualOnes.length - 1] || ps[ps.length - 1]);
    sel.value = state.period;
  }

  // ---------------------------------------------------------------- 国内：部門×部門
  /** 1経済の部門×部門セル（自国通貨）。w2w があれば実績、無ければ発行側の「残高」シェアで按分（フローは符号が混ざるので重みは残高から取る）。
      A/L は各部門の金融資産・負債の統計値そのもの（カードの資金運用・資金調達に使う）。 */
  function domesticCells(e, P, kind, instrs) {  // kind: "stock" | "flow"
    const src = kind === "stock" ? e.fin.stock : e.fin.flow;
    const w2w = e.w2w?.[kind] || {};
    const get = (s, ent, ins) => { const ser = src[s]?.[ent]?.[ins] || (ins === "F51" ? src[s]?.[ent]?.F5 : null); if (!ser) return null; return kind === "stock" ? at(ser, P) : sum4(ser, P); };
    const stockL = (s, ins) => { const ser = e.fin.stock[s]?.L?.[ins] || (ins === "F51" ? e.fin.stock[s]?.L?.F5 : null); return ser ? (at(ser, P) ?? last(ser)?.[1] ?? 0) : 0; };
    const cells = {}, actual = {}, A = {}, L = {};
    const add = (h, i, v, isAct) => { const k = h + "|" + i; cells[k] = (cells[k] || 0) + v; if (isAct) actual[k] = true; };
    for (const instr of instrs) {
      const w = w2w[instr];
      const a = {}, l = {}, sh = {};
      for (const s of SECT_ORDER) { a[s] = get(s, "A", instr); l[s] = get(s, "L", instr); sh[s] = Math.max(0, stockL(s, instr)); if (a[s] != null) A[s] = (A[s] || 0) + a[s]; if (l[s] != null) L[s] = (L[s] || 0) + l[s]; }
      const shT = d3.sum(Object.values(sh));
      for (const h of SECT_ORDER) {
        const wh = w && w[h];
        if (wh && Object.values(wh).some(ser => (kind === "stock" ? at(ser, P) : sum4(ser, P)) != null)) {
          for (const [i, ser] of Object.entries(wh)) { const x = kind === "stock" ? at(ser, P) : sum4(ser, P); if (x != null && SECT_ORDER.includes(i)) add(h, i, x, true); }
          continue;
        }
        if (a[h] == null || !shT) continue;
        for (const i of SECT_ORDER) if (sh[i] > 0) add(h, i, a[h] * sh[i] / shT, false);
      }
    }
    return { cells, actual, A, L };
  }
  /** 地域の部門×部門セルと部門別の統計値（USD10億）。DPOS は各時点の期末レートで換算した差。 */
  function domRegionUSD(rid, P, measure, instrs) {
    const cells = {}, actual = {}, A = {}, L = {}; let ok = false;
    const acc = (dst, src, f) => { for (const [k, v] of Object.entries(src)) dst[k] = (dst[k] || 0) + v * f; };
    for (const iso of DOM_MEMBERS[rid] || []) {
      const e = D.economies[iso]; if (!e) continue; const fx = fxAt(e, P); if (!fx) continue;
      if (measure === "DPOS") {
        const P0 = qShift(P, -4), fx0 = fxAt(e, P0); if (!fx0) continue;
        const a = domesticCells(e, P, "stock", instrs), b = domesticCells(e, P0, "stock", instrs);
        acc(cells, a.cells, fx); acc(cells, b.cells, -fx0); acc(A, a.A, fx); acc(A, b.A, -fx0); acc(L, a.L, fx); acc(L, b.L, -fx0);
        for (const k in a.actual) actual[k] = true; ok = true;
      } else {
        const c = domesticCells(e, P, measure === "TX" ? "flow" : "stock", instrs);
        acc(cells, c.cells, fx); acc(A, c.A, fx); acc(L, c.L, fx);
        for (const k in c.actual) actual[k] = true; ok = ok || Object.keys(c.cells).length > 0;
      }
    }
    return ok ? { cells, actual, A, L } : null;
  }
  /** 家計/企業の資産シェア、政府/金融/企業の負債シェア（PIP の内訳が無い分を割り振るため） */
  function assetSplit(rid, P, instrs) {
    let hh = 0, nfc = 0;
    for (const iso of DOM_MEMBERS[rid] || []) { const e = D.economies[iso]; if (!e) continue; for (const ins of instrs) { hh += Math.abs(at(e.fin.stock.HH?.A?.[ins] || e.fin.stock.HH?.A?.F5 || [], P) || 0); nfc += Math.abs(at(e.fin.stock.NFC?.A?.[ins] || e.fin.stock.NFC?.A?.F5 || [], P) || 0); } }
    const t = hh + nfc; return t ? { HH: hh / t, NFC: nfc / t } : { HH: 0.6, NFC: 0.4 };
  }
  function liabSplit(rid, P, instrs) {
    const s = { GOV: 0, FIN: 0, NFC: 0 };
    for (const iso of DOM_MEMBERS[rid] || []) { const e = D.economies[iso]; if (!e) continue; for (const sec of Object.keys(s)) for (const ins of instrs) s[sec] += Math.abs(at(e.fin.stock[sec]?.L?.[ins] || e.fin.stock[sec]?.L?.F5 || [], P) || 0); }
    const t = d3.sum(Object.values(s)); if (t) { for (const k in s) s[k] /= t; return s; }
    return null;
  }

  // ---------------------------------------------------------------- 国際：二国間
  function pipMap(kind, date) { const m = new Map(); for (const [hr, hs, ir, cs, v] of D.matrix[kind]?.[date] || []) m.set(`${hr}|${hs}|${ir}|${cs}`, v); return m; }
  function bopFlow(rid, key, quarters) {
    let s = 0, miss = 0;
    for (const iso of REG[rid].members) { const ser = D.bop[iso]?.[key]; if (!ser) { miss++; continue; } const m = toMap(ser); for (const q of quarters) { const v = m.get(q); if (v == null) miss++; else s += v; } }
    return { v: s, miss };
  }
  /** 時点 P の二国間残高（USD10億）。PIP 時点ならそのまま、間の四半期は国際収支フローを二国間シェアで配分して積み上げ。 */
  function posMap(kind, P) {
    if (pipHas(kind, P)) return { map: pipMap(kind, pipDate(P)), est: false, base: pipDate(P), quarters: [] };
    const pts = pipPoints(kind).filter(p => qIdx(pipQ(p)) <= qIdx(P));
    const base = pts[pts.length - 1]; if (!base) return { map: new Map(), est: true, base: null, quarters: [] };
    const bm = pipMap(kind, base);
    const quarters = []; for (let q = qShift(pipQ(base), 1); qIdx(q) <= qIdx(P); q = qShift(q, 1)) quarters.push(q);
    const tot = {}; for (const [k, v] of bm) { const hr = k.split("|")[0]; tot[hr] = (tot[hr] || 0) + Math.max(0, v); }
    const fl = {}; for (const hr of REG_ORDER) fl[hr] = bopFlow(hr, BOP_KEY[kind], quarters).v;
    const map = new Map();
    for (const [k, v] of bm) { const hr = k.split("|")[0]; const sh = tot[hr] ? Math.max(0, v) / tot[hr] : 0; map.set(k, v + (fl[hr] || 0) * sh); }
    return { map, est: true, base, quarters };
  }
  /** 尺度に応じた二国間セル（USD10億）。TX は国際収支のフローを二国間シェアで配分（金額は実績、配分は推計）。 */
  function crossMap(measure, P, instrKey) {
    const cfg = INSTRS[instrKey], kind = cfg.cross;
    if (!kind) return { map: new Map(), est: false, none: true, note: "この金融商品はクロスボーダーの二国間統計が無いため、対外分はすべて「その他海外」に計上。" };
    if (measure === "POS") { const r = posMap(kind, P); return { map: r.map, est: r.est, base: r.base, note: r.est ? `${r.base}の二国間残高（IMF PIP）に、${r.quarters[0]}〜${P}の証券投資フロー（IMF国際収支）を二国間シェアで配分して積み上げた推計。` : "" }; }
    if (measure === "DPOS") {
      const a = posMap(kind, P), b = posMap(kind, qShift(P, -4));
      const map = new Map(); for (const k of new Set([...a.map.keys(), ...b.map.keys()])) map.set(k, (a.map.get(k) || 0) - (b.map.get(k) || 0));
      return { map, est: a.est || b.est, base: a.base, note: (a.est || b.est ? "両端の一方以上が推計（直近PIP＋国際収支フロー）。" : "") + "残高の差＝取引＋価格変動＋為替変動＋その他。" };
    }
    // TX: 4四半期の国際収支フローを、直近PIPの二国間シェアで配分
    const pts = pipPoints(kind).filter(p => qIdx(pipQ(p)) <= qIdx(P)); const base = pts[pts.length - 1];
    const bm = base ? pipMap(kind, base) : new Map();
    const quarters = []; for (let k = 3; k >= 0; k--) quarters.push(qShift(P, -k));
    const tot = {}; for (const [k, v] of bm) { const hr = k.split("|")[0]; tot[hr] = (tot[hr] || 0) + Math.max(0, v); }
    const map = new Map(); const missAreas = [];
    for (const hr of REG_ORDER) { const f = bopFlow(hr, BOP_KEY[kind], quarters); if (f.miss) missAreas.push(SHORT[hr]); }
    for (const [k, v] of bm) { const hr = k.split("|")[0]; const sh = tot[hr] ? Math.max(0, v) / tot[hr] : 0; map.set(k, (bopFlow(hr, BOP_KEY[kind], quarters).v) * sh); }
    return { map, est: true, base, quarters, note: `各地域の証券投資フロー（IMF国際収支、${quarters[0]}〜${P}の実績・相手国不問）を、${base}の二国間残高シェアで配分した推計。金額は実取引、相手先の内訳が推計。` + (missAreas.length ? `国際収支が未公表の四半期あり：${[...new Set(missAreas)].join("・")}。` : "") };
  }

  // ---------------------------------------------------------------- グラフ構築
  function buildGraph(measureArg) {
    const measure = measureArg || state.measure;
    if (measure === "VAL") return diffGraphs(buildGraph("DPOS"), buildGraph("TX"));
    const P = state.period, cfg = INSTRS[state.instr];
    const hasDom = {}; for (const rid of REG_ORDER) hasDom[rid] = !!(DOM_MEMBERS[rid] || []).some(iso => D.economies[iso] && fxAt(D.economies[iso], P));
    const nid = (r, s) => `${r}|${hasDom[r] ? s : "ALL"}`;
    const edges = {}, loops = {};
    const add = (a, b, v, kind, act) => { if (!v) return; const k = a + ">" + b; const t = a === b ? loops : edges; if (!t[k]) t[k] = { a, b, v: 0, kind, actual: !!act }; t[k].v += v; if (!act) t[k].actual = false; };

    // 国内
    const dom = {};
    for (const rid of REG_ORDER) if (hasDom[rid]) {
      dom[rid] = domRegionUSD(rid, P, measure, cfg.dom);
      if (!dom[rid]) { hasDom[rid] = false; continue; }
      for (const [k, v] of Object.entries(dom[rid].cells)) {
        const [h, i] = k.split("|"); if (h === "ROW" || i === "ROW") continue;
        if (cfg.issuer && i !== cfg.issuer) continue;
        add(nid(rid, h), nid(rid, i), v, "dom", dom[rid].actual[k]);
      }
    }
    // 国際
    const cm = crossMap(measure, P, state.instr);
    const outCross = {}, inCross = {};
    const fallbackLiab = (() => { const xs = REG_ORDER.map(r => hasDom[r] ? liabSplit(r, P, cfg.split) : null).filter(Boolean); if (!xs.length) return { GOV: .35, FIN: .35, NFC: .30 }; const s = { GOV: 0, FIN: 0, NFC: 0 }; for (const x of xs) for (const k in s) s[k] += x[k] / xs.length; return s; })();
    for (const [key, v] of cm.map) {
      const [hr, hs, ir, cs] = key.split("|"); if (hr === ir || !v) continue;
      const srcs = !hasDom[hr] ? [[nid(hr, "ALL"), 1]] : H2G[hs] === "NFP_SPLIT" ? Object.entries(assetSplit(hr, P, cfg.split)).map(([s, sh]) => [nid(hr, s), sh]) : [[nid(hr, H2G[hs]), 1]];
      let dsts;
      if (!hasDom[ir]) dsts = [[nid(ir, "ALL"), cfg.issuer ? (liabSplit(ir, P, cfg.split) || fallbackLiab)[cfg.issuer] : 1]];
      else if (I2G[cs] === "SPLIT") { const sp = liabSplit(ir, P, cfg.split) || fallbackLiab; dsts = cfg.issuer ? [[nid(ir, cfg.issuer), sp[cfg.issuer]]] : Object.entries(sp).map(([s, sh]) => [nid(ir, s), sh]); }
      else { const g = I2G[cs]; if (cfg.issuer && g !== cfg.issuer) continue; dsts = [[nid(ir, g), 1]]; }
      const actual = !cm.est && cs !== "TOTAL" && H2G[hs] !== "NFP_SPLIT" && !cfg.issuer;
      for (const [a, sa] of srcs) for (const [b, sb] of dsts) { const val = v * sa * sb; add(a, b, val, "x", actual); outCross[a] = (outCross[a] || 0) + val; inCross[b] = (inCross[b] || 0) + val; }
    }
    // 残差（相手地域不明の対外ポジション）
    for (const rid of REG_ORDER) if (hasDom[rid] && dom[rid]) for (const s of GSECT) {
      const o = (dom[rid].cells[`${s}|ROW`] || 0) - (outCross[nid(rid, s)] || 0);
      const i = (dom[rid].cells[`ROW|${s}`] || 0) - (inCross[nid(rid, s)] || 0);
      if (cfg.issuer) { if (Math.abs(i) > 0.5) add("OTHER|ALL", nid(rid, s), i, "res", false); }
      else { if (Math.abs(o) > 0.5) add(nid(rid, s), "OTHER|ALL", o, "res", false); if (Math.abs(i) > 0.5) add("OTHER|ALL", nid(rid, s), i, "res", false); }
    }
    const E = Object.values(edges).filter(e => Math.abs(e.v) > 0.05);
    const L = Object.values(loops).filter(e => Math.abs(e.v) > 0.05);
    const outT = {}, inT = {}, statT = {};
    for (const e of E) { outT[e.a] = (outT[e.a] || 0) + e.v; inT[e.b] = (inT[e.b] || 0) + e.v; }
    for (const e of L) { outT[e.a] = (outT[e.a] || 0) + e.v; inT[e.b] = (inT[e.b] || 0) + e.v; }
    // カードの資金運用・資金調達は統計値（各部門の金融資産・負債）を使う。矢印は按分なので受け手側の合計とは一致しないことがある。
    if (!cfg.issuer) for (const rid of REG_ORDER) if (hasDom[rid] && dom[rid]) for (const s of GSECT) { const id = nid(rid, s); if (dom[rid].A[s] != null) { outT[id] = dom[rid].A[s]; statT[id] = true; } if (dom[rid].L[s] != null) inT[id] = dom[rid].L[s]; }
    return { E, L, outT, inT, statT, hasDom, nid, P, measure, cm, cfg };
  }

  /** ④ 評価変動 = ③ 残高変化 − ① 実取引（同じ期間・同じキーで差を取る）。相手先の按分を含むので常に推計扱い。 */
  function diffGraphs(Gd, Gt) {
    const key = e => e.a + ">" + e.b;
    const sub = (A, B) => { const m = new Map(); for (const e of A) m.set(key(e), { ...e, v: e.v, actual: false }); for (const e of B) { const k = key(e); if (m.has(k)) m.get(k).v -= e.v; else m.set(k, { ...e, v: -e.v, actual: false }); } return [...m.values()].filter(e => Math.abs(e.v) > 0.05); };
    const outT = {}, inT = {}; const ids = new Set([...Object.keys(Gd.outT), ...Object.keys(Gt.outT), ...Object.keys(Gd.inT), ...Object.keys(Gt.inT)]);
    for (const id of ids) { outT[id] = (Gd.outT[id] || 0) - (Gt.outT[id] || 0); inT[id] = (Gd.inT[id] || 0) - (Gt.inT[id] || 0); }
    return { E: sub(Gd.E, Gt.E), L: sub(Gd.L, Gt.L), outT, inT, statT: Gd.statT, hasDom: Gd.hasDom, nid: Gd.nid, P: Gd.P, measure: "VAL", cfg: Gd.cfg,
      cm: { est: true, note: "評価変動＝③残高変化−①実取引。国内は資金循環統計の残高差と取引の差（＝価格変動＋その他変動）、国際はPIP残高差と国際収支フロー配分の差。ドル建てなので為替換算の影響を含む。" } };
  }

  // ---------------------------------------------------------------- 描画：資本フロー図
  function drawGrid() {
    const G = buildGraph(), M = MEASURE[G.measure], flow = M.flow;
    const showDom = $("chk-dom").checked, showOther = $("chk-other").checked;
    let pool = G.E.filter(e => (showDom || e.kind !== "dom") && (showOther || e.kind !== "res")).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    let edges;
    if (state.show === "80") { const tot = d3.sum(pool, e => Math.abs(e.v)); let acc = 0; edges = []; for (const e of pool) { if (acc > tot * 0.8 && edges.length >= 6) break; acc += Math.abs(e.v); edges.push(e); } }
    else edges = pool.slice(0, +state.show);
    if (state.lock) edges = pool.filter(e => e.a === state.lock || e.b === state.lock).slice(0, 40);

    const svg = d3.select("#grid"); svg.selectAll("*").remove();
    const cols = [...REG_ORDER, "OTHER"], cw = 156, rh = 126, left = 62, top = 44, nw = 124, nh = 58;
    const W = left + cols.length * cw + 12, H = top + GSECT.length * rh + 24;
    svg.attr("width", W).attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    const pos = {}, nodes = [];
    cols.forEach((rid, j) => {
      const cx = left + j * cw + cw / 2;
      svg.append("text").attr("class", "colhead").attr("x", cx).attr("y", 22).attr("text-anchor", "middle").attr("fill", REG_COLOR[rid]).text(SHORT[rid]);
      if (j) svg.append("line").attr("x1", left + j * cw).attr("x2", left + j * cw).attr("y1", top - 10).attr("y2", H - 16).attr("stroke", "#eceff2");
      const secs = rid !== "OTHER" && G.hasDom[rid] ? GSECT : ["ALL"];
      secs.forEach(s => { const cy = s === "ALL" ? top + GSECT.length * rh / 2 : top + GSECT.indexOf(s) * rh + rh / 2; const id = `${rid}|${s}`; pos[id] = { x: cx, y: cy }; nodes.push({ id, rid, s, x: cx, y: cy, agg: s === "ALL" }); });
    });
    GSECT.forEach((s, i) => svg.append("text").attr("class", "rowhead").attr("x", 10).attr("y", top + i * rh + rh / 2 + 4).text(SECT[s]));
    edges = edges.filter(e => pos[e.a] && pos[e.b]);
    const maxAbs = d3.max(edges, e => Math.abs(e.v)) || 1, floor = maxAbs / 800;
    const wid = v => Math.max(1.4, 17 * Math.log10(1 + Math.abs(v) / floor) / Math.log10(1 + maxAbs / floor));

    const ribbonPath = (a, c, b, w0) => {
      const N = 28, P = [], T = [];
      for (let i = 0; i <= N; i++) { const t = i / N, u = 1 - t; P.push({ x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y }); const tx = 2 * u * (c.x - a.x) + 2 * t * (b.x - c.x), ty = 2 * u * (c.y - a.y) + 2 * t * (b.y - c.y); const l = Math.hypot(tx, ty) || 1; T.push({ x: -ty / l, y: tx / l }); }
      const head = Math.min(24, Math.max(10, 9 + w0 * 0.8)); let iH = N, acc = 0;
      for (let i = N; i > 0; i--) { acc += Math.hypot(P[i].x - P[i - 1].x, P[i].y - P[i - 1].y); if (acc >= head) { iH = i - 1; break; } }
      iH = Math.max(2, iH);
      const hw = i => (w0 / 2) * (1 - 0.62 * (i / iH)), Lp = [], Rp = [];
      for (let i = 0; i <= iH; i++) { Lp.push(`${(P[i].x + T[i].x * hw(i)).toFixed(1)},${(P[i].y + T[i].y * hw(i)).toFixed(1)}`); Rp.push(`${(P[i].x - T[i].x * hw(i)).toFixed(1)},${(P[i].y - T[i].y * hw(i)).toFixed(1)}`); }
      const bh = Math.max(hw(iH) * 2.4, 5), base = P[iH], n = T[iH];
      return `M${Lp.join(" L")} L${(base.x + n.x * bh).toFixed(1)},${(base.y + n.y * bh).toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)} L${(base.x - n.x * bh).toFixed(1)},${(base.y - n.y * bh).toFixed(1)} L${Rp.reverse().join(" L")} Z`;
    };
    const geo = edges.map(e => {
      const a = pos[e.a], b = pos[e.b], dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1;
      const sign = dx !== 0 ? (dx > 0 ? -1 : 1) : (dy > 0 ? 1 : -1), k = (dx === 0 ? 0.55 : 0.22) * L;
      const c = { x: a.x + dx / 2 + (-dy / L) * k * sign, y: a.y + dy / 2 + (dx / L) * k * sign };
      const trim = (p, q, d) => { const vx = q.x - p.x, vy = q.y - p.y, l = Math.hypot(vx, vy) || 1; return { x: p.x + vx / l * d, y: p.y + vy / l * d }; };
      const s = trim(a, c, dx === 0 ? 31 : 40), t = trim(b, c, dx === 0 ? 34 : 46);
      return { e, d: ribbonPath(s, c, t, wid(e.v)), s, mid: { x: .25 * s.x + .5 * c.x + .25 * t.x, y: .25 * s.y + .5 * c.y + .25 * t.y }, w: wid(e.v) };
    });
    const color = d => state.lock ? (d.e.a === state.lock ? "#2b6cb0" : "#dd6b20") : flow ? (d.e.v >= 0 ? M.pos : M.neg) : REG_COLOR[d.e.a.split("|")[0]];
    const gE = svg.append("g");
    const selE = gE.selectAll("path").data(geo).join("path").attr("class", "gedge").attr("d", d => d.d)
      .attr("fill", color).attr("fill-opacity", d => state.lock ? .72 : d.e.kind === "dom" ? .5 : .66)
      .attr("stroke", d => d.e.actual ? "#fff" : color(d)).attr("stroke-width", d => d.e.actual ? .6 : 1).attr("stroke-dasharray", d => d.e.actual ? null : "5 3").attr("stroke-opacity", .85)
      .on("mousemove", (ev, d) => { showTip(edgeTip(d.e, G), ev); if (!state.lock) hi(d.e.a, d.e.b); }).on("mouseleave", () => { hideTip(); if (!state.lock) hi(null); });
    gE.selectAll("circle").data(geo).join("circle").attr("cx", d => d.s.x).attr("cy", d => d.s.y).attr("r", d => Math.max(2.6, d.w / 2 + 1)).attr("fill", color).attr("stroke", "#fff").attr("stroke-width", 1).style("pointer-events", "none");
    svg.append("g").selectAll("text").data(geo.slice(0, Math.min(12, geo.length))).join("text").attr("class", "glab").attr("x", d => d.mid.x).attr("y", d => d.mid.y + 3).attr("text-anchor", "middle").text(d => usd(d.e.v, flow));

    // ノード（カード）
    const loopMax = d3.max(G.L, l => Math.abs(l.v)) || 1;
    const gN = svg.append("g").selectAll("g").data(nodes).join("g").attr("class", d => "gnode" + (d.agg ? " agg" : "")).attr("transform", d => `translate(${d.x - nw / 2},${d.y - nh / 2})`);
    gN.each(function (d) {
      const g = d3.select(this), loop = G.L.find(l => l.a === d.id);
      if (loop) { const lw = 2 + 8 * Math.sqrt(Math.abs(loop.v) / loopMax); g.append("rect").attr("class", "ring").attr("x", -lw / 2 - 2).attr("y", -lw / 2 - 2).attr("width", nw + lw + 4).attr("height", nh + lw + 4).attr("stroke", flow ? (loop.v >= 0 ? M.pos : M.neg) : REG_COLOR[d.rid]).attr("stroke-width", lw).attr("stroke-opacity", .3); }
      g.append("rect").attr("class", "body").attr("width", nw).attr("height", nh).attr("stroke", state.lock === d.id ? "#1b2733" : null).attr("stroke-width", state.lock === d.id ? 2 : null);
      g.append("rect").attr("x", 0).attr("y", 0).attr("width", 5).attr("height", nh).attr("rx", 2).attr("fill", REG_COLOR[d.rid]);
      g.append("text").attr("class", "sec").attr("x", 12).attr("y", 15).text(d.agg ? (d.rid === "OTHER" ? "相手地域不明" : "全主体") : SECT[d.s]);
      if (d.agg && d.rid !== "OTHER") g.append("text").attr("class", "agglab").attr("x", nw - 8).attr("y", 15).attr("text-anchor", "end").text("集約");
      const o = G.outT[d.id] || 0, i = G.inT[d.id] || 0, net = o - i;
      const lab = net >= 0 ? M.netJa[0] : M.netJa[1];
      g.append("text").attr("class", "net").attr("x", 12).attr("y", 34).attr("fill", net >= 0 ? "var(--surplus)" : "var(--deficit)").text(`${lab} ${usd(Math.abs(net))}`);
      g.append("text").attr("class", "sub").attr("x", 12).attr("y", 48).text(`${M.gross[0]} ${usd(o, flow)}／${M.gross[1]} ${usd(i, flow)}`);
    });
    gN.on("mousemove", (ev, d) => { showTip(nodeTip(d, G), ev); if (!state.lock) hi(d.id); }).on("mouseleave", () => { hideTip(); if (!state.lock) hi(null); })
      .on("click", (ev, d) => { state.lock = state.lock === d.id ? null : d.id; drawGrid(); });
    function hi(a, b) { selE.classed("dim", d => a != null && !(d.e.a === a || d.e.b === a || (b && (d.e.a === b || d.e.b === b)))); gN.classed("dim", d => a != null && d.id !== a && d.id !== b && !geo.some(x => (x.e.a === a || x.e.b === a) && (x.e.a === d.id || x.e.b === d.id))); }

    // 見出しと凡例
    const per = state.measure === "POS" ? `${qJa(state.period)}末` : `${qJa(qShift(state.period, state.measure === "TX" ? -3 : -4))}${state.measure === "TX" ? "" : "末"}〜${qJa(state.period)}末（12か月）`;
    $("grid-title").textContent = { TX: `① 実取引フロー：${per} に実際に動いた資金`, POS: `② 保有残高：${per} に誰が誰の資本を持っているか`, DPOS: `③ 残高変化：${per} の保有残高の増減`, VAL: `④ 評価変動：${per} に価格・為替で増減した保有価値（③−①）` }[state.measure];
    const kindNote = { TX: "矢印＝期間中に実際に取引された金額（国内＝資金循環統計の金融取引、国際＝国際収支の証券投資フロー）。価格・為替の評価変動は含まない。", POS: "矢印＝時点の保有残高（国内＝資金循環統計、国際＝IMF PIP の二国間証券保有）。", DPOS: "矢印＝残高の増減。取引だけでなく株価・債券価格・為替の変動を含む。したがって「資金が動いた額」ではない。", VAL: "矢印＝保有者から見た保有価値の増減のうち、取引によらない部分（株価・債券価格・為替、および統計上のその他変動）。プラス＝保有者に評価益、発行者側では負債の時価が膨らんだことを意味する（株式を発行する企業の「純負債の評価損」は株高の裏返し）。" }[state.measure];
    $("grid-note").textContent = `${kindNote} すべての矢印は同じ期間・同じ通貨（USD）に揃えてある。${G.cm.note || ""}「その他海外」＝相手地域を特定できない対外ポジション（貸出・預金・直接投資・外貨準備・金融センター経由）。`;
    const info = periodInfo(state.period);
    $("status-badge").className = "badge" + (info.actual ? "" : " est");
    $("status-badge").innerHTML = info.actual ? `<b>実績</b>　国内＝資金循環統計、国際＝IMF PIP` : `<b>推計を含む</b>　${info.why}`;
    const lockName = state.lock ? nodeName(state.lock) : null;
    $("grid-legend").innerHTML = `<span><b>向き</b>：●＝${flow ? "出し手" : "貸し手・保有者"}（太い側）　▶＝${flow ? "受け手" : "借り手・発行者"}（矢先）</span>`
      + (state.lock ? `<span><i style="background:#2b6cb0"></i>${lockName}が出し手　<i style="background:#dd6b20"></i>${lockName}が受け手　<a href="#" id="unlock">全体に戻す</a></span>`
        : flow ? `<span><i style="background:${M.pos}"></i>プラス＝${{ TX: "資金が流れた", DPOS: "残高が増えた", VAL: "評価益（価格・為替で価値が増えた）" }[state.measure]}</span><span><i style="background:${M.neg}"></i>マイナス＝${{ TX: "引き揚げた", DPOS: "残高が減った", VAL: "評価損" }[state.measure]}</span>`
          : `<span>色＝保有者の地域</span>`)
      + `<span><i class="dash"></i>破線＝相手先を按分した推計・Nowcast推計、実線＝統計が相手先まで示す実績</span>`
      + `<span>枠の太さ＝同じ主体の中（銀行間・企業間など）</span><span>破線の枠＝部門別に分解できない集約データ</span>`
      + `<span>表示 ${geo.length} 本／${pool.length} 本。セルをクリックで絞り込み。</span>`;
    const ul = $("unlock"); if (ul) ul.onclick = ev => { ev.preventDefault(); state.lock = null; drawGrid(); };
    drawStory(G);
    drawExplain(G);
    $("wealth").hidden = state.measure !== "VAL";
    if (state.measure === "VAL") drawWealth();
  }
  const nodeName = id => { const [r, s] = id.split("|"); return r === "OTHER" ? "その他海外" : `${SHORT[r]}の${SECT[s]}`; };
  function nodeTip(d, G) {
    const M = MEASURE[G.measure], flow = M.flow, o = G.outT[d.id] || 0, i = G.inT[d.id] || 0, net = o - i;
    const loop = G.L.find(l => l.a === d.id);
    let h = `<b>${d.rid === "OTHER" ? "その他海外（相手地域不明）" : `${REG[d.rid].name}の${SECT[d.s]}`}</b>`;
    if (d.agg && d.rid !== "OTHER") h += `<br><span class="m">部門別の統計が公開されておらず、全主体を1セルに集約</span>`;
    h += `<br>${M.gross[0]}（出し手として）${usd(o, flow)}<br>${M.gross[1]}（受け手として）${usd(i, flow)}`;
    h += `<br><b>${net >= 0 ? M.netJa[0] : M.netJa[1]} ${usd(Math.abs(net))}</b>`;
    if (G.measure === "VAL") h += `<br><span class="m">純＝保有資産の評価変動 − 発行負債の時価変動。株式を発行する企業は株高で負債の時価が膨らむため「評価損（純）」になる（＝株主側の評価益の裏返し）。政府は金利上昇で国債の時価が下がると「評価益（純）」。</span>`;
    h += `<br><span class="m">${G.statT[d.id] ? "資金循環統計の部門合計（金融資産・負債）。矢印は相手先を按分しているため、受け手側の矢印の合計とは一致しないことがある。" : "この主体に出入りする矢印の合計。"}</span>`;
    if (loop) h += `<br><span class="m">同じ主体の中（枠の太さ）${usd(loop.v, flow)}</span>`;
    if (d.agg && d.rid !== "OTHER") { const ca = regionCA(d.rid, state.period.slice(0, 4)); if (ca != null) h += `<br><span class="m">参考：経常収支 ${usd(ca, true)}（${state.period.slice(0, 4)}年, IMF WEO）＝ 世界に対する純資金供給</span>`; }
    h += `<br><span class="m">クリックでこのセルの資金の出入りだけを表示</span>`;
    return h;
  }
  function edgeTip(e, G) {
    const M = MEASURE[G.measure], flow = M.flow;
    const what = { TX: "期間中の純取引", POS: "保有残高", DPOS: "残高の増減", VAL: "評価変動（価格・為替・その他）" }[G.measure];
    let h = `<span style="color:#8fb3e0">●</span> ${flow ? "出し手" : "保有者"}：<b>${nodeName(e.a)}</b><br><span style="color:#f0a06a">▶</span> ${flow ? "受け手" : "発行者"}：<b>${nodeName(e.b)}</b><br>${what}：<b>${usd(e.v, flow)}</b>`;
    if (flow && e.v < 0) h += `（マイナス＝${{ TX: "出し手が引き揚げた", DPOS: "残高が減った", VAL: "保有価値が下がった" }[G.measure]}）`;
    const basis = G.measure === "VAL" ? (e.kind === "dom" ? "資金循環統計：残高の差 − 取引（相手部門は按分）" : e.kind === "res" ? "残差部分の評価変動" : "PIP残高の差 − 国際収支フロー配分（為替換算を含む）") : e.kind === "dom" ? (e.actual ? "資金循環統計（相手部門別の実績）" : "資金循環統計（相手部門は発行残高シェアで按分した推計）")
      : e.kind === "res" ? "残差＝資金循環統計の対外ポジション − 二国間で特定できた分"
        : G.measure === "TX" ? "国際収支のフロー（実績）× PIP の二国間シェア（推計）" : (e.actual ? "IMF PIP（二国間残高の実績）" : "IMF PIP ＋ 推計");
    h += `<br><span class="m">${basis}</span>`;
    if ((G.measure === "DPOS" || G.measure === "VAL") && e.kind === "x") { const fx = regionFx(e.b.split("|")[0], qShift(state.period, -4), state.period); if (fx != null) h += `<br><span class="m">この期間の発行国通貨の対ドル変化 ${(fx * 100).toFixed(1)}%。増減のうちこの分は為替による評価。実取引額は①で確認。</span>`; }
    return h;
  }
  function regionFx(rid, fromQ, toQ) {
    const isos = REG_FX[rid]; if (!isos) return null;
    const ch = isos.map(iso => { const e = D.economies[iso]; if (!e) return null; const a = fxAt(e, fromQ), b = fxAt(e, toQ); return a && b ? b / a - 1 : null; }).filter(x => x != null);
    return ch.length ? d3.mean(ch) : null;
  }
  function regionCA(rid, year) {
    let s = 0, any = false;
    for (const iso of REG[rid].members) { const w = D.weo[iso]; if (!w) continue; const r = w.BCA_NGDPD?.[year], g = w.NGDPD?.[year]; if (r != null && g) { s += r / 100 * g; any = true; } }
    return any ? s : null;
  }

  // ---------------------------------------------------------------- 読み解き（自動生成）
  function drawStory(G) {
    const M = MEASURE[G.measure], flow = M.flow;
    const nets = Object.keys({ ...G.outT, ...G.inT }).filter(id => !id.startsWith("OTHER")).map(id => ({ id, net: (G.outT[id] || 0) - (G.inT[id] || 0) })).sort((a, b) => b.net - a.net);
    const sur = nets[0], def = nets[nets.length - 1];
    const toFin = G.E.filter(e => e.kind === "dom" && e.b.endsWith("|FIN")).sort((a, b) => b.v - a.v)[0];
    const cross = G.E.filter(e => e.kind === "x").sort((a, b) => b.v - a.v)[0];
    const step = (h, who, amt, cls) => `<div class="step"><h4>${h}</h4><div class="who">${who}</div><div class="amt" style="color:${cls}">${amt}</div></div>`;
    if (!sur || !def) { $("story").innerHTML = ""; return; }
    const H = G.measure === "VAL" ? ["① 富が最も増えた主体（保有資産の値上がり）", "② 仲介経路で最大の評価変動", "③ 国境をまたぐ最大の評価変動", "④ 発行負債の時価が最も膨らんだ主体（株高・債券高の裏側）"]
      : G.measure === "POS" ? ["① 最大の純資産保有者", "② 最大の仲介残高", "③ 最大の対外保有", "④ 最大の純債務者"]
        : ["① 資金余剰の発生", "② 金融仲介", "③ 国境を越える", "④ 資金不足のファイナンス"];
    $("story").innerHTML =
      step(H[0], nodeName(sur.id), `${M.netJa[0]} ${usd(sur.net)}`, "var(--surplus)")
      + (toFin ? step(H[1], `${nodeName(toFin.a)} → ${nodeName(toFin.b)}`, usd(toFin.v, flow), "#66707a") : "")
      + (cross ? step(H[2], `${nodeName(cross.a)} → ${nodeName(cross.b)}`, usd(cross.v, flow), "#66707a") : "")
      + step(H[3], nodeName(def.id), `${M.netJa[1]} ${usd(Math.abs(def.net))}`, "var(--deficit)");
  }

  // ---------------------------------------------------------------- 背景の解釈
  const YEAR_NOTES = {
    2021: ["コロナ後の回復。主要中銀は超低金利・資産購入を継続し、米欧は大型財政。政府の国債発行を国内の金融部門と海外が吸収した。", "株高で証券残高が大きく膨らんだ年。③残高変化の多くは取引ではなく株式の評価益。", "中国は規制強化（IT・不動産）で株式からの資金引き揚げが始まった。", "日本は円が115円台へ下落、家計・年金・生保の海外証券投資は継続。"],
    2022: ["世界的な利上げ（FRB 0→4.25〜4.5%、ECBはマイナス金利を終了）。債券価格が急落し株式も下落したため、③残高変化の減少の大半は評価損であって売却ではない。", "ドル高（円は一時150円超、ユーロは1ドル割れ）で、ドル建てで測った非米資産の残高が縮んだ。日本の対外債券は円ヘッジコスト上昇で売却が進み、当局は円買い介入。", "米国への対内投資は高金利の魅力で継続。エネルギー高で産油国の余剰資金が積み上がった。", "米国では預金金利よりMMF・短期国債の利回りが高くなり、家計の資金が預金から市場性商品へ動き始めた。"],
    2023: ["高金利の据え置き（higher for longer）。米地銀危機（3月）で預金がMMFへ流出し、金融部門の中での資金配置が変わった。", "米大型株の反発でドル建て株式残高が回復。日本株も東証の資本効率要請と円安で海外投資家の買いが入った。", "日銀はYCCを柔軟化したが緩和を継続し円安（年末141円）。日本の家計・機関投資家の対外証券投資が再開。", "中国は景気低迷と不動産不況で株式・債券ともに海外からの資金が細った。"],
    2024: ["利下げ局面の入口。ECBが6月、FRBが9月に利下げ開始、日銀は3月にマイナス金利解除・7月に追加利上げ。8月の円キャリー巻き戻しで一時的に大きな資金移動。", "米株はAI関連主導で高値更新。世界の資金が米国企業の株式に集中し、米国への対内証券投資が積み増された。", "日本は新NISA（1月）で家計の海外株式投信への資金が加速。ドル建てでは円安（年末157円）が日本の資産残高を圧縮。", "中国は長期金利が低下し、海外からの株式資金は引き揚げ気味。金融センター経由の資金が増え、最終的な相手国が見えにくくなっている。"],
    2025: ["米国は前年の利下げ後に据え置き。4月の関税ショックで株式・国債・ドルが同時に売られ「米国資産離れ」が意識された。5〜6月に株は回復したがドルは主要通貨に対して大きく下落。", "ドル安（ユーロ1.17台、円144円前後）で、ドル建てで測った欧州・日本の資産残高は膨らむ。③残高変化のプラスの一部は為替による評価。", "日本は日銀の利上げ継続（1月0.5%）と超長期国債利回りの急上昇（5月）。生保・年金の外債から国内債への回帰観測。家計は新NISAで海外投信買いを継続。", "ドイツの財政拡張（防衛・インフラ）で欧州の国債発行が増え、欧州株にも資金が戻った。中国は関税とデフレ圧力で海外資金が慎重。"],
    2026: ["利下げが進む一方、各国の財政赤字と国債発行は高水準が続く。長期金利は高止まりし、政府部門が最大の資金の受け手であり続けている。", "国内の家計・年金の余剰資金が金融部門を経由して国債と海外証券に向かう構図が継続。", "日本は日銀の追加利上げ観測と超長期金利の上昇で、対外債券投資と国内債投資の綱引きが続く。", "米国は対内証券投資への依存が大きく、ドルと米金利の変化に資金フローが敏感。"],
  };
  function drawExplain(G) {
    const Y = state.period.slice(0, 4), M = MEASURE[G.measure];
    const notes = YEAR_NOTES[Y] || ["この期間の解釈メモは未作成。"];
    const head = { TX: "この期間の背景 ── なぜその方向に資金が動いたか", POS: "この時点の背景 ── なぜその保有構造になっているか", DPOS: "この期間の背景 ── 残高が動いた理由（取引と評価の両方）", VAL: "この期間の背景 ── 何の価格が動いて富を増減させたか" }[state.measure];
    $("ex-year-h").textContent = head;
    const caution = state.measure === "DPOS"
      ? "③は「残高の増減」であり、取引・価格変動・為替変動・その他の合計。資金が実際に動いた額を知りたいときは①に切り替える。"
      : state.measure === "TX" ? "①は取引ベースなので、株価や為替が動いても値は変わらない。残高がどれだけ増えたかは③で見る。"
        : state.measure === "VAL" ? "④は③−①。保有者にとっての評価益・評価損で、株式では発行企業側の「負債の評価増」と対になる。国内は各時点の期末レートで換算しているため、通貨の対ドル変化も含む（下の資産クラス別表は自国通貨ベースの価格効果）。"
          : "②は時点の残高。期間中に何が起きたかは①（取引）と③（残高変化）で見る。";
    $("ex-year").innerHTML = `<ul>${notes.map(n => `<li>${n}</li>`).join("")}</ul><p class="note">${caution}<span class="tag">解釈</span></p>`;
    const rows = [];
    for (const rid of REG_ORDER) {
      const members = REG[rid].members.filter(iso => D.weo[iso]);
      const wavg = ind => { let s = 0, w = 0; for (const iso of members) { const v = D.weo[iso][ind]?.[Y], g = D.weo[iso].NGDPD?.[Y]; if (v != null && g) { s += v * g; w += g; } } return w ? s / w : null; };
      const fx = regionFx(rid, qShift(state.period, -4), state.period);
      const cell = (v, d = 1, sign = true) => v == null ? "<td>–</td>" : `<td class="${sign ? (v >= 0 ? "pos" : "neg") : ""}">${sign && v > 0 ? "+" : ""}${v.toFixed(d)}</td>`;
      rows.push(`<tr><td>${SHORT[rid]}</td>${cell(fx == null ? null : fx * 100)}${cell(wavg("BCA_NGDPD"))}${cell(wavg("GGXCNL_NGDP"))}${cell(wavg("NGDP_RPCH"))}${cell(wavg("PCPIPCH"), 1, false)}</tr>`);
    }
    const mk = D.market || {}, y10 = mk.ea_10y?.[Y], y10p = mk.ea_10y?.[String(+Y - 1)];
    $("ex-data").innerHTML = `<table><tr><th>地域</th><th>通貨の対ドル<br>12か月変化 %</th><th>経常収支<br>/GDP %</th><th>財政収支<br>/GDP %</th><th>実質成長率 %</th><th>インフレ率 %</th></tr>${rows.join("")}</table>
      <p class="note">出所：IMF WEO（GDP加重の地域平均、${Y}年${+Y >= 2025 ? "・IMF推計／予測" : ""}）。為替は資金循環統計のUSD換算比率の期末値（他先進国＝加・韓の平均、中国・金融センター・新興国は未取得）。${y10 != null ? `ユーロ圏10年国債利回り ${y10p != null ? y10p.toFixed(2) + "% → " : ""}${y10.toFixed(2)}%（年末, ECB）。` : ""}
      経常黒字の地域は世界への純資金供給者、財政赤字の大きい地域は政府が最大の資金の受け手になりやすい。通貨がドルに対して下落した地域は、取引が無くてもドル建て残高が減る。</p>`;
  }

  // ---------------------------------------------------------------- 資産クラス別の評価変動（自国通貨ベースの価格効果 → 期末レートでUSD表示）
  const WCLASS = [
    { id: "F51", ja: "株式・出資金", ins: ["F51"] }, { id: "F52", ja: "投資信託", ins: ["F52"] }, { id: "F3", ja: "債券", ins: ["F3"] },
    { id: "F6", ja: "保険・年金", ins: ["F6"] }, { id: "F24", ja: "現金・預金・貸出", ins: ["F2", "F4"] }, { id: "F78", ja: "その他（デリバティブ等）", ins: ["F7", "F8"] },
    { id: "RE", ja: "不動産（住宅・土地）", ins: [] }, { id: "GOLD", ja: "金（中央銀行の貨幣用金）", ins: [] },
  ];
  const W = D.wealth || {};
  const hpiAt = (iso, q) => { const m = toMap(W.hpi?.[iso] || []); if (m.has(q)) return m.get(q); const ks = [...m.keys()].filter(k => k <= q).sort(); return ks.length ? m.get(ks[ks.length - 1]) : null; };
  /** financial asset valuation of one economy: sector x class -> {val, open} in USD bn (local price effect, converted at end-period rate) */
  function finValuation(e, P, sectors) {
    const P0 = qShift(P, -4), fx = fxAt(e, P); const out = {};
    if (!fx) return out;
    for (const s of sectors) for (const c of WCLASS) if (c.ins.length) {
      let val = 0, open = 0, any = false;
      for (const ins of c.ins) {
        const st = e.fin.stock[s]?.A?.[ins] || (ins === "F51" ? e.fin.stock[s]?.A?.F5 : null), fl = e.fin.flow[s]?.A?.[ins] || (ins === "F51" ? e.fin.flow[s]?.A?.F5 : null);
        const a = st ? at(st, P) : null, b = st ? at(st, P0) : null, f = fl ? sum4(fl, P) : null;
        if (a == null || b == null || f == null) continue;
        val += a - b - f; open += b; any = true;
      }
      if (any) out[s + "|" + c.id] = { val: val * fx, open: open * fx };
    }
    return out;
  }
  /** housing: annual dwellings+land (households) carried to P-4 with the price index, then price effect over the 12 months.
      US: Fed Z.1 revaluation series (actual). */
  function housingValuation(iso, P) {
    const e = D.economies[iso]; const fx = e && fxAt(e, P); if (!fx) return null;
    const P0 = qShift(P, -4);
    if (iso === "USA" && W.us_re?.HH?.RE) { const rv = sum4(W.us_re.HH.RE.reval, P), op = at(W.us_re.HH.RE.stock, P0); return rv != null && op != null ? { val: rv, open: op, actual: true } : null; }
    const h = W.housing?.[iso]?.HH; if (!h) return null;
    const dw = h.N111N || {}, ld = h.N211N || {}; const years = Object.keys(dw).filter(y => y <= P0.slice(0, 4)).sort(); const y = years[years.length - 1]; if (!y) return null;
    const base = (dw[y] || 0) + (ld[y] || 0); const h0 = hpiAt(iso, `${y}-Q4`), h1 = hpiAt(iso, P0), h2 = hpiAt(iso, P);
    if (!h0 || !h1 || !h2) return null;
    const open = base * h1 / h0; return { val: open * (h2 / h1 - 1) * fx, open: open * fx, actual: false, year: y, hpi: h2 / h1 - 1 };
  }
  function goldValuation(iso, P) {
    const g = W.gold?.[iso]?.CB?.F11; const e = D.economies[iso]; const fx = e && fxAt(e, P); if (!g || !fx || iso === "USA") return null;  // US official gold is at statutory book value
    const P0 = qShift(P, -4); const a = at(g.stock, P), b = at(g.stock, P0), f = sum4(g.flow, P);
    if (a == null || b == null || f == null) return null;
    return { val: (a - b - f) * fx, open: b * fx, actual: true };
  }
  function drawWealth() {
    const P = state.period, P0 = qShift(P, -4), sel = $("sel-wsector").value;
    const sectors = sel === "ALL" ? ["HH", "NFC", "GOV", "FIN"] : [sel];
    const regions = REG_ORDER.filter(r => (DOM_MEMBERS[r] || []).some(iso => D.economies[iso]));
    const cells = {}; // class|region -> {val, open, est}
    const add = (c, r, x, est) => { if (!x) return; const k = c + "|" + r; const o = cells[k] || { val: 0, open: 0, est: false }; o.val += x.val; o.open += x.open; o.est = o.est || est; cells[k] = o; };
    const HOUSE_MEMBERS = { EA: ["DEU", "FRA", "ITA", "ESP", "NLD"] };
    for (const r of regions) {
      for (const iso of DOM_MEMBERS[r]) {
        const e = D.economies[iso]; if (!e) continue;
        const fv = finValuation(e, P, sectors);
        for (const [k, x] of Object.entries(fv)) add(k.split("|")[1], r, x, true);
        if (sectors.includes("FIN")) { const gv = goldValuation(iso, P); if (gv && Math.abs(gv.val) > 0.05) add("GOLD", r, gv, false); }
      }
      if (sectors.includes("HH")) for (const iso of HOUSE_MEMBERS[r] || DOM_MEMBERS[r]) { const hv = housingValuation(iso, P); if (hv) add("RE", r, hv, !hv.actual); }
    }
    const maxAbs = d3.max(Object.values(cells), c => Math.abs(c.val)) || 1;
    const cell = (c, r) => { const x = cells[c + "|" + r]; if (!x) return `<td class="est">–</td>`; const w = Math.max(2, 60 * Math.sqrt(Math.abs(x.val) / maxAbs)); const pct = x.open ? x.val / Math.abs(x.open) * 100 : null; return `<td class="${x.val >= 0 ? "up" : "dn"}${x.est ? " est" : ""}" title="${x.est ? "推計" : "実績"}：期首残高 ${usd(x.open)}"><span class="bar" style="width:${w}px"></span>${usd(x.val, true)}<span class="pct">${pct == null ? "" : `(${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)`}</span></td>`; };
    let html = `<thead><tr><th class="rh">資産クラス ＼ 保有地域</th>${regions.map(r => `<th>${SHORT[r]}</th>`).join("")}</tr></thead><tbody>`;
    for (const c of WCLASS) { if (!regions.some(r => cells[c.id + "|" + r])) continue; html += `<tr><td class="rh">${c.ja}</td>${regions.map(r => cell(c.id, r)).join("")}</tr>`; }
    const tot = r => { let v = 0, o = 0, any = false; for (const c of WCLASS) { const x = cells[c.id + "|" + r]; if (x) { v += x.val; o += x.open; any = true; } } return any ? { val: v, open: o, est: true } : null; };
    html += `<tr class="total"><th class="rh">合計</th>${regions.map(r => { const x = tot(r); return x ? `<td class="${x.val >= 0 ? "up" : "dn"}">${usd(x.val, true)}<span class="pct">(${x.open ? (x.val / Math.abs(x.open) * 100).toFixed(1) : "–"}%)</span></td>` : "<td>–</td>"; }).join("")}</tr>`;
    // context rows: FX vs USD, house prices, gold price
    const gp = toMap(W.gold_price?.INDEX || []); const g1 = gp.get(P), g0 = gp.get(P0); const gch = g1 && g0 ? (g1 / g0 - 1) * 100 : null;
    html += `<tr class="ctx"><td class="rh">参考：通貨の対ドル変化</td>${regions.map(r => { const f = regionFx(r, P0, P); return `<td>${f == null ? "–" : (f >= 0 ? "+" : "") + (f * 100).toFixed(1) + "%"}</td>`; }).join("")}</tr>`;
    html += `<tr class="ctx"><td class="rh">参考：住宅価格指数の変化（BIS）</td>${regions.map(r => { const isos = DOM_MEMBERS[r].filter(i => hpiAt(i, P) && hpiAt(i, P0)); if (!isos.length) return "<td>–</td>"; const m = d3.mean(isos, i => hpiAt(i, P) / hpiAt(i, P0) - 1) * 100; return `<td>${m >= 0 ? "+" : ""}${m.toFixed(1)}%</td>`; }).join("")}</tr>`;
    html += `<tr class="ctx"><td class="rh">参考：金価格の変化（IMF, USD）</td><td colspan="${regions.length}" style="text-align:left">${gch == null ? "–" : (gch >= 0 ? "+" : "") + gch.toFixed(1) + "%"}　${g1 ? `（${P0} → ${P} の四半期平均、指数 ${g0.toFixed(0)} → ${g1.toFixed(0)}。中銀の金残高は期末時価なのでタイミングが少し異なる）` : ""}</td></tr></tbody>`;
    $("wealth-table").innerHTML = html;
    $("wealth-note").textContent = `${qJa(P0)}末 → ${qJa(P)}末の12か月。金融資産＝資金循環統計の残高差 − 取引（保有者側、自国通貨ベースの価格効果を期末レートでUSD表示、括弧内＝期首残高比）。不動産＝家計の住宅・土地（OECD年次バランスシート）を住宅価格指数（BIS）で四半期に延ばした価格効果の推計、米国のみFRB Z.1の再評価系列（実績）。金＝中央銀行の貨幣用金の残高差 − 取引（米国は簿価計上のため対象外）。`;
    $("wealth-foot").textContent = `注意：「全部門」の合計は、投資信託が保有する株式と家計が保有する投信、年金が保有する債券と家計の年金受給権のように、金融部門を介した二重計上を含む。最終的な富の持ち手を見るには「家計」を選ぶ。読み方：株式の評価益は株価、債券は金利低下、投信は中身の株・債券、保険・年金は運用資産の価格で動く。不動産は家計の最大の資産で、住宅価格が数％動くだけで金融資産の評価変動に匹敵する。家計の不動産以外（企業の不動産、非居住用）は対象外。ユーロ圏は独・仏・伊・西・蘭の合計。他先進国は加・韓。`;
  }
  $("sel-wsector").onchange = () => drawWealth();
  $("sel-rsector").onchange = () => drawRecent();

  // ---------------------------------------------------------------- 統計後の値動き：直近指数と評価変動の Nowcast
  const PR = D.prices || {};
  const qEndISO = q => { const y = +q.slice(0, 4), n = +q.slice(6); const m = n * 3; return `${y}-${String(m).padStart(2, "0")}-${new Date(y, m, 0).getDate()}`; };
  const onOrBefore = (ser, iso) => { let out = null; for (const x of ser || []) { if (x[0] <= iso) out = x; else break; } return out; };
  const lastOf = ser => ser && ser.length ? ser[ser.length - 1] : null;
  const chg = (ser, fromISO) => { const a = onOrBefore(ser, fromISO), b = lastOf(ser); return a && b && a[1] ? { v: b[1] / a[1] - 1, from: a[0], to: b[0], lvl: b[1] } : null; };
  const dchg = (ser, fromISO) => { const a = onOrBefore(ser, fromISO), b = lastOf(ser); return a && b ? { v: b[1] - a[1], from: a[0], to: b[0], lvl: b[1] } : null; };
  const REG_EQ = { JPN: ["JPN"], USA: ["USA"], EA: ["EA"], GBR: ["GBR"], CHN: ["CHN"], FC: ["HKG", "SGP", "CHE"], ADV: ["CAN", "KOR", "AUS"], EM: ["IND", "BRA", "MEX"] };
  const REG_FXM = { JPN: ["JPN"], USA: [], EA: ["EA"], GBR: ["GBR"], CHN: ["CHN"], FC: ["HKG", "SGP", "CHE"], ADV: ["CAN", "KOR", "AUS"], EM: ["IND", "BRA", "MEX"] };
  const pctS = (v, d = 1) => v == null ? "–" : `<span class="chg ${v >= 0 ? "up" : "dn"}">${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%</span>`;
  const bpS = v => v == null ? "–" : `<span class="chg ${v <= 0 ? "up" : "dn"}">${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}bp</span>`;
  const meanOf = xs => { const a = xs.filter(x => x != null); return a.length ? d3.mean(a) : null; };
  /** region-level price changes since a date: equity, 10y yield (pp), FX (USD per local), gold (USD) */
  function regionMoves(rid, fromISO) {
    const eq = meanOf((REG_EQ[rid] || []).map(i => chg(PR.eq?.[i], fromISO)?.v));
    const eqL = (REG_EQ[rid] || []).map(i => ({ i, c: chg(PR.eq?.[i], fromISO), lab: PR.labels?.[i] })).filter(x => x.c);
    let dy = null, ySrc = "";
    if (PR.yield10?.[rid]) { const d = dchg(PR.yield10[rid], fromISO); if (d) { dy = d.v; ySrc = `日次 ${d.to}`; } }
    else { const ms = (REG_EQ[rid] || []).map(i => { const ser = (PR.mei?.[i]?.YIELD_LT || []).map(x => [x[0] + "-28", x[1]]); return dchg(ser, fromISO); }).filter(Boolean); if (ms.length) { dy = d3.mean(ms, x => x.v); ySrc = `月次 ${ms[0].to.slice(0, 7)}（OECD）`; } }
    const fx = rid === "USA" ? 0 : meanOf((REG_FXM[rid] || []).map(i => chg(PR.fx?.[i], fromISO)?.v));
    const fxTo = lastOf(PR.fx?.[(REG_FXM[rid] || [])[0]])?.[0];
    const hpi = meanOf((DOM_MEMBERS[rid] || REG_EQ[rid] || []).map(i => { const ser = W.hpi?.[i]; if (!ser) return null; const a = onOrBefore(ser.map(x => [qEndISO(x[0]), x[1]]), fromISO), b = lastOf(ser); return a && b && qEndISO(b[0]) > fromISO ? b[1] / a[1] - 1 : null; }));
    const hpiTo = lastOf(W.hpi?.[(DOM_MEMBERS[rid] || REG_EQ[rid] || [])[0]])?.[0];
    return { eq, eqL, dy, ySrc, fx, fxTo, hpi, hpiTo };
  }
  function drawRecent() {
    const stq = (D.economies.JPN.fin.stock.HH?.A?.F || []).map(x => x[0]); const P = stq[stq.length - 1] || state.period;
    const fromISO = qEndISO(P), sel = $("sel-rsector").value, sectors = sel === "ALL" ? ["HH", "NFC", "GOV", "FIN"] : [sel];
    const regions = REG_ORDER;
    const gold = chg(PR.gold_daily, fromISO), goldM = lastOf(PR.gold_m);
    const ytd = `${P.slice(0, 4)}-01-01`;
    // ---- table 1: latest indices by region
    let h = `<thead><tr><th class="rh">地域</th><th>株価指数</th><th>最新値・日付</th><th>${qJa(P)}末以降</th><th>年初来</th><th>12か月</th><th>10年国債利回り</th><th>${qJa(P)}末以降</th><th>通貨の対ドル（${qJa(P)}末以降）</th><th>住宅価格（BIS、最新四半期）</th></tr></thead><tbody>`;
    for (const r of regions) {
      const m = regionMoves(r, fromISO);
      const eqCells = m.eqL.length ? m.eqL.map(x => `${x.lab || x.i}`).join("・") : "–";
      const lv = m.eqL.length ? m.eqL.map(x => `<span class="lvl">${fmtN(x.c.lvl, x.c.lvl > 1000 ? 0 : 2)}</span> <span class="dt">${x.c.to.slice(5)}</span>`).join(" / ") : "–";
      const y12 = meanOf((REG_EQ[r] || []).map(i => { const ser = PR.eq?.[i]; if (!ser) return null; const last = lastOf(ser); const d = new Date(last[0]); d.setFullYear(d.getFullYear() - 1); return chg(ser, d.toISOString().slice(0, 10))?.v; }));
      const yl = PR.yield10?.[r] ? lastOf(PR.yield10[r]) : null;
      const ylv = yl ? `${yl[1].toFixed(2)}%<span class="dt">${yl[0].slice(5)}</span>` : (() => { const i = (REG_EQ[r] || [])[0]; const mm = lastOf(PR.mei?.[i]?.YIELD_LT); return mm ? `${mm[1].toFixed(2)}%<span class="dt">${mm[0]}月次</span>` : "–"; })();
      const hp = m.hpiTo ? `${m.hpi == null ? "更新なし" : pctS(m.hpi)} <span class="dt">${m.hpiTo}</span>` : "–";
      h += `<tr><td class="rh" style="color:${REG_COLOR[r]}">${SHORT[r]}</td><td class="src">${eqCells}</td><td>${lv}</td><td>${pctS(m.eq)}</td><td>${pctS(meanOf((REG_EQ[r] || []).map(i => chg(PR.eq?.[i], ytd)?.v)))}</td><td>${pctS(y12)}</td><td>${ylv}</td><td>${bpS(m.dy)}</td><td>${r === "USA" ? "–" : pctS(m.fx)}</td><td>${hp}</td></tr>`;
    }
    h += `<tr class="ctx"><td class="rh">金（USD/oz）</td><td class="src">COMEX先物（日次）／IMF月次平均</td><td>${gold ? `${fmtN(gold.lvl, 0)}<span class="dt">${gold.to.slice(5)}</span>` : "–"}${goldM ? ` ／ ${fmtN(goldM[1], 0)}<span class="dt">${goldM[0]}</span>` : ""}</td><td>${pctS(gold?.v)}</td><td>${pctS(chg(PR.gold_daily, ytd)?.v)}</td><td>${pctS((() => { const l = lastOf(PR.gold_daily); if (!l) return null; const d = new Date(l[0]); d.setFullYear(d.getFullYear() - 1); return chg(PR.gold_daily, d.toISOString().slice(0, 10))?.v; })())}</td><td colspan="4"></td></tr>`;
    h += `<tr class="ctx"><td class="rh">米国 債券・REIT ETF</td><td class="src">BND（総合債券）／VNQ（REIT）</td><td>${lastOf(PR.bond_etf) ? fmtN(lastOf(PR.bond_etf)[1], 2) : "–"} ／ ${lastOf(PR.reit_etf) ? fmtN(lastOf(PR.reit_etf)[1], 2) : "–"}</td><td>${pctS(chg(PR.bond_etf, fromISO)?.v)} ／ ${pctS(chg(PR.reit_etf, fromISO)?.v)}</td><td>${pctS(chg(PR.bond_etf, ytd)?.v)} ／ ${pctS(chg(PR.reit_etf, ytd)?.v)}</td><td colspan="5"></td></tr></tbody>`;
    $("idx-table").innerHTML = h;
    $("idx-note").textContent = `株価指数・金先物・ETF は Yahoo Finance の日次終値（参考値、配当除く価格指数）。10年国債利回りは財務省（日）・米財務省（米）・英中銀（英）・ECB AAA（欧）の日次、それ以外は OECD 月次。為替は BIS 日次（USD/自国通貨）。住宅価格は BIS 四半期（名目）。上昇＝紫、下落・金利上昇＝橙。金融センター・他先進国・新興国は主要指数の単純平均。`;
    // ---- table 2: nowcast valuation = statistical stock at P x price move since P
    const rows = [];
    const eqW = { F51: 1, F52: 0.6, F6: 0.4 }, bdW = { F3: 1, F52: 0.4, F6: 0.6 };
    const cls = [["F51", "株式・出資金", "株価指数の変化"], ["F52", "投資信託", "株式60%＋債券40%の近似"], ["F3", "債券", "−7 × 10年利回りの変化（デュレーション近似）"], ["F6", "保険・年金", "株式40%＋債券60%の近似（統計上は準備金評価のため実際は小さめ）"], ["RE", "不動産（住宅・土地）", "BIS 住宅価格の変化（統計より新しい四半期がある場合のみ）"], ["GOLD", "金（中央銀行）", "金価格（USD）の変化を自国通貨換算"]];
    const cell = {}; const fxNow = {}; const fxP = {};
    for (const r of regions) {
      const mv = regionMoves(r, fromISO); const bond = mv.dy == null ? null : -7 * mv.dy / 100;
      let anyStock = false;
      for (const iso of DOM_MEMBERS[r] || []) {
        const e = D.economies[iso]; if (!e) continue; const fx0 = fxAt(e, P); const fxl = lastOf(PR.fx?.[iso])?.[1] ?? (iso === "USA" ? 1 : fx0); if (!fx0) continue;
        fxNow[r] = fxl; fxP[r] = fx0;
        const open = ins => d3.sum(sectors, s => at(e.fin.stock[s]?.A?.[ins] || (ins === "F51" ? e.fin.stock[s]?.A?.F5 : null) || [], P) || 0);
        for (const [c] of cls) if (["F51", "F52", "F3", "F6"].includes(c)) {
          const o = open(c); if (!o) continue; anyStock = true;
          const move = (eqW[c] || 0) * (mv.eq ?? 0) + (bdW[c] || 0) * (bond ?? 0);
          const ok = (eqW[c] ? mv.eq != null : true) && (bdW[c] ? bond != null : true);
          if (!ok) continue;
          const k = c + "|" + r; const x = cell[k] || { val: 0, open: 0 }; x.val += o * move * fxl; x.open += o * fx0; cell[k] = x;
        }
        if (sectors.includes("FIN")) { const g = W.gold?.[iso]?.CB?.F11; const o = g ? at(g.stock, P) : null; if (o && iso !== "USA" && gold) { const goldLocal = (1 + gold.v) * (fx0 / fxl) - 1; const k = "GOLD|" + r; const x = cell[k] || { val: 0, open: 0 }; x.val += o * goldLocal * fxl; x.open += o * fx0; cell[k] = x; } }
      }
      if (sectors.includes("HH") && mv.hpi != null) for (const iso of ({ EA: ["DEU", "FRA", "ITA", "ESP", "NLD"] }[r] || DOM_MEMBERS[r] || [])) { const hv = housingValuation(iso, P); if (hv) { const k = "RE|" + r; const x = cell[k] || { val: 0, open: 0 }; x.val += hv.open * mv.hpi; x.open += hv.open; cell[k] = x; } }
    }
    const cols = regions.filter(r => cls.some(([c]) => cell[c + "|" + r]));
    const maxAbs = d3.max(Object.values(cell), c => Math.abs(c.val)) || 1;
    let t = `<thead><tr><th class="rh">資産クラス ＼ 保有地域</th>${cols.map(r => `<th>${SHORT[r]}</th>`).join("")}<th class="rh">価格の代理変数</th></tr></thead><tbody>`;
    for (const [c, ja, proxy] of cls) {
      if (!cols.some(r => cell[c + "|" + r])) continue;
      t += `<tr><td class="rh">${ja}</td>${cols.map(r => { const x = cell[c + "|" + r]; if (!x) return `<td class="est">${c === "RE" ? "統計と同時点" : "–"}</td>`; const w = Math.max(2, 60 * Math.sqrt(Math.abs(x.val) / maxAbs)); return `<td class="${x.val >= 0 ? "up" : "dn"} est" title="期首（${P}末）残高 ${usd(x.open)}"><span class="bar" style="width:${w}px"></span>${usd(x.val, true)}<span class="pct">(${x.open ? ((x.val / x.open) * 100).toFixed(1) : "–"}%)</span></td>`; }).join("")}<td class="src">${proxy}</td></tr>`;
    }
    t += `<tr class="total"><th class="rh">合計（Nowcast）</th>${cols.map(r => { let v = 0, o = 0; for (const [c] of cls) { const x = cell[c + "|" + r]; if (x) { v += x.val; o += x.open; } } return `<td class="${v >= 0 ? "up" : "dn"}">${usd(v, true)}<span class="pct">(${o ? (v / o * 100).toFixed(1) : "–"}%)</span></td>`; }).join("")}<td></td></tr>`;
    t += `<tr class="ctx"><td class="rh">参考：期首残高のドル換算の変化（為替）</td>${cols.map(r => { let o = 0; for (const [c] of cls) { const x = cell[c + "|" + r]; if (x) o += x.open; } const f = fxNow[r] && fxP[r] ? fxNow[r] / fxP[r] - 1 : null; return `<td>${f == null ? "–" : `${usd(o * f, true)} ${pctS(f)}`}</td>`; }).join("")}<td class="src">${qJa(P)}末 → 直近の為替</td></tr></tbody>`;
    $("nowcast-table").innerHTML = t;
    const latestDates = [lastOf(PR.eq?.USA)?.[0], lastOf(PR.yield10?.USA)?.[0], lastOf(PR.fx?.JPN)?.[0]].filter(Boolean).sort();
    $("recent-badge").innerHTML = `<b>Nowcast</b>　${qJa(P)}末の統計残高 × ${latestDates[latestDates.length - 1] || ""} までの値動き`;
    $("recent-note").textContent = `統計（資金循環統計・IMF PIP）の最終時点は ${qJa(P)}末。上の表はそこから直近までに各資産の価格がどれだけ動いたか、下の表はその値動きを ${qJa(P)}末の保有残高（統計）に掛けて、公表を待たずに評価変動を推計したもの（取引は含まない）。その下の資本フロー図は統計そのものを ①実取引・②残高・③残高変化・④評価変動 に分けて示す。`;
    $("nowcast-note").textContent = `推計の前提：株式・出資金は各地域の代表的な株価指数（非上場株も同率と仮定）、債券は10年利回りの変化×デュレーション7年、投資信託・保険年金は株式と債券の固定比率で近似。自国通貨ベースの価格効果を直近の為替でUSD表示し、為替の影響は最下行に分離。米国の公的金は簿価のため対象外。金融センター・新興国は保有残高の統計が無く対象外。`;
    // ---- export for the e-mail digest (headless extractor reads window.__NOWCAST__)
    try {
      const idx = regions.map(r => { const m = regionMoves(r, fromISO); const yl = PR.yield10?.[r] ? lastOf(PR.yield10[r]) : null;
        const y12 = meanOf((REG_EQ[r] || []).map(i => { const ser = PR.eq?.[i]; if (!ser) return null; const last = lastOf(ser); const d = new Date(last[0]); d.setFullYear(d.getFullYear() - 1); return chg(ser, d.toISOString().slice(0, 10))?.v; }));
        return { region: r, name: SHORT[r], indices: m.eqL.map(x => ({ id: x.i, label: x.lab || x.i, level: x.c.lvl, date: x.c.to })),
          eq_since_stat: m.eq, eq_ytd: meanOf((REG_EQ[r] || []).map(i => chg(PR.eq?.[i], ytd)?.v)), eq_12m: y12,
          yield10_level: yl ? yl[1] : null, yield10_date: yl ? yl[0] : null, yield10_chg_pp: m.dy, fx_vs_usd_since_stat: r === "USA" ? 0 : m.fx, hpi_since_stat: m.hpi }; });
      const val = {};
      for (const [c, ja] of cls) for (const r of cols) { const x = cell[c + "|" + r]; if (x) { (val[c] ||= { label: ja, by_region: {} }).by_region[r] = { usd_bn: x.val, open_usd_bn: x.open, pct: x.open ? x.val / x.open : null }; } }
      const totals = {}; for (const r of cols) { let v = 0, o = 0; for (const [c] of cls) { const x = cell[c + "|" + r]; if (x) { v += x.val; o += x.open; } } totals[r] = { usd_bn: v, open_usd_bn: o, pct: o ? v / o : null }; }
      const fxeff = {}; for (const r of cols) { let o = 0; for (const [c] of cls) { const x = cell[c + "|" + r]; if (x) o += x.open; } const f = fxNow[r] && fxP[r] ? fxNow[r] / fxP[r] - 1 : null; fxeff[r] = f == null ? null : { usd_bn: o * f, pct: f }; }
      window.__NOWCAST__ = { stat_period: P, stat_period_ja: qJa(P), from: fromISO, sector: sel, regions: cols, region_names: SHORT,
        gold: gold ? { level: gold.lvl, date: gold.to, since_stat: gold.v, ytd: chg(PR.gold_daily, ytd)?.v } : null,
        bond_etf: { since_stat: chg(PR.bond_etf, fromISO)?.v, ytd: chg(PR.bond_etf, ytd)?.v }, reit_etf: { since_stat: chg(PR.reit_etf, fromISO)?.v, ytd: chg(PR.reit_etf, ytd)?.v },
        indices: idx, valuation: val, totals, fx_effect: fxeff, assumptions: $("nowcast-note").textContent };
    } catch (e) { window.__NOWCAST__ = { error: String(e) }; }
  }

  // ---------------------------------------------------------------- 国内の部門×部門（詳細）
  function buildDom() {
    const e = D.economies[state.econ], P = state.period, m = state.measure === "VAL" ? "DPOS" : state.measure;
    const instrs = INSTRS[state.instr].dom;
    let cells, actual;
    if (m === "DPOS") {
      const a = domesticCells(e, P, "stock", instrs), b = domesticCells(e, qShift(P, -4), "stock", instrs);
      cells = {}; actual = a.actual;
      for (const k of new Set([...Object.keys(a.cells), ...Object.keys(b.cells)])) cells[k] = (a.cells[k] || 0) - (b.cells[k] || 0);
    } else { const c = domesticCells(e, P, m === "TX" ? "flow" : "stock", instrs); cells = c.cells; actual = c.actual; }
    const rows = SECT_ORDER.map(s => ({ id: s, label: SECT_JA[s], sub: "", color: SECT_COLOR[s] })).filter(r => SECT_ORDER.some(i => cells[r.id + "|" + i] != null));
    const cols = SECT_ORDER.map(s => ({ id: s, label: SECT_JA[s], sub: "" }));
    const cell = (r, c) => { const v = cells[r.id + "|" + c.id]; return v == null ? null : { v, actual: !!actual[r.id + "|" + c.id] }; };
    return { rows, cols, cell, fmt: (v, s) => local(state.econ, v, s), groupRows: r => r.id, groupCols: c => c.id };
  }
  function drawBubbles(M) {
    const svg = d3.select("#bubbles"); svg.selectAll("*").remove();
    const rows = M.rows, cols = M.cols, flow = MEASURE[state.measure].flow;
    if (!rows.length) { svg.attr("width", 600).attr("height", 60).append("text").attr("x", 10).attr("y", 30).attr("fill", "#66707a").text("この時点のデータがありません"); return; }
    const cw = 118, rh = 66, left = 120, top = 44, right = 130, bottom = 50;
    const W = left + cols.length * cw + right, H = top + rows.length * rh + bottom;
    svg.attr("width", W).attr("height", H).attr("viewBox", `0 0 ${W} ${H}`);
    const vals = rows.flatMap(r => cols.map(c => M.cell(r, c)?.v)).filter(v => v != null);
    const maxAbs = d3.max(vals, Math.abs) || 1, floor = maxAbs / 2000, rmax = Math.min(cw, rh) * .45;
    const rad = v => v === 0 ? 0 : Math.max(1.5, rmax * Math.log10(1 + Math.abs(v) / floor) / Math.log10(1 + maxAbs / floor));
    cols.forEach((c, j) => svg.append("text").attr("class", "collab").attr("x", left + j * cw + cw / 2).attr("y", 26).attr("text-anchor", "middle").attr("font-weight", 700).text(c.label));
    svg.append("text").attr("class", "collab grp").attr("x", left - 8).attr("y", 26).attr("text-anchor", "end").text("保有部門 ＼ 発行・借入部門");
    rows.forEach((r, i) => {
      const y = top + i * rh + rh / 2;
      svg.append("text").attr("class", "rowlab").attr("x", left - 8).attr("y", y + 4).attr("text-anchor", "end").attr("fill", r.color).attr("font-weight", 700).text(r.label);
      cols.forEach((c, j) => {
        const cell = M.cell(r, c), cx = left + j * cw + cw / 2; if (!cell) return;
        const g = svg.append("g").attr("class", "cell");
        g.append("circle").attr("cx", cx).attr("cy", y).attr("r", rad(cell.v)).attr("fill", flow ? (cell.v >= 0 ? MEASURE[state.measure].pos : MEASURE[state.measure].neg) : r.color).attr("fill-opacity", .78).attr("class", cell.actual ? "actual" : "est");
        g.append("text").attr("class", "val" + (rad(cell.v) > 16 ? " in" : "")).attr("x", cx).attr("y", rad(cell.v) > 16 ? y + 3.5 : y + rad(cell.v) + 11).attr("text-anchor", "middle").text(M.fmt(cell.v, flow));
        g.on("mousemove", ev => showTip(`<b>${r.label}</b> → <b>${c.label}</b><br>${M.fmt(cell.v, flow)}<br><span class="m">${cell.actual ? "相手部門別の実績" : "比例配分による推計"}</span>`, ev)).on("mouseleave", hideTip);
      });
      const tot = d3.sum(cols, c => M.cell(r, c)?.v || 0);
      svg.append("text").attr("class", "val").attr("x", W - right + 12).attr("y", y + 4).text(M.fmt(tot, flow));
    });
    cols.forEach((c, j) => svg.append("text").attr("class", "val").attr("x", left + j * cw + cw / 2).attr("y", H - bottom + 18).attr("text-anchor", "middle").text(M.fmt(d3.sum(rows, r => M.cell(r, c)?.v || 0), flow)));
    svg.append("text").attr("class", "collab grp").attr("x", left - 8).attr("y", H - bottom + 18).attr("text-anchor", "end").text("列合計");
    svg.append("text").attr("class", "collab grp").attr("x", W - right + 12).attr("y", top - 8).text("行合計");
  }
  function drawChord(M) {
    const svg = d3.select("#chord"); svg.selectAll("*").remove();
    const W = svg.node().clientWidth || 420, H = svg.node().clientHeight || 420, R = Math.min(W, H) / 2 - 52;
    const ids = SECT_ORDER, n = ids.length, mat = Array.from({ length: n }, () => Array(n).fill(0));
    M.rows.forEach(r => M.cols.forEach(c => { const x = M.cell(r, c); if (!x || x.v <= 0) return; mat[ids.indexOf(r.id)][ids.indexOf(c.id)] += x.v; }));
    if (!d3.sum(mat.flat())) { svg.append("text").attr("x", 20).attr("y", 30).attr("fill", "#66707a").text("正の値がありません"); return; }
    const chord = d3.chordDirected().padAngle(.04).sortSubgroups(d3.descending)(mat);
    const g = svg.append("g").attr("transform", `translate(${W / 2},${H / 2})`);
    const arc = d3.arc().innerRadius(R).outerRadius(R + 14), ribbon = d3.ribbonArrow().radius(R - 2).padAngle(.01);
    const groups = g.append("g").selectAll("g").data(chord.groups).join("g");
    groups.append("path").attr("class", "arc").attr("d", arc).attr("fill", d => SECT_COLOR[ids[d.index]])
      .on("mousemove", (ev, d) => { showTip(`<b>${SECT_JA[ids[d.index]]}</b><br>出し手として ${M.fmt(d3.sum(mat[d.index]))}<br>受け手として ${M.fmt(d3.sum(mat.map(r => r[d.index])))}`, ev); rib.classed("dim", x => x.source.index !== d.index && x.target.index !== d.index); })
      .on("mouseleave", () => { hideTip(); rib.classed("dim", false); });
    groups.append("text").attr("class", "chordlab").each(d => { d.angle = (d.startAngle + d.endAngle) / 2; }).attr("dy", ".35em")
      .attr("transform", d => `rotate(${d.angle * 180 / Math.PI - 90}) translate(${R + 20}) ${d.angle > Math.PI ? "rotate(180)" : ""}`).attr("text-anchor", d => d.angle > Math.PI ? "end" : null).text(d => SECT_JA[ids[d.index]]);
    const rib = g.append("g").selectAll("path").data(chord).join("path").attr("class", "ribbon").attr("d", ribbon).attr("fill", d => SECT_COLOR[ids[d.source.index]])
      .on("mousemove", (ev, d) => showTip(`<b>${SECT_JA[ids[d.source.index]]}</b> → <b>${SECT_JA[ids[d.target.index]]}</b><br>${M.fmt(mat[d.source.index][d.target.index])}`, ev)).on("mouseleave", hideTip);
  }
  function renderDom() {
    const e = D.economies[state.econ], M = buildDom(), meas = MEASURE[state.measure];
    const per = state.measure === "TX" ? `${qShift(state.period, -3)}〜${state.period}（4四半期の取引）` : state.measure !== "POS" ? `${qShift(state.period, -4)}末→${state.period}末の残高変化` : `${state.period}末の残高`;
    $("mx-title").textContent = `${e.name}：部門×部門（${INSTRS[state.instr].ja}、${per}）`;
    const hasW2W = e.w2w && Object.keys(e.w2w[state.measure === "TX" ? "flow" : "stock"] || {}).length;
    $("mx-note").textContent = `行＝保有部門（資金の出し手）、列＝発行・借入部門（受け手）。円の面積は対数目盛。` + (hasW2W ? `実線の円＝相手部門別の実績（${state.econ === "EA" ? "ECB whom-to-whom" : "日銀 債券保有部門別"}）、点線＝比例配分の推計。` : `この経済は相手部門別統計が無いため、すべて比例配分の推計。`);
    $("chord-title").textContent = `${e.name}：部門間の関係`;
    $("chord-note").textContent = "帯の太さ＝金額、色＝出し手側の部門。正の値のみ。";
    $("status-badge").className = "badge" + (hasW2W ? "" : " est");
    $("status-badge").innerHTML = hasW2W ? `<b>実績＋推計</b>　${e.labels?.fin || ""}` : `<b>推計（比例配分）</b>　${e.labels?.fin || ""}`;
    $("mx-legend").innerHTML = meas.flow ? `<span><i style="background:${meas.pos}"></i>プラス</span><span><i style="background:${meas.neg}"></i>マイナス</span>` : SECT_ORDER.map(s => `<span><i style="background:${SECT_COLOR[s]}"></i>${SECT_JA[s]}</span>`).join("");
    $("side").innerHTML = `<h3>この経済のデータ</h3><table><tr><th>金融勘定</th><td>${e.clock.fin_stock || "–"}まで</td></tr><tr><th>相手部門別統計</th><td>${hasW2W ? (state.econ === "EA" ? "ECB whom-to-whom" : "日銀 債券のみ") : "なし"}</td></tr><tr><th>出所</th><td style="text-align:left">${e.labels?.fin || ""}</td></tr></table>`;
    drawBubbles(M); drawChord(M);
  }

  // ---------------------------------------------------------------- render
  function render() {
    const grid = state.scope === "GRID";
    $("grid-sec").hidden = !grid; $("dom-main").hidden = grid;
    $("ctl-econ").hidden = grid; $("ctl-show").hidden = !grid; $("ctl-dom").hidden = !grid; $("ctl-other").hidden = !grid;
    if (!state.period) fillPeriods();
    drawRecent();
    if (grid) drawGrid(); else renderDom();
    $("foot").textContent = `生成 ${D.generated_at.replace("T", " ")}　｜　①実取引フロー：資金循環統計の金融取引（国内）＋IMF国際収支の証券投資フロー（国際）。②保有残高：資金循環統計＋IMF PIP（旧CPIS）。③残高変化：②の差分（取引＋価格・為替変動）。④評価変動：③−①。国内は各時点の期末レートでUSD換算。不動産＝OECD年次非金融資産＋BIS住宅価格指数、米国はFRB Z.1再評価。金＝中央銀行の貨幣用金（F11）＋IMF金価格。中国・金融センター・新興国は部門別統計が公開されておらず全主体を集約。`;
  }

  document.body.dataset.measure = state.measure;
  fillPeriods();
  render();
})();
