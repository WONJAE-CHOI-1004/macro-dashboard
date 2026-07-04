# -*- coding: utf-8 -*-
"""
지표 자동 분석 + AI 리포트/통화정책위원회 시뮬레이션
- 회귀·시계열 분석(오쿤 계수, 필립스 기울기, 테일러 갭 등)은 여기서 직접 계산하고
- NVIDIA NIM API의 LLM(Qwen 3.5)은 그 숫자를 '해석'하는 역할만 한다.
- LLM에는 최신값뿐 아니라 12개월/8분기 추이, 역사적 백분위, 준칙 괴리의
  시계열까지 통째로 전달해 넓은 맥락에서 판단하게 한다.
"""
import json
import os
import re
import threading
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
NV_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NV_MODEL = "qwen/qwen3.5-122b-a10b"


def _load_env():
    env = {}
    try:
        with open(os.path.join(BASE, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


NV_KEY = _load_env().get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_API_KEY", "")

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_NV_LOCK = threading.Lock()  # 무료 티어는 동시 호출 시 빈 응답 → 전역 직렬화


def nv_chat(system, user, max_tokens=1400, temperature=0.4):
    """NVIDIA NIM 호출. 무료 티어는 연속·동시 호출 시 '빈 응답'을 돌려주는 경우가 있어
    전역 직렬화 + 점증 대기 재시도(5→10→20→40초, 최대 5회)로 감싼다."""
    with _NV_LOCK:
        return _nv_chat_inner(system, user, max_tokens, temperature)


def _nv_chat_inner(system, user, max_tokens, temperature):
    last_err = None
    for attempt in range(5):
        if attempt:
            time.sleep(5 * 2 ** (attempt - 1))  # 5, 10, 20, 40초 대기 후 재시도
        try:
            req = urllib.request.Request(
                NV_URL,
                data=json.dumps({
                    "model": NV_MODEL,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "max_tokens": max_tokens + (1000 if attempt >= 2 else 0),
                    "temperature": temperature,
                }).encode("utf-8"),
                headers={"Authorization": f"Bearer {NV_KEY}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=360) as r:
                d = json.loads(r.read().decode("utf-8"))
            msg = d["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            content = _THINK_RE.sub("", content).strip()
            if content:
                return content
            last_err = RuntimeError("빈 응답 (레이트리밋 추정)")
        except Exception as e:
            last_err = e
    raise last_err


# ---------------------------------------------------------------- 통계 도구
def ols(xs, ys):
    """단순회귀 y = a + b·x → (기울기 b, 절편 a, R², 표본수)"""
    n = len(xs)
    if n < 8:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    if sxx == 0:
        return None
    b = sxy / sxx
    a = my - b * mx
    ss_res = sum((ys[i] - (a + b * xs[i])) ** 2 for i in range(n))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return round(b, 3), round(a, 3), round(r2, 3), n


def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (sx * sy)


def _pairs(a, b, since="2000-01-01"):
    """두 시계열을 날짜로 맞춰 (x리스트, y리스트) 반환"""
    db = dict(b)
    xs, ys = [], []
    for d, v in a:
        if d >= since and d in db:
            xs.append(v)
            ys.append(db[d])
    return xs, ys


def _fmt(v):
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


# ---------------------------------------------------------------- 지표 진단
def build_digest(country, payload):
    """대시보드의 모든 시리즈에서 추이·백분위·회귀분석·이상 신호를 뽑아 정리한다."""
    sd = {s["id"]: s["data"] for s in payload["series"]}
    name = {s["id"]: s["name"] for s in payload["series"]}
    kr = country == "kr"
    core_id = "core_cpi" if kr else "core_pce"
    infl_id = "cpi" if kr else "head_pce"
    long_id, short_id = ("ktb10", "ktb3") if kr else ("gs10", "gs2")

    def last(sid):
        d = sd.get(sid)
        return d[-1] if d else None

    def ago(sid, k):
        d = sd.get(sid)
        return d[-1 - k] if d and len(d) > k else None

    def trail(sid, n=12, step=1):
        """최근 n개 값을 (step 간격으로) '연-월: 값' 나열"""
        d = sd.get(sid)
        if not d:
            return "자료 없음"
        pts = d[::-1][: n * step : step][::-1]
        return ", ".join(f"{p[0][:7]} {_fmt(p[1])}" for p in pts)

    def pctile(sid, since="2000-01-01"):
        """현재 값이 2000년 이후 분포에서 몇 번째 백분위인지"""
        vals = [v for t, v in sd.get(sid, []) if t >= since]
        if len(vals) < 40:
            return None
        cur = vals[-1]
        return round(sum(1 for x in vals if x <= cur) / len(vals) * 100)

    def pct_str(sid):
        p = pctile(sid)
        return f" [2000년 이후 백분위 {p}% — 높을수록 역사적 고점권]" if p is not None else ""

    L = []
    add = L.append
    signals = []
    cur_rate = last("policy_rate")[1]

    # ---------- 1. 물가 ----------
    add("[1. 물가]")
    add(f"- 근원 인플레이션({name[core_id]}) 12개월 추이: {trail(core_id, 12)}{pct_str(core_id)}")
    add(f"- 헤드라인 인플레이션 12개월 추이: {trail(infl_id, 12)}")
    if not kr:
        add(f"- CPI 12개월 추이: {trail('cpi', 12)}")
    c_now, c_6m, c_12m = last(core_id), ago(core_id, 6), ago(core_id, 12)
    if c_now and c_6m and c_12m:
        add(f"- 근원 모멘텀: 6개월 변화 {c_now[1]-c_6m[1]:+.2f}%p, 12개월 변화 {c_now[1]-c_12m[1]:+.2f}%p (목표 2%)")
        if c_now[1] > 2.5:
            signals.append(f"근원 인플레이션 {c_now[1]:.1f}%로 목표(2%) 상회, 6개월 모멘텀 {c_now[1]-c_6m[1]:+.1f}%p")
        elif c_now[1] < 1.5:
            signals.append(f"근원 인플레이션 {c_now[1]:.1f}%로 목표(2%) 하회")
    w_now, w_12m = last("wti"), ago("wti", 12)
    if w_now and w_12m:
        chg = (w_now[1] / w_12m[1] - 1) * 100
        add(f"- WTI 유가 12개월 추이(2개월 간격): {trail('wti', 6, 2)} → 전년비 {chg:+.1f}%")
        if abs(chg) >= 20:
            signals.append(f"유가 전년비 {chg:+.0f}% — {'물가 상방' if chg > 0 else '물가 하방'} 압력")
    if "exp_inflation" in sd:
        lab = "기대인플레이션(향후1년, 소비자조사)" if kr else "기대인플레이션(10년 BEI, 시장)"
        add(f"- {lab} 12개월 추이: {trail('exp_inflation', 12)}")
        ei = last("exp_inflation")
        if ei and ei[1] >= 3.0:
            signals.append(f"기대인플레이션 {ei[1]:.1f}% — 기대 고착화(디앵커링) 우려 구간")

    # ---------- 2. 고용 ----------
    add("\n[2. 고용]")
    add(f"- 실업률 12개월 추이: {trail('unrate', 12)}{pct_str('unrate')}")
    if last("nairu"):
        add(f"- 자연실업률(NAIRU) 추정: {_fmt(last('nairu')[1])}%")
    add(f"- 실업률 갭(실업률−NAIRU) 12개월 추이: {trail('unemp_gap', 12)}")
    ug = last("unemp_gap")
    if ug and ug[1] <= -0.3:
        signals.append(f"실업률갭 {ug[1]:+.1f}%p — 노동시장 과열")
    elif ug and ug[1] >= 0.3:
        signals.append(f"실업률갭 {ug[1]:+.1f}%p — 노동시장 냉각")
    if kr:
        add(f"- 취업자 증감(만명, 전년동월비) 12개월 추이: {trail('emp_chg', 12)}")
        e = sd.get("emp_chg", [])
        neg = 0
        for _, v in reversed(e):
            if v < 0:
                neg += 1
            else:
                break
        if neg >= 3:
            signals.append(f"취업자 증감이 {neg}개월 연속 마이너스 — 고용 악화 신호")
    else:
        add(f"- 비농업 고용 증감(천명) 12개월 추이: {trail('payrolls', 12)}")
        p6 = [v for _, v in sd.get("payrolls", [])[-6:]]
        if p6:
            add(f"- 최근 6개월 평균 고용증가: {sum(p6)/len(p6):.0f}천 명 (10만 명 미만이면 냉각 신호로 봄)")

    # ---------- 3. 성장·경기 ----------
    add("\n[3. 성장·경기]")
    add(f"- 실질GDP 성장률 8분기 추이: {trail('gdp_growth', 8)}")
    add(f"- 산출갭 8분기 추이: {trail('output_gap', 8)}{pct_str('output_gap')}")
    og = last("output_gap")
    if og and abs(og[1]) >= 0.5:
        signals.append(f"산출갭 {og[1]:+.1f}% — 경기 {'과열' if og[1] > 0 else '침체'} 방향")
    if kr:
        add(f"- 선행지수 순환변동치 12개월 추이: {trail('leading', 12)} (100이 추세선)")
        add(f"- 동행지수 순환변동치 12개월 추이: {trail('coincident', 12)}")
        l_now, l_6m = last("leading"), ago("leading", 6)
        if l_now and l_6m:
            chg = round(l_now[1] - l_6m[1], 1)
            if chg <= -1:
                signals.append(f"선행지수 6개월간 {chg}p 하락 — 경기 둔화 신호")
            elif chg >= 1:
                signals.append(f"선행지수 6개월간 +{chg}p 상승 — 경기 회복 신호")
        ip = sd.get("indprod", [])
        if len(ip) > 12:
            ip_yoy = round((ip[-1][1] / ip[-13][1] - 1) * 100, 1)
            add(f"- 전산업생산지수: 최근 12개월 추이 {trail('indprod', 12)} (전년비 {ip_yoy:+.1f}%)")

    # ---------- 4. 금리·금융·재정 ----------
    add("\n[4. 금리·금융·재정]")
    add(f"- 정책금리 12개월 경로: {trail('policy_rate', 12)}{pct_str('policy_rate')}")
    add(f"- {name[long_id]} 12개월 추이: {trail(long_id, 12)}")
    add(f"- {name[short_id]} 12개월 추이: {trail(short_id, 12)}")
    lv, sv = last(long_id), last(short_id)
    if lv and sv:
        spread = round(lv[1] - sv[1], 2)
        lv12, sv12 = ago(long_id, 12), ago(short_id, 12)
        old = f" (12개월 전 {lv12[1]-sv12[1]:+.2f}%p)" if lv12 and sv12 else ""
        add(f"- 장단기 금리차: {spread:+.2f}%p{old}")
        if spread < 0:
            signals.append("장단기 금리 역전 — 역사적으로 경기침체 선행 신호")
    add(f"- 실질 정책금리(정책금리−근원 인플레) 12개월 추이: {trail('real_rate', 12)} — 0% 위여야 긴축적(테일러 원칙)")
    rr = last("real_rate")
    if rr and rr[1] < 0:
        signals.append(f"실질 정책금리 {rr[1]:+.2f}% — 마이너스(테일러 원칙 위배 상태)")
    if kr:
        h = sd.get("housing", [])
        if len(h) > 24:
            h_yoy_tr = ", ".join(
                f"{h[i][0][:7]} {(h[i][1]/h[i-12][1]-1)*100:+.1f}%" for i in range(len(h) - 12, len(h), 2))
            add(f"- 주택매매가격 전년비 추이: {h_yoy_tr}")
        if last("debt") and last("debt_ratio"):
            d5 = sd["debt"][-5:]
            r5 = sd["debt_ratio"][-5:]
            add("- 국가채무 최근 5년: " + ", ".join(f"{p[0][:4]}년 {_fmt(p[1])}조원" for p in d5)
                + " / GDP대비 " + ", ".join(f"{_fmt(p[1])}%" for p in r5))
    else:
        d = sd.get("debt", [])
        if len(d) >= 21:
            add("- 연방정부 부채(조$) 최근 5년(연간): "
                + ", ".join(f"{p[0][:4]}년 {_fmt(p[1])}" for p in d[-21::4]))
        sep_map = [("sep_ffr", "연방기금금리"), ("sep_core", "근원 PCE"), ("sep_pce", "PCE 인플레"),
                   ("sep_unrate", "실업률"), ("sep_gdp", "실질GDP 성장률")]
        sep_lines = []
        for sid, lab in sep_map:
            dd = sd.get(sid)
            if dd:
                sep_lines.append(f"  · {lab}: " + ", ".join(f"{p[0][:4]}년말 {_fmt(p[1])}%" for p in dd[-3:]))
        if sep_lines:
            add("- FOMC 스스로의 전망(SEP 중간값) — 현재 실제값과 비교해 전망 이탈 여부를 판단할 것:")
            L.extend(sep_lines)
            year = payload["updated"][:4]
            proj = next((v for t, v in sd.get("sep_core", []) if t[:4] == year), None)
            if proj is not None and c_now and c_now[1] - proj >= 0.3:
                signals.append(f"현재 근원 인플레이션({c_now[1]:.1f}%)이 연준 자체 {year}년 전망({proj}%)을 상회 — 전망 이탈")

    # ---------- 4b. 가계부채·자산시장·신용 ----------
    add("\n[4b. 가계부채·자산시장·신용]")
    d_unit = "조원" if kr else "조 달러"
    if "hh_debt" in sd:
        add(f"- 가계부채 8분기 추이({d_unit}): {trail('hh_debt', 8)}")
    if "hh_debt_gdp" in sd:
        add(f"- GDP 대비 가계부채 비율 8분기 추이: {trail('hh_debt_gdp', 8)}")
        hd = sd["hh_debt_gdp"]
        if len(hd) >= 5 and all(hd[i][1] <= hd[i + 1][1] for i in range(len(hd) - 5, len(hd) - 1)):
            signals.append(f"GDP 대비 가계부채 비율이 4분기 연속 상승 (현재 {hd[-1][1]}%)")
    if "dsr" in sd:
        add(f"- 가계 원리금 상환 부담률(DSR) 8분기 추이: {trail('dsr', 8)}")
    if "mktcap" in sd:
        add(f"- 주식 시가총액({d_unit}) 추이: {trail('mktcap', 8, 3 if kr else 1)}")
    if "foreign_share" in sd:
        add(f"- 주식 외국인 보유 비중(추정) 8분기 추이: {trail('foreign_share', 8)}")
    si_now, si_12 = last("stock_idx"), ago("stock_idx", 12)
    if si_now and si_12:
        chg = (si_now[1] / si_12[1] - 1) * 100
        add(f"- 주가지수 12개월 추이: {trail('stock_idx', 12)} (전년비 {chg:+.1f}%)")
        if chg >= 30:
            signals.append(f"주가지수 전년비 {chg:+.0f}% 급등 — 자산가격 과열·부(富)의 효과 점검 필요")
        elif chg <= -20:
            signals.append(f"주가지수 전년비 {chg:+.0f}% 급락 — 금융여건 긴축 효과")
    if kr:
        fx_now, fx_12 = last("usdkrw"), ago("usdkrw", 12)
        if fx_now and fx_12:
            chg = (fx_now[1] / fx_12[1] - 1) * 100
            add(f"- 원/달러 환율 12개월 추이: {trail('usdkrw', 12)} (전년비 {chg:+.1f}%)")
            if chg >= 8:
                signals.append(f"원/달러 전년비 {chg:+.0f}% 상승 — 원화 약세, 수입물가 상방 압력")
            elif chg <= -8:
                signals.append(f"원/달러 전년비 {chg:+.0f}% — 원화 강세, 수입물가 하방 압력")
    elif "dollar_idx" in sd:
        add(f"- 달러인덱스(광의) 12개월 추이: {trail('dollar_idx', 12)}")
    if "credit_spread" in sd:
        lab = "신용스프레드(회사채AA-−국고채3년)" if kr else "하이일드 스프레드"
        add(f"- {lab} 12개월 추이: {trail('credit_spread', 12)}")
        cs = last("credit_spread")
        thr = 1.5 if kr else 5.0
        if cs and cs[1] >= thr:
            signals.append(f"{lab} {cs[1]:.2f}%p — 경계 수준({thr}%p) 상회, 신용시장 스트레스")

    # ---------- 5. 통화량·명목GDP ----------
    add("\n[5. 통화량·명목GDP]")
    add(f"- M2 증가율 12개월 추이: {trail('m2_growth', 12)} (프리드먼 k% 기준선 4%)")
    add(f"- 본원통화 증가율 12개월 추이: {trail('base_growth', 12)}")
    mc, bg = last("mccallum"), last("base_growth")
    if mc and bg:
        add(f"- 맥컬럼 준칙: 필요 본원통화 증가율 {_fmt(mc[1])}% vs 실제 {_fmt(bg[1])}% (괴리 {bg[1]-mc[1]:+.1f}%p)")
    ng, nt = last("ngdp_growth"), last("ngdp_target")
    if ng and nt:
        add(f"- 명목GDP 증가율 8개 추이(분기 간격): {trail('ngdp_growth', 8, 3)} (목표 {_fmt(nt[1])}%)")
        if ng[1] - nt[1] >= 2:
            signals.append(f"명목GDP 증가율 {_fmt(ng[1])}%로 목표({_fmt(nt[1])}%)를 크게 상회")

    # ---------- 6. 통화정책 준칙 종합 ----------
    add("\n[6. 통화정책 준칙 대비 현재 위치]")
    for sid, note in [("taylor", "표준 테일러"), ("balanced", "균형접근(산출갭 가중 2배)"),
                      ("inertial", "관성형(점진 조정)"), ("forward_taylor", "전진형(1년 후 물가 기준)")]:
        v = last(sid)
        if v:
            add(f"- {note} 준칙 금리: {_fmt(v[1])}% (현 정책금리 {_fmt(cur_rate)}% 대비 {cur_rate - v[1]:+.2f}%p)")
    tay = dict(sd.get("taylor", []))
    pol = dict(sd.get("policy_rate", []))
    common = sorted(set(tay) & set(pol))
    if common:
        gap_tr = ", ".join(f"{m[:7]} {pol[m]-tay[m]:+.1f}" for m in common[-12:])
        add(f"- 테일러 갭(정책금리−준칙, %p) 12개월 추이: {gap_tr}")
        g = pol[common[-1]] - tay[common[-1]]
        if abs(g) >= 1:
            signals.append(f"정책금리가 테일러 준칙 대비 {abs(g):.1f}%p {'낮음(완화적)' if g < 0 else '높음(긴축적)'}")

    # ---------- 7. 회귀분석 (자체 계산) ----------
    add("\n[7. 회귀분석 결과 (이 대시보드가 실제 데이터로 직접 추정)]")
    if "output_gap" in sd and "unemp_gap" in sd:
        xs, ys = _pairs(sd["unemp_gap"], sd["output_gap"])
        res = ols(xs, ys)
        if res:
            b, a, r2, n = res
            add(f"- 오쿤 법칙: 산출갭 = {a} + ({b})×실업률갭, R²={r2}, 표본 {n}개(2000년~) → 오쿤 계수 {abs(b):.2f}")
    if core_id in sd and "unrate" in sd:
        xs_f, ys_f = _pairs(sd["unrate"], sd[core_id], "2000-01-01")
        xs_r, ys_r = _pairs(sd["unrate"], sd[core_id], f"{int(payload['updated'][:4]) - 10}-01-01")
        rf, rr2 = ols(xs_f, ys_f), ols(xs_r, ys_r)
        if rf and rr2:
            add(f"- 필립스 곡선 기울기: 2000년 이후 {rf[0]:+.3f} → 최근 10년 {rr2[0]:+.3f} "
                f"(절댓값이 클수록 실업률-물가 상충관계 뚜렷)")
            if abs(rr2[0]) < abs(rf[0]) * 0.5:
                signals.append("필립스 곡선이 최근 10년간 크게 평탄해짐 — 실업률을 낮춰도 물가 압력 제한적일 수 있음")
    if kr and "housing" in sd:
        h = sd["housing"]
        h_yoy = [[h[i][0], round((h[i][1] / h[i - 12][1] - 1) * 100, 2)] for i in range(12, len(h))]
        pol_l = dict(sd["policy_rate"])
        best_lag, best_c = 0, 0.0
        for lag in range(0, 25):
            xs, ys = [], []
            for d0, v in h_yoy:
                yy, mm = int(d0[:4]), int(d0[5:7])
                mm2 = mm - lag
                yy2 = yy + (mm2 - 1) // 12
                mm2 = (mm2 - 1) % 12 + 1
                pd = f"{yy2}-{mm2:02d}-01"
                if pd in pol_l:
                    xs.append(pol_l[pd])
                    ys.append(v)
            if len(xs) > 60:
                c = corr(xs, ys)
                if abs(c) > abs(best_c):
                    best_lag, best_c = lag, c
        add(f"- 기준금리→주택가격 시차상관: {best_lag}개월 시차에서 최대 (상관계수 {best_c:+.2f})")

    # ---------- 8. 자동 신호 ----------
    add("\n[8. 자동 탐지된 주요 신호]")
    L.extend(f"- {s}" for s in (signals or ["특이 신호 없음"]))

    meta = {
        "current_rate": cur_rate,
        "institution": "한국은행 금융통화위원회" if kr else "미국 연방공개시장위원회(FOMC)",
        "country_name": "한국" if kr else "미국",
        "unit_step": 0.25,
    }
    return "\n".join(L), meta


# ---------------------------------------------------------------- AI 리포트
def generate_report(country, payload):
    digest, meta = build_digest(country, payload)
    system = (f"당신은 {meta['country_name']} 거시경제 전문 애널리스트입니다. "
              "제공된 수치는 모두 공식 통계와 검증된 회귀분석 결과입니다. "
              "반드시 제공된 숫자만 인용하고 새로운 숫자를 만들어내지 마세요. "
              "모든 판단 문장에는 근거가 된 지표와 수치를 괄호로 함께 적으세요. "
              "경제 초심자도 이해할 수 있게 쉬운 한국어로 쓰되, 전문 용어에는 한 줄 설명을 붙이세요.")
    user = (f"아래는 {meta['country_name']} 거시경제 지표의 추이·백분위·회귀분석 자료입니다 "
            f"(데이터 기준일 {payload['updated']}).\n\n{digest}\n\n"
            "이를 바탕으로 마크다운 리포트를 작성하세요. 구성:\n"
            "## 한눈에 보는 총평 (4문장 이내)\n"
            "## 물가: 수준과 방향 (최신값뿐 아니라 12개월 추이와 모멘텀, 유가까지 반영)\n"
            "## 고용과 성장 (추이·갭·경기지표를 종합)\n"
            "## 금융·재정 여건 (시장금리, 장단기 금리차, 부채" + ("·주택시장" if country == "kr" else "·연준 SEP 전망과 현실의 괴리") + ")\n"
            "## 통화정책 평가 — 4개 금리준칙과 3개 통화량준칙 각각에 대해 현재 정책이 어긋난 정도를 표로 정리하고 종합 판단\n"
            "## 리스크 요인 (자동 탐지 신호를 하나씩 평가)\n"
            "## 향후 관전 포인트 3가지\n\n"
            "각 섹션은 반드시 ①수치 확인 → ②추세 해석 → ③판단 의 순서로 서술하고, "
            "서로 다른 지표가 상충할 때는 그 상충을 명시적으로 다루세요. 전체 1500~2000자.")
    md = nv_chat(system, user, max_tokens=4000, temperature=0.35)
    return {"markdown": md, "digest": digest, "model": NV_MODEL}


# ---------------------------------------------------------------- 위원회 시뮬레이션
PERSONAS = [
    ("hawk", "매파 위원",
     "물가 안정을 최우선으로 여기는 매파(hawk)입니다. 인플레이션 재발 위험을 크게 보고, "
     "금리를 높게 유지하거나 인상하는 쪽에 무게를 둡니다. 다만 데이터가 명확히 반대면 인정합니다."),
    ("neutral", "중립 위원",
     "데이터 의존적(data-dependent) 중도파입니다. 물가와 고용 양쪽 책무를 균형 있게 보고, "
     "준칙(테일러 준칙 등)과 실제 지표의 괴리를 중시합니다."),
    ("dove", "비둘기파 위원",
     "고용과 성장을 중시하는 비둘기파(dove)입니다. 긴축이 노동시장과 서민 경제에 주는 "
     "부담을 크게 보고, 금리 인하 또는 완화 유지에 무게를 둡니다. 다만 데이터가 명확히 반대면 인정합니다."),
]

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def _parse_vote(text, cur_rate):
    vote = {"decision_1m": "동결", "rate_1m": cur_rate,
            "rate_6m": cur_rate, "rate_1y": cur_rate}
    for m in reversed(_JSON_RE.findall(text)):
        try:
            d = json.loads(m)
            for k in vote:
                if k in d:
                    vote[k] = d[k]
            break
        except (ValueError, TypeError):
            continue
    for k in ("rate_1m", "rate_6m", "rate_1y"):
        try:
            vote[k] = round(float(vote[k]) * 4) / 4  # 0.25%p 단위 반올림
        except (ValueError, TypeError):
            vote[k] = cur_rate
    if vote["decision_1m"] not in ("인상", "인하", "동결"):
        vote["decision_1m"] = "동결"
    return vote


def _run_all(tasks):
    """[(key, fn)] 순차 실행 → {key: 결과}
    (NVIDIA 무료 티어는 동시 호출 제한이 있어 병렬로 보내면 빈 응답이 온다)"""
    results = {}
    for i, (key, fn) in enumerate(tasks):
        if i:
            time.sleep(3)
        try:
            results[key] = fn()
        except Exception as e:
            raise RuntimeError(f"AI 호출 실패 ({key}): {e}")
    return results


def run_fomc(country, payload):
    digest, meta = build_digest(country, payload)
    inst, cur = meta["institution"], meta["current_rate"]
    ctx = (f"당신은 {inst} 정책 토론 시뮬레이션의 참가자입니다. "
           f"현재 정책금리는 {cur}%이며, 금리 조정은 0.25%p 단위입니다. "
           "아래 자료의 숫자만 인용하고 새 숫자를 지어내지 마세요. 쉬운 한국어로 말하세요. "
           "최신값 하나만 보지 말고 추이(방향·속도)와 역사적 백분위, 지표 간 상충까지 함께 판단하세요.\n\n"
           f"[경제 지표 분석 자료 (기준일 {payload['updated']})]\n{digest}")

    # 1라운드: 모두발언 (순차)
    def opening(p):
        return nv_chat(
            ctx + f"\n\n당신의 성향: {p[2]}",
            f"{p[1]}으로서 모두발언을 하세요. 요구사항:\n"
            "1) 물가·고용·성장·금융 네 영역을 각각 최소 1문장씩 평가하고, 영역마다 지표의 '추이'를 "
            "구체적 수치로 인용할 것 (총 6개 이상의 지표 사용)\n"
            "2) 자신의 성향에서 가장 중요하게 보는 지표가 무엇이고 왜인지 밝힐 것\n"
            "3) 잠정적인 정책 방향(인상/동결/인하)과 그 핵심 근거 한 가지로 마무리\n"
            "8문장 이내.",
            max_tokens=1800, temperature=0.6)

    r1 = _run_all([(p[0], (lambda p=p: opening(p))) for p in PERSONAS])

    def others(key):
        return "\n\n".join(f"◆ {p[1]}의 모두발언:\n{r1[p[0]]}" for p in PERSONAS if p[0] != key)

    # 2라운드: 반론 + 최종 표결 (순차)
    def round2(p):
        return nv_chat(
            ctx + f"\n\n당신의 성향: {p[2]}",
            f"다른 위원들의 모두발언입니다:\n\n{others(p[0])}\n\n"
            f"{p[1]}으로서 최종 발언을 하세요. 반드시 아래 구조를 따르세요:\n"
            "【반론과 동의】 다른 위원 주장 중 동의하는 것 1가지와 반박하는 것 1가지를 각각 "
            "구체적 수치를 들어 설명 (상대가 놓친 지표나 추이를 지적할 것)\n"
            "【영역별 판단】 물가 / 노동시장 / 성장·경기 / 금융·재정 각각에 대해 1~2문장씩: "
            "현재 수준과 추이를 보고 어느 방향의 압력인지 판단\n"
            "【트레이드오프】 지표들이 상충하는 지점이 무엇이고, 나는 무엇을 우선했는지 명시\n"
            "【금리 경로】 1개월 후(다음 회의) / 6개월 후 / 1년 후 각각의 적정 금리를 제시하고, "
            "각 시점의 숫자를 그렇게 정한 이유를 시점별로 설명\n"
            "마지막 줄에 반드시 아래 형식의 JSON 한 줄만 추가:\n"
            '{"decision_1m": "인상|동결|인하", "rate_1m": 숫자, "rate_6m": 숫자, "rate_1y": 숫자}',
            max_tokens=2500, temperature=0.5)

    r2 = _run_all([(p[0], (lambda p=p: round2(p))) for p in PERSONAS])

    members, votes = [], []
    for p in PERSONAS:
        vote = _parse_vote(r2[p[0]], cur)
        votes.append(vote)
        members.append({
            "stance": p[0], "title": p[1],
            "opening": r1[p[0]],
            "final": _JSON_RE.sub("", r2[p[0]]).strip(),
            "vote": vote,
        })

    # 다수결 (1:1:1이면 절충안 = 동결)
    counts = {}
    for v in votes:
        counts[v["decision_1m"]] = counts.get(v["decision_1m"], 0) + 1
    decision = max(counts, key=counts.get)
    if counts.get(decision, 0) == 1:
        decision = "동결"

    # 의장 성명
    chair = nv_chat(
        ctx,
        "당신은 의장입니다. 세 위원의 최종 발언은 다음과 같습니다:\n\n"
        + "\n\n".join(f"◆ {m['title']}: {m['final']}\n(표결: 1개월 {m['vote']['decision_1m']} "
                      f"{m['vote']['rate_1m']}%, 6개월 {m['vote']['rate_6m']}%, 1년 {m['vote']['rate_1y']}%)"
                      for m in members)
        + f"\n\n표결 결과 이번 회의의 결정은 '{decision}'입니다. 의장 성명을 발표하세요:\n"
          "1) 토론에서 위원들이 맞붙은 핵심 쟁점 2가지를 수치와 함께 요약\n"
          "2) 결정의 근거 (다수 의견이 중시한 지표)\n"
          "3) 소수 의견이 지적한 리스크도 1문장으로 언급\n"
          "4) 향후 어떤 지표가 어떻게 움직이면 정책을 바꿀지 조건부 가이던스\n"
          "8문장 이내.",
        max_tokens=1500, temperature=0.4)

    return {
        "institution": inst, "current_rate": cur,
        "decision": decision, "counts": counts,
        "members": members, "chair": chair,
        "digest": digest, "model": NV_MODEL,
    }
