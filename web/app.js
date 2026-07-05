/* 거시경제 지표 대시보드 프런트엔드
   STATIC_MODE: GitHub Pages 정적 배포용 — 서버 API 대신 미리 생성된 JSON 파일을 읽는다 */
const STATIC = !!window.STATIC_MODE;
const SECTIONS = {
  1: "1. 핵심 거시지표 (SEP 추적)",
  2: "2. 통화정책 준칙",
  3: "3. 산출갭·보조 이론 지표",
  4: "4. 금융·부채",
  5: "5. 국제기구 (IMF·World Bank)",
};
const PALETTE = [
  "#2456d6", "#e0403c", "#12945f", "#e8871a", "#8b4bd6", "#0f9bb5",
  "#d6338f", "#6b7280", "#a16207", "#15803d", "#be123c", "#4338ca",
  "#0369a1", "#b45309", "#7c2d92", "#334155", "#dc6803", "#0d9488",
  "#9333ea", "#65a30d", "#c2410c", "#1d4ed8", "#a21caf", "#475569",
];

const COUNTRIES = ["us", "kr", "jp", "ez"];
const INSTITUTION = {
  us: "🏛️ 연준 FOMC 시뮬레이션",
  kr: "🏛️ 한국은행 금통위 시뮬레이션",
  jp: "🏛️ 일본은행 금정위 시뮬레이션",
  ez: "🏛️ ECB 정책이사회 시뮬레이션",
};
const state = {
  country: "us",
  years: 10,               // 5 | 10 | 20 | "all"
  selected: { us: null, kr: null, jp: null, ez: null },   // Set of ids (국가별 기억)
  cache: {},               // country -> payload
  fomcData: {},            // country -> 시뮬레이션 결과 (전망 비교 패널용)
};
const DEFAULT_SEL = {
  us: ["core_pce", "policy_rate", "taylor"],
  kr: ["core_cpi", "policy_rate", "taylor"],
  jp: ["infl", "policy_rate", "taylor"],
  ez: ["core_cpi", "policy_rate", "taylor"],
};

let mainChart = null;
let phillipsChart = null;
let dotChart = null;
const STANCE_COLOR = { hawk: "#e0403c", neutral: "#6b7280", dove: "#2456d6" };

function colorFor(idx) { return PALETTE[idx % PALETTE.length]; }

function cutoffDate() {
  if (state.years === "all") return null;
  const d = new Date();
  d.setFullYear(d.getFullYear() - state.years);
  return d.toISOString().slice(0, 10);
}

async function loadCountry(country) {
  if (state.cache[country]) return state.cache[country];
  document.getElementById("loading").style.display = "block";
  const url = STATIC ? `data_${country}.json` : `/api/data?country=${country}`;
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    document.getElementById("loading").textContent =
      "데이터 로딩 실패: " + (err.error || res.status) + " — 새로고침(F5) 해보세요.";
    throw new Error("load fail");
  }
  const payload = await res.json();
  state.cache[country] = payload;
  document.getElementById("loading").style.display = "none";
  return payload;
}

function getSelected(country, payload) {
  if (!state.selected[country]) {
    state.selected[country] = new Set(DEFAULT_SEL[country]);
  }
  const ids = new Set(payload.series.map((s) => s.id));
  ids.add("phillips");
  for (const id of [...state.selected[country]]) {
    if (!ids.has(id)) state.selected[country].delete(id);
  }
  return state.selected[country];
}

function buildSidebar(payload) {
  const sel = getSelected(state.country, payload);
  const root = document.getElementById("sections");
  root.innerHTML = "";
  const ordered = payload.series.map((s, i) => ({ ...s, _idx: i }));
  for (const sec of [1, 2, 3, 4, 5]) {
    const h = document.createElement("div");
    h.className = "section-title";
    h.textContent = SECTIONS[sec];
    root.appendChild(h);
    for (const s of ordered.filter((x) => x.section === sec)) {
      root.appendChild(makeRow(s.id, s.name, s.desc, s._idx, sel));
    }
    if (sec === 3 && payload.phillips) {
      root.appendChild(makeRow("phillips", "필립스 곡선 (산점도 보기)",
        "실업률(가로축)과 인플레이션(세로축)의 관계를 점으로 표시합니다. 색이 진할수록 최근입니다.",
        -1, sel));
    }
  }
}

