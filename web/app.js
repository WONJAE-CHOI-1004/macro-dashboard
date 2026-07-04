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

const state = {
  country: "us",
  years: 10,               // 5 | 10 | 20 | "all"
  selected: { us: null, kr: null },   // Set of ids (국가별 기억)
  cache: {},               // country -> payload
};
const DEFAULT_SEL = {
  us: ["core_pce", "policy_rate", "taylor"],
  kr: ["core_cpi", "policy_rate", "taylor"],
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
    if (sec === 3) {
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
  document.getElementById("fomcTitle").textContent =
    state.country === "kr" ? "🏛️ 한국은행 금통위 시뮬레이션" : "🏛️ 연준 FOMC 시뮬레이션";
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
  document.getElementById("tab-us").classList.toggle("active", country === "us");
  document.getElementById("tab-kr").classList.toggle("active", country === "kr");
  const payload = await loadCountry(country);
  document.getElementById("updated").textContent = "데이터 갱신: " + payload.updated;
  buildSidebar(payload);
  render();
  loadAIPanels();
}

document.getElementById("tab-us").addEventListener("click", () => switchCountry("us"));
document.getElementById("tab-kr").addEventListener("click", () => switchCountry("kr"));
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