function makeRow(id, name, desc, idx, sel) {
  const label = document.createElement("label");
  label.className = "ind" + (sel.has(id) ? " on" : "");
  label.title = desc || "";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = sel.has(id);
  cb.addEventListener("change", () => {
    if (cb.checked) sel.add(id); else sel.delete(id);
    label.classList.toggle("on", cb.checked);
    render();
  });
  const sw = document.createElement("span");
  sw.className = "swatch";
  sw.style.background = idx >= 0 ? colorFor(idx) : "#666";
  const txt = document.createElement("span");
  txt.textContent = name;
  label.append(cb, sw, txt);
  return label;
}

function render() {
  const payload = state.cache[state.country];
  if (!payload) return;
  const sel = getSelected(state.country, payload);
  const cut = cutoffDate();

  // ---------- 메인 오버랩 차트 ----------
  const datasets = [];
  let hasLevel = false, hasNorm = false, hasRaw = false;
  payload.series.forEach((s, idx) => {
    if (!sel.has(s.id)) return;
    let pts = s.data;
    if (cut) pts = pts.filter((p) => p[0] >= cut);
    if (!pts.length) return;
    const isLevel = s.axis === "level";
    const doNorm = isLevel && s.norm !== false;
    if (isLevel) hasLevel = true;
    if (doNorm) hasNorm = true;
    if (isLevel && !doNorm) hasRaw = true;
    const base = doNorm ? pts[0][1] : 1;
    datasets.push({
      label: s.name,
      data: pts.map((p) => ({
        x: p[0],
        y: doNorm ? (p[1] / base) * 100 : p[1],
        raw: p[1],
      })),
      normed: doNorm,
      borderColor: colorFor(idx),
      backgroundColor: colorFor(idx),
      borderWidth: 2,
      borderDash: s.dash ? [7, 4] : undefined,
      pointRadius: s.dash ? 3.5 : 0,
      pointHoverRadius: 4,
      tension: 0.15,
      spanGaps: true,
      yAxisID: isLevel ? "y2" : "y",
      unit: s.unit,
    });
  });

  const opts = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearest", axis: "x", intersect: false },
    plugins: {
      legend: { position: "bottom", labels: { boxWidth: 14, font: { size: 12 } } },
      tooltip: {
        callbacks: {
          label(ctx) {
            const r = ctx.raw.raw;
            const u = ctx.dataset.unit || "";
            const shown = ctx.dataset.normed
              ? `${r.toLocaleString()} ${u} (지수 ${ctx.parsed.y.toFixed(1)})`
              : `${r.toLocaleString()}${u === "%" ? "%" : u ? " " + u : ""}`;
            return `${ctx.dataset.label}: ${shown}`;
          },
        },
      },
    },
    scales: {
      x: { type: "time", time: { tooltipFormat: "yyyy-MM", unit: "year" } },
      y: {
        position: "left",
        title: { display: true, text: "%" },
        grid: { color: (c) => (c.tick.value === 0 ? "#94a3b8" : "#eef1f6") },
      },
      y2: {
        position: "right",
        display: hasLevel,
        title: {
          display: true,
          text: hasNorm && hasRaw ? "지수(시작=100) · 원값 혼합"
            : hasRaw ? "원값" : "지수 (기간 시작=100)",
        },
        grid: { drawOnChartArea: false },
      },
    },
  };

  if (mainChart) mainChart.destroy();
  mainChart = new Chart(document.getElementById("mainChart"), {
    type: "line",
    data: { datasets },
    options: opts,
  });

  // ---------- 필립스 곡선 산점도 ----------
  const box = document.getElementById("phillipsBox");
  if (sel.has("phillips") && payload.phillips) {
    box.classList.remove("hidden");
    const ph = payload.phillips;
    let pts = ph.points;
    if (cut) pts = pts.filter((p) => p[2] >= cut);
    const years = pts.map((p) => +p[2].slice(0, 4));
    const minY = Math.min(...years), maxY = Math.max(...years);
    const data = pts.map((p) => {
      const t = maxY === minY ? 1 : (+p[2].slice(0, 4) - minY) / (maxY - minY);
      return {
        x: p[0], y: p[1], date: p[2],
        col: `rgba(36, 86, 214, ${0.15 + 0.85 * t})`,
      };
    });
    document.getElementById("phillipsTitle").textContent =
      `필립스 곡선 산점도 — 가로: ${ph.x}, 세로: ${ph.y} (진한 점 = 최근)`;
    if (phillipsChart) phillipsChart.destroy();
    phillipsChart = new Chart(document.getElementById("phillipsChart"), {
      type: "scatter",
      data: {
        datasets: [{
          data,
          pointBackgroundColor: data.map((d) => d.col),
          pointBorderColor: "transparent",
          pointRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (c) => `${c.raw.date.slice(0, 7)}  실업률 ${c.raw.x}%, 인플레이션 ${c.raw.y}%`,
            },
          },
        },
        scales: {
          x: { title: { display: true, text: ph.x } },
          y: { title: { display: true, text: ph.y } },
        },
      },
    });
  } else {
    box.classList.add("hidden");
    if (phillipsChart) { phillipsChart.destroy(); phillipsChart = null; }
  }
}

// ---------- AI 리포트 ----------
function renderReport(d) {
  const box = document.getElementById("reportContent");
  const meta = document.getElementById("reportMeta");
  if (d && d.status === "running") {
    box.className = "report-md empty";
    box.textContent = "⏳ 리포트 생성 중입니다… (30초~1분, 완료되면 자동으로 표시됩니다)";
    meta.textContent = "";
    return;
  }
  if (!d || d.none) {
    box.className = "report-md empty";
    box.textContent = d && d.error
      ? "생성 실패: " + d.error + " — 다시 시도해주세요."
      : "아직 생성된 리포트가 없습니다. '리포트 생성' 버튼을 눌러주세요.";
    meta.textContent = "";
    return;
  }
  box.className = "report-md";
  box.innerHTML = marked.parse(d.markdown || "");
  meta.textContent = `생성: ${d.created} · 데이터 기준: ${d.data_updated}`;
}

// ---------- 위원회 시뮬레이션 ----------
function renderFomc(d) {
  const empty = document.getElementById("fomcContent");
  const result = document.getElementById("fomcResult");
  const meta = document.getElementById("fomcMeta");
  document.getElementById("fomcTitle").textContent = INSTITUTION[state.country];
  if (d && d.status === "running") {
    empty.classList.remove("hidden");
    empty.textContent = "⏳ 위원들이 토론 중입니다… (2~5분, 완료되면 자동으로 표시됩니다)";
    result.classList.add("hidden");
    meta.textContent = "";
    return;
  }
  if (!d || d.none) {
    empty.classList.remove("hidden");
    if (d && d.error) empty.textContent = "실행 실패: " + d.error + " — 다시 시도해주세요.";
    result.classList.add("hidden");
    meta.textContent = "";
    return;
  }
  empty.classList.add("hidden");
  result.classList.remove("hidden");
  meta.textContent = `실행: ${d.created} · 데이터 기준: ${d.data_updated}`;
  state.fomcData[state.country] = d;
  renderCompare();

  const votes = d.members.map((m) => m.vote.decision_1m);
  document.getElementById("fomcDecision").textContent =
    `⚖️ 표결 결과: ${d.decision} (위원별 1개월 결정 — ` +
    d.members.map((m) => `${m.title}: ${m.vote.decision_1m}`).join(", ") +
    `) · 현재 금리 ${d.current_rate}%`;

  // 점도표: x축 0/1/2 = 1개월/6개월/1년, 위원별 색상
  const horizons = ["1개월 후", "6개월 후", "1년 후"];
  const offset = { hawk: -0.09, neutral: 0, dove: 0.09 };
  const datasets = d.members.map((m) => ({
    type: "scatter",
    label: `${m.title}`,
    data: [
      { x: 0 + offset[m.stance], y: m.vote.rate_1m },
      { x: 1 + offset[m.stance], y: m.vote.rate_6m },
      { x: 2 + offset[m.stance], y: m.vote.rate_1y },
    ],
    backgroundColor: STANCE_COLOR[m.stance],
    pointRadius: 7,
    pointHoverRadius: 8,
  }));
  datasets.push({
    type: "line",
    label: `현재 금리 (${d.current_rate}%)`,
    data: [{ x: -0.4, y: d.current_rate }, { x: 2.4, y: d.current_rate }],
    borderColor: "#94a3b8",
    borderDash: [6, 4],
    borderWidth: 1.5,
    pointRadius: 0,
  });
  if (dotChart) dotChart.destroy();
  dotChart = new Chart(document.getElementById("dotChart"), {
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 14, font: { size: 12 } } },
        tooltip: {
          callbacks: {
            label: (c) => `${c.dataset.label}: ${c.parsed.y}%`,
          },
        },
      },
      scales: {
        x: {
          type: "linear", min: -0.5, max: 2.5,
          ticks: { stepSize: 1, callback: (v) => horizons[v] ?? "" },
          grid: { display: false },
        },
        y: { title: { display: true, text: "적정 정책금리 (%)" } },
      },
    },
  });

  // 토론 내용
  const t = document.getElementById("fomcTranscript");
  t.innerHTML = "";
  const addBubble = (cls, who, text) => {
    const b = document.createElement("div");
    b.className = "bubble " + cls;
    const w = document.createElement("span");
    w.className = "who";
    w.textContent = who;
    const body = document.createElement("span");
    body.textContent = text;
    b.append(w, body);
    t.appendChild(b);
  };
  const round1 = document.createElement("div");
  round1.className = "round-title";
  round1.textContent = "1라운드 — 모두발언";
  t.appendChild(round1);
  d.members.forEach((m) => addBubble(m.stance, m.title, m.opening));
  const round2 = document.createElement("div");
  round2.className = "round-title";
  round2.textContent = "2라운드 — 반론과 최종 표결";
  t.appendChild(round2);
  d.members.forEach((m) => addBubble(
    m.stance,
    `${m.title} (1개월: ${m.vote.decision_1m} ${m.vote.rate_1m}% / 6개월: ${m.vote.rate_6m}% / 1년: ${m.vote.rate_1y}%)`,
    m.final));
  addBubble("chair", "의장 성명", d.chair);
}

// ---------- 전망 비교 패널 (실제 vs IMF vs SEP vs AI) ----------
function renderCompare() {
  const payload = state.cache[state.country];
  const box = document.getElementById("compareContent");
  if (!payload) return;
  const byId = {};
  payload.series.forEach((s) => { byId[s.id] = s; });
  const year = +payload.updated.slice(0, 4);
  const lastOf = (id) => {
    const s = byId[id];
    return s && s.data.length ? s.data[s.data.length - 1] : null;
  };
  const yearVal = (id, y) => {
    const s = byId[id];
    if (!s) return null;
    const hit = s.data.find((p) => p[0].slice(0, 4) === String(y));
    return hit ? hit[1] : null;
  };
  const fomc = state.fomcData[state.country];
  const median = (arr) => [...arr].sort((a, b) => a - b)[Math.floor(arr.length / 2)];
  const ai = fomc && fomc.members ? {
    m1: median(fomc.members.map((m) => m.vote.rate_1m)),
    m6: median(fomc.members.map((m) => m.vote.rate_6m)),
    y1: median(fomc.members.map((m) => m.vote.rate_1y)),
  } : null;
  const hasSep = !!byId.sep_ffr;
  const coreId = byId.core_pce ? "core_pce" : (byId.core_cpi ? "core_cpi" : "infl");
  const inflId = byId.head_pce ? "head_pce" : (byId.cpi ? "cpi" : "infl");
  const fmt = (v) => (v == null ? "—" : `${Math.round(v * 100) / 100}%`);

  const rows = [
    { name: "정책금리", actual: lastOf("policy_rate"),
      ai: ai ? [ai.m1, ai.m6, ai.y1] : null,
      sep: hasSep ? [yearVal("sep_ffr", year), yearVal("sep_ffr", year + 1)] : null, imf: null },
    { name: "실질GDP 성장률", actual: lastOf("gdp_growth"), ai: null,
      sep: hasSep ? [yearVal("sep_gdp", year), yearVal("sep_gdp", year + 1)] : null,
      imf: [yearVal("imf_gdp_fc", year), yearVal("imf_gdp_fc", year + 1)] },
    { name: "인플레이션 (헤드라인)", actual: lastOf(inflId), ai: null,
      sep: hasSep ? [yearVal("sep_pce", year), yearVal("sep_pce", year + 1)] : null,
      imf: [yearVal("imf_infl_fc", year), yearVal("imf_infl_fc", year + 1)] },
    { name: "근원 인플레이션", actual: lastOf(coreId), ai: null,
      sep: hasSep ? [yearVal("sep_core", year), yearVal("sep_core", year + 1)] : null, imf: null },
    { name: "실업률", actual: lastOf("unrate"), ai: null,
      sep: hasSep ? [yearVal("sep_unrate", year), yearVal("sep_unrate", year + 1)] : null,
      imf: [yearVal("imf_unemp_fc", year), yearVal("imf_unemp_fc", year + 1)] },
  ];

  let html = '<table class="compare"><tr><th>지표</th><th>현재 실제</th>' +
    '<th>AI 시뮬레이션<span class="src">1개월 / 6개월 / 1년</span></th>' +
    (hasSep ? `<th>연준 SEP<span class="src">${year}년말 / ${year + 1}년말</span></th>` : "") +
    `<th>IMF 전망<span class="src">${year}년 / ${year + 1}년</span></th></tr>`;
  for (const r of rows) {
    html += `<tr><td class="rowname">${r.name}</td>`;
    html += `<td>${r.actual ? `${fmt(r.actual[1])}<span class="src">${r.actual[0].slice(0, 7)}</span>` : "—"}</td>`;
    html += `<td>${r.ai ? r.ai.map(fmt).join(" / ") : "—"}</td>`;
    if (hasSep) html += `<td>${r.sep ? r.sep.map(fmt).join(" / ") : "—"}</td>`;
    html += `<td>${r.imf ? r.imf.map(fmt).join(" / ") : "—"}</td></tr>`;
  }
  html += "</table>";
  html += `<div class="compare-note">· AI 시뮬레이션은 매파·중립·비둘기파 3인 표결의 <b>중간값</b>` +
    (fomc ? ` (이번 결정: ${fomc.decision})` : " (아직 실행 전이면 — 표시)") +
    `이고 금리에만 적용됩니다 · SEP는 FOMC 위원 전망 중간값(연말 기준)` +
    ` · IMF는 세계경제전망(WEO) 연평균 기준이라 서로 기준 시점이 조금 다릅니다</div>`;
  box.className = "";
  box.innerHTML = html;
}

async function fetchAI(kind, params = "", country = state.country) {
  if (STATIC) {
    const res = await fetch(`${kind}_${country}.json`);
    if (!res.ok) return { none: true };
    return res.json();
  }
  const res = await fetch(`/api/${kind}?country=${country}${params}`);
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.error || res.status);
  return d;
}

function bindAI(kind, renderFn, btnId, forceBtnId, waitMsg) {
  const btn = document.getElementById(btnId);
  const forceBtn = document.getElementById(forceBtnId);
  if (STATIC) {
    // 정적 사이트에서는 방문자가 생성을 실행할 수 없음 (매주 자동 갱신)
    btn.style.display = "none";
    forceBtn.style.display = "none";
    return;
  }
  const runIt = async (force) => {
    const country = state.country;
    const orig = btn.textContent;
    btn.disabled = forceBtn.disabled = true;
    btn.textContent = waitMsg;
    try {
      let d = await fetchAI(kind, "&run=1" + (force ? "&force=1" : ""), country);
      if (state.country === country) renderFn(d);
      while (d.status === "running") {
        await new Promise((r) => setTimeout(r, 8000)); // 8초마다 완료 확인
        d = await fetchAI(kind, "", country);
        if (state.country !== country) return; // 폴링 중 국가 전환 시 중단
      }
      if (d.error) throw new Error(d.error);
      if (state.country === country) renderFn(d);
    } catch (e) {
      alert(`생성 실패: ${e.message}`);
    } finally {
      btn.disabled = forceBtn.disabled = false;
      btn.textContent = orig;
    }
  };
  btn.addEventListener("click", () => runIt(false));
  forceBtn.addEventListener("click", () => runIt(true));
}

async function loadAIPanels() {
  // 저장된 결과만 조회 (크레딧 소모 없음)
  try { renderReport(await fetchAI("report")); } catch (e) { /* 무시 */ }
  try { renderFomc(await fetchAI("fomc")); } catch (e) { /* 무시 */ }
}

async function switchCountry(country) {
  state.country = country;
  for (const c of COUNTRIES) {
    document.getElementById(`tab-${c}`).classList.toggle("active", c === country);
  }
  const payload = await loadCountry(country);
  document.getElementById("updated").textContent = "데이터 갱신: " + payload.updated;
  buildSidebar(payload);
  render();
  renderCompare();
  loadAIPanels();
}

for (const c of COUNTRIES) {
  document.getElementById(`tab-${c}`).addEventListener("click", () => switchCountry(c));
}
document.querySelectorAll(".period").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".period").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.years = btn.dataset.years === "all" ? "all" : +btn.dataset.years;
    render();
  });
});

bindAI("report", renderReport, "reportBtn", "reportForceBtn", "분석 중… (30초~1분)");
bindAI("fomc", renderFomc, "fomcBtn", "fomcForceBtn", "위원들이 토론 중… (1~2분)");

switchCountry("us");
