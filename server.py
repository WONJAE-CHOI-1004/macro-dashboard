# -*- coding: utf-8 -*-
"""
거시경제 지표 대시보드 서버
- FRED(미국) / ECOS(한국) API에서 원자료를 받아
- 통화정책 준칙(테일러 등)과 산출갭 지표를 계산한 뒤
- 웹 화면(web/ 폴더)에 JSON으로 전달한다.
실행:  py server.py --open
"""
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(BASE, "web")
CACHE_DIR = os.path.join(BASE, "cache")
REPORT_DIR = os.path.join(BASE, "reports")
CACHE_TTL = 6 * 3600  # 6시간마다 새 데이터
PORT = 8135

sys.path.insert(0, BASE)
import analysis  # noqa: E402 (AI 리포트·위원회 시뮬레이션)

# ---------------------------------------------------------------- .env 로드
# 로컬은 .env 파일, GitHub Actions 등 서버 환경은 환경변수에서 키를 읽는다.
ENV = {}
try:
    with open(os.path.join(BASE, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip()
except FileNotFoundError:
    pass

FRED_KEY = ENV.get("FRED_API_KEY") or os.environ.get("FRED_API_KEY", "")
ECOS_KEY = ENV.get("ECOS_API_KEY") or os.environ.get("ECOS_API_KEY", "")
KOSIS_KEY = ENV.get("KOSIS_API_KEY") or os.environ.get("KOSIS_API_KEY", "")


def http_json(url, timeout=60, tries=3):
    """JSON GET (일시적 네트워크 오류·응답 지연에 대비해 최대 3회 재시도)"""
    last_err = None
    for i in range(tries):
        if i:
            time.sleep(8 * i)  # 8초, 16초 대기 후 재시도
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = e
    raise last_err


# ---------------------------------------------------------------- 원자료 수집
def fred(series_id, units=None, start="1985-01-01", freq=None):
    """FRED 시계열 → [[YYYY-MM-DD, float], ...] (freq='m'이면 일별을 월평균으로 집계)"""
    params = {
        "series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
        "observation_start": start,
    }
    if units:
        params["units"] = units
    if freq:
        params["frequency"] = freq
        params["aggregation_method"] = "avg"
    url = "https://api.stlouisfed.org/fred/series/observations?" + urllib.parse.urlencode(params)
    d = http_json(url)
    out = []
    for o in d["observations"]:
        if o["value"] not in (".", ""):
            out.append([o["date"], float(o["value"])])
    return out


def _ecos_date(t, cycle):
    if cycle == "D":
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    if cycle == "M":
        return f"{t[:4]}-{t[4:6]}-01"
    if cycle == "Q":
        q = int(t[5])
        return f"{t[:4]}-{(q - 1) * 3 + 1:02d}-01"
    return f"{t}-01-01"


def ecos(stat, cycle, start, end, item):
    """ECOS 시계열 → [[YYYY-MM-DD, float], ...]"""
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/10000/"
           f"{stat}/{cycle}/{start}/{end}/{item}")
    d = http_json(url)
    if "StatisticSearch" not in d:
        raise RuntimeError(f"ECOS 오류 {stat}/{item}: {json.dumps(d, ensure_ascii=False)[:200]}")
    out = []
    for r in d["StatisticSearch"]["row"]:
        v = r.get("DATA_VALUE")
        if v not in (None, ""):
            out.append([_ecos_date(r["TIME"], cycle), float(v)])
    out.sort(key=lambda x: x[0])
    return out


def kosis(org_id, tbl_id, itm_id, obj_l1, prd_se="A", start="1997", end="2030"):
    """KOSIS 시계열 → [[YYYY-MM-DD, float], ...] (연간 A / 월간 M)"""
    params = {
        "method": "getList", "apiKey": KOSIS_KEY, "format": "json", "jsonVD": "Y",
        "orgId": org_id, "tblId": tbl_id, "itmId": itm_id, "objL1": obj_l1,
        "prdSe": prd_se, "startPrdDe": start, "endPrdDe": end,
    }
    url = "https://kosis.kr/openapi/Param/statisticsParameterData.do?" + urllib.parse.urlencode(params)
    d = http_json(url)
    if isinstance(d, dict):
        raise RuntimeError(f"KOSIS 오류 {tbl_id}: {json.dumps(d, ensure_ascii=False)[:200]}")
    out = []
    for r in d:
        v = r.get("DT")
        t = r.get("PRD_DE", "")
        if v in (None, "", "-"):
            continue
        date = f"{t}-01-01" if prd_se == "A" else f"{t[:4]}-{t[4:6]}-01"
        out.append([date, float(v)])
    out.sort(key=lambda x: x[0])
    return out


def imf(indicator, country):
    """IMF DataMapper(WEO) → [[YYYY-01-01, float], ...] — 2031년까지 전망 포함.
    주의: User-Agent 헤더를 보내면 403이 나므로 http_json을 쓰지 않는다."""
    url = f"https://www.imf.org/external/datamapper/api/v1/{indicator}/{country}"
    last_err = None
    for i in range(3):
        if i:
            time.sleep(8 * i)
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
                d = json.loads(r.read().decode("utf-8"))
            vals = d["values"][indicator].get(country, {})
            return [[f"{y}-01-01", float(v)] for y, v in sorted(vals.items())]
        except Exception as e:
            last_err = e
    raise last_err


def wb(indicator, country, start=1980):
    """World Bank → [[YYYY-01-01, float], ...] (연간)"""
    url = (f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
           f"?format=json&per_page=200&date={start}:2035")
    d = http_json(url)
    rows = d[1] if len(d) > 1 and d[1] else []
    out = [[f"{r['date']}-01-01", float(r["value"])] for r in rows if r["value"] is not None]
    out.sort(key=lambda x: x[0])
    return out


def intl_series(imf_cty, wb_cty=None):
    """IMF·World Bank 공통 시리즈 (섹터 5). imf_cty: IMF 코드(USA/KOR/JPN/EUQ),
    wb_cty: World Bank 코드(기본은 IMF와 동일, 유로존은 EMU)"""
    cty = imf_cty
    wb_cty = wb_cty or imf_cty
    return [
        S("imf_gdp_fc", "IMF 전망: 실질GDP 성장률", 5, imf("NGDP_RPCH", cty), dash=True,
          desc="IMF 세계경제전망(WEO). 2031년까지의 전망치가 점선으로 이어집니다"),
        S("imf_infl_fc", "IMF 전망: 인플레이션 (연평균)", 5, imf("PCPIPCH", cty), dash=True,
          desc="IMF WEO 소비자물가 연평균 상승률. 미래 구간은 전망치"),
        S("imf_unemp_fc", "IMF 전망: 실업률", 5, imf("LUR", cty), dash=True,
          desc="IMF WEO 실업률. 미래 구간은 전망치"),
        S("imf_govdebt", "정부부채/GDP (IMF, 전망 포함)", 5, imf("GGXWDG_NGDP", cty),
          desc="일반정부 총부채 ÷ GDP. 2031년까지의 IMF 전망 경로 포함"),
        S("imf_ca", "경상수지/GDP (IMF)", 5, imf("BCA_NGDPD", cty),
          desc="경상수지 흑자(+)/적자(−) 비율. 대외 건전성 지표"),
        S("imf_fiscal", "재정수지/GDP (IMF)", 5, imf("GGXCNL_NGDP", cty),
          desc="일반정부 재정 흑자(+)/적자(−) 비율"),
        S("wb_trade", "무역의존도 (수출입/GDP)", 5, wb("NE.TRD.GNFS.ZS", wb_cty),
          desc="World Bank. 수출+수입 ÷ GDP — 대외 충격 민감도"),
        S("wb_buffett", "버핏지표 (시가총액/GDP)", 5, wb("CM.MKT.LCAP.GD.ZS", wb_cty),
          desc="World Bank. 100% 이상이면 고평가 논쟁 구간, 150% 이상은 과열 신호로 통용"),
        S("wb_gdppc", "1인당 GDP", 5, wb("NY.GDP.PCAP.CD", wb_cty), axis="level", unit="달러",
          desc="World Bank, 명목 달러 기준"),
        S("wb_old", "65세 이상 인구 비중", 5, wb("SP.POP.65UP.TO.ZS", wb_cty),
          desc="World Bank. 고령화 속도 — 잠재성장률과 자연이자율(r*)을 낮추는 구조 요인"),
    ]


def previous_series(country, ids):
    """로컬 캐시 → 공개 사이트 순으로 이전 데이터에서 특정 시리즈를 복구
    (KOSIS처럼 CI 환경에서 간헐적으로 접속이 막히는 출처의 안전장치)"""
    payload = None
    try:
        with open(os.path.join(CACHE_DIR, f"{country}.json"), encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in repo:
            owner, name = repo.split("/", 1)
            try:
                payload = http_json(f"https://{owner.lower()}.github.io/{name}/data_{country}.json")
            except Exception:
                payload = None
    by_id = {s["id"]: s["data"] for s in (payload or {}).get("series", [])}
    return [by_id.get(sid, []) for sid in ids]


# ---------------------------------------------------------------- 시계열 유틸
def yoy(series, periods):
    """전년동기대비 % (periods = 12(월) 또는 4(분기))"""
    out = []
    for i in range(periods, len(series)):
        prev = series[i - periods][1]
        if prev:
            out.append([series[i][0], round((series[i][1] / prev - 1) * 100, 3)])
    return out


def monthly_last(daily):
    """일별 → 월별(각 달 마지막 값), 날짜는 그 달 1일로 표기"""
    by_month = {}
    for d, v in daily:
        by_month[d[:7]] = v
    return [[m + "-01", v] for m, v in sorted(by_month.items())]


def ffill_to(dates, series):
    """series(드문 주기)를 dates(촘촘한 주기)에 직전 값으로 채워 매핑"""
    out, j, last = [], 0, None
    for d in dates:
        while j < len(series) and series[j][0] <= d:
            last = series[j][1]
            j += 1
        out.append(last)
    return out


def hp_filter(values, lam):
    """HP 필터 추세 (순수 파이썬 밴드 LDL 분해)"""
    n = len(values)
    if n < 8:
        return list(values)
    a0 = [0.0] * n
    a1 = [0.0] * (n - 1)
    a2 = [lam] * (n - 2)
    for i in range(n):
        if i in (0, n - 1):
            c = 1.0
        elif i in (1, n - 2):
            c = 5.0
        else:
            c = 6.0
        a0[i] = 1.0 + lam * c
    for i in range(n - 1):
        a1[i] = -2.0 * lam if i in (0, n - 2) else -4.0 * lam
    # LDL^T 분해 (대역폭 2)
    d = [0.0] * n
    l1 = [0.0] * n
    l2 = [0.0] * n
    for i in range(n):
        if i >= 2:
            l2[i] = a2[i - 2] / d[i - 2]
        if i >= 1:
            t = a1[i - 1]
            if i >= 2:
                t -= l2[i] * d[i - 2] * l1[i - 1]
            l1[i] = t / d[i - 1]
        di = a0[i]
        if i >= 1:
            di -= l1[i] ** 2 * d[i - 1]
        if i >= 2:
            di -= l2[i] ** 2 * d[i - 2]
        d[i] = di
    # 전진/후진 대입
    y = [0.0] * n
    for i in range(n):
        y[i] = values[i]
        if i >= 1:
            y[i] -= l1[i] * y[i - 1]
        if i >= 2:
            y[i] -= l2[i] * y[i - 2]
    z = [y[i] / d[i] for i in range(n)]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = z[i]
        if i + 1 < n:
            x[i] -= l1[i + 1] * x[i + 1]
        if i + 2 < n:
            x[i] -= l2[i + 2] * x[i + 2]
    return x


def S(sid, name, section, data, axis="pct", unit="%", dash=False, desc="", norm=True):
    """norm=False: 오른쪽 축에 지수화 없이 원값 그대로 표시 (음수가 나올 수 있는 증감 지표용)"""
    return {"id": sid, "name": name, "section": section, "axis": axis,
            "unit": unit, "dash": dash, "desc": desc, "norm": norm, "data": data}


# ---------------------------------------------------------------- 준칙 계산
def policy_rules(months, pi, gap_m, policy, prefix_desc, pi_name, rstar=2.0, pistar=2.0):
    """테일러 계열 준칙들. months: 날짜리스트, pi/gap_m/policy: 같은 길이 값 리스트(None 허용)"""
    taylor, balanced, inertial, real_rate = [], [], [], []
    prev_inertial = None
    taylor_vals = {}
    for i, m in enumerate(months):
        p, g = pi[i], gap_m[i]
        if p is None:
            continue
        real_rate.append([m, round((policy[i] - p), 2)] if policy[i] is not None else None)
        if g is None:
            continue
        t = rstar + p + 0.5 * (p - pistar) + 0.5 * g
        b = rstar + p + 0.5 * (p - pistar) + 1.0 * g
        taylor.append([m, round(t, 2)])
        balanced.append([m, round(b, 2)])
        taylor_vals[m] = t
        if prev_inertial is None:
            prev_inertial = policy[i] if policy[i] is not None else t
        prev_inertial = 0.85 * prev_inertial + 0.15 * t
        inertial.append([m, round(prev_inertial, 2)])
    real_rate = [x for x in real_rate if x]
    # 전진형(포워드): 12개월 뒤 실제 인플레이션을 기대치로 사용
    forward = []
    for i, m in enumerate(months):
        if i + 12 < len(months) and pi[i + 12] is not None and gap_m[i] is not None:
            p, g = pi[i + 12], gap_m[i]
            forward.append([m, round(rstar + p + 0.5 * (p - pistar) + 0.5 * g, 2)])
    return [
        S("taylor", "테일러 준칙 금리", 2, taylor,
          desc=f"r*+π+0.5(π−2%)+0.5×산출갭. π={pi_name}, r*=2% 가정. {prefix_desc}"),
        S("balanced", "균형접근 준칙 금리", 2, balanced,
          desc="테일러 준칙과 같지만 산출갭 가중치가 1.0 (옐런 연준이 참고)"),
        S("forward_taylor", "전진형 테일러 준칙", 2, forward,
          desc="12개월 뒤 실제 인플레이션을 '기대 인플레이션'으로 사용한 테일러 준칙"),
        S("inertial", "관성형 준칙 금리", 2, inertial,
          desc="전월 금리 85% + 테일러 준칙 15%로 서서히 조정 (금리 스무딩)"),
        S("real_rate", "실질 정책금리 (테일러 원칙 체크)", 2, real_rate,
          desc="정책금리 − 인플레이션. 0% 위에 있어야 긴축적 = 테일러 원칙 충족 판단에 사용"),
    ]


def mccallum_ngdp_friedman(months, ngdp_yoy_m, base_yoy_m, m2_yoy, xstar, k_pct):
    """맥컬럼 준칙 / 명목GDP 목표 / 프리드먼 k% (모두 증가율 % 기준)"""
    mcc = []
    hist = []
    for i, m in enumerate(months):
        ng, bg = ngdp_yoy_m[i], base_yoy_m[i]
        if ng is None or bg is None:
            continue
        hist.append(ng - bg)  # 유통속도 증가율 근사
        vavg = sum(hist[-16:]) / len(hist[-16:])
        mcc.append([m, round(xstar - vavg + 0.5 * (xstar - ng), 2)])
    base_g = [[months[i], round(base_yoy_m[i], 2)] for i in range(len(months)) if base_yoy_m[i] is not None]
    ngdp_g = [[months[i], round(ngdp_yoy_m[i], 2)] for i in range(len(months)) if ngdp_yoy_m[i] is not None]
    const_k = [[m, k_pct] for m, v in ngdp_g]
    const_x = [[m, xstar] for m, v in ngdp_g]
    return [
        S("mccallum", "맥컬럼 준칙 본원통화 증가율", 2, mcc,
          desc=f"명목GDP 목표성장률 {xstar}% 달성에 필요한 본원통화 증가율 (실제 증가율과 비교)"),
        S("base_growth", "본원통화 증가율 (실제)", 2, base_g,
          desc="실제 본원통화 전년동기대비 증가율. 맥컬럼 준칙과 겹쳐 보세요"),
        S("ngdp_growth", "명목GDP 증가율", 2, ngdp_g,
          desc="명목GDP 목표제: 이 선이 목표선에 붙도록 통화정책을 운용하자는 아이디어"),
        S("ngdp_target", f"명목GDP 목표선 ({xstar}%)", 2, const_x, dash=True,
          desc="명목GDP 목표제의 기준선"),
        S("m2_growth", "M2 증가율 (실제)", 2, m2_yoy,
          desc="프리드먼 k% 준칙: M2를 매년 일정 비율만 늘리자는 주장. 실제 증가율"),
        S("friedman_k", f"프리드먼 k% 기준선 ({k_pct}%)", 2, const_k, dash=True,
          desc="실질성장률+목표물가상승률 수준의 고정 증가율 기준선"),
    ]


# ---------------------------------------------------------------- 미국 데이터
def build_us():
    core = fred("PCEPILFE", "pc1")          # 핵심 PCE YoY
    head = fred("PCEPI", "pc1")
    cpi = fred("CPIAUCSL", "pc1")
    unrate = fred("UNRATE")
    gdp_g = fred("A191RL1Q225SBEA")          # 실질GDP 전기비연율
    ffr = fred("FEDFUNDS")
    debt = [[d, round(v / 1e6, 3)] for d, v in fred("GFDEBTN")]   # 백만$ → 조$
    gs10 = fred("GS10")
    gs2 = fred("GS2")
    pay = fred("PAYEMS", "chg")
    wti = fred("MCOILWTICO")
    brent = fred("MCOILBRENTEU")
    gdpc1 = fred("GDPC1")
    gdppot = fred("GDPPOT")
    nrou = fred("NROU")
    gdp_n_yoy = fred("GDP", "pc1")
    base_yoy = fred("BOGMBASE", "pc1")
    m2_yoy = fred("M2SL", "pc1")
    # --- 금융·부채 (섹터 4) ---
    hh_debt = [[d, round(v / 1e6, 2)] for d, v in fred("CMDEBT")]        # 조$
    hh_debt_gdp = fred("HDTGPDUSQ163N")
    dsr = fred("TDSP")
    mktcap_raw = fred("BOGZ1LM893064105Q")                                # 백만$
    mktcap = [[d, round(v / 1e6, 2)] for d, v in mktcap_raw]              # 조$
    frn = fred("BOGZ1FL263064003Q")
    mk = dict(mktcap_raw)
    foreign_share = [[d, round(v / mk[d] * 100, 1)] for d, v in frn if mk.get(d)]
    sp500 = fred("SP500", freq="m")
    dollar_idx = fred("DTWEXBGS", freq="m")
    t10yie = fred("T10YIE", freq="m")
    hy_spread = fred("BAMLH0A0HYM2", freq="m")
    # SEP (연준 전망, 연간)
    sep = {
        "sep_ffr": ("SEP 전망: 연방기금금리 (점도표 중간값)", fred("FEDTARMD", start="2015-01-01")),
        "sep_pce": ("SEP 전망: PCE 인플레이션", fred("PCECTPICTM", start="2015-01-01")),
        "sep_core": ("SEP 전망: 핵심 PCE", fred("JCXFECTM", start="2015-01-01")),
        "sep_unrate": ("SEP 전망: 실업률", fred("UNRATECTM", start="2015-01-01")),
        "sep_gdp": ("SEP 전망: 실질GDP 성장률", fred("GDPC1CTM", start="2015-01-01")),
    }
    # 산출갭 = (실질GDP − 잠재GDP)/잠재GDP
    pot = dict(gdppot)
    gap_q = [[d, round((v / pot[d] - 1) * 100, 2)] for d, v in gdpc1 if d in pot]
    # 실업률 갭 = 실업률 − NAIRU
    months = [d for d, _ in core]
    nairu_m = ffill_to(months, nrou)
    un_map = dict(unrate)
    unemp_gap = [[m, round(un_map[m] - nairu_m[i], 2)] for i, m in enumerate(months)
                 if m in un_map and nairu_m[i] is not None]
    okun_pred = [[m, round(-2.0 * v, 2)] for m, v in unemp_gap]

    series = [
        S("core_pce", "핵심 PCE 인플레이션", 1, core, desc="연준이 가장 중시하는 물가지표 (전년동월대비)"),
        S("head_pce", "헤드라인 PCE 인플레이션", 1, head, desc="식품·에너지 포함 PCE (전년동월대비)"),
        S("cpi", "CPI 인플레이션", 1, cpi, desc="소비자물가지수 (전년동월대비)"),
        S("unrate", "실업률", 1, unrate, desc="U-3 실업률"),
        S("gdp_growth", "실질GDP 성장률", 1, gdp_g, desc="전기비 연율 (분기)"),
        S("policy_rate", "연방기금금리 (실제)", 1, ffr, desc="실효 연방기금금리"),
        S("gs10", "국채 10년물 금리", 1, gs10, desc="10년 만기 미 국채 수익률"),
        S("gs2", "국채 2년물 금리", 1, gs2, desc="2년 만기 미 국채 수익률"),
        S("debt", "연방정부 부채 총액", 1, debt, axis="level", unit="조 달러", desc="Total Public Debt"),
        S("payrolls", "비농업 고용 증감", 1, pay, axis="level", unit="천 명", norm=False,
          desc="월간 신규고용 (천 명, 원값)"),
        S("wti", "WTI 유가", 1, wti, axis="level", unit="$/배럴", desc="서부텍사스산 원유 월평균"),
        S("brent", "브렌트 유가", 1, brent, axis="level", unit="$/배럴", desc="브렌트유 월평균"),
    ]
    for sid, (name, data) in sep.items():
        series.append(S(sid, name, 1, data, dash=True,
                        desc="FOMC 위원들의 연말 전망 중간값 — 미래 구간이 점선으로 표시됩니다"))

    pi = [dict(core).get(m) for m in months]
    gap_m = ffill_to(months, gap_q)
    pol = [dict(ffr).get(m) for m in months]
    series += policy_rules(months, pi, gap_m, pol, "실제 연방기금금리와 겹쳐 보세요.", "핵심 PCE")
    ngdp_m = ffill_to(months, gdp_n_yoy)
    base_m = [dict(base_yoy).get(m) for m in months]
    series += mccallum_ngdp_friedman(months, ngdp_m, base_m, m2_yoy, 5.0, 4.0)
    series += [
        S("exp_inflation", "기대인플레이션 (10년 BEI)", 4, t10yie,
          desc="10년 국채와 물가연동국채의 금리차 = 시장이 보는 향후 10년 평균 기대 인플레이션"),
        S("hh_debt", "가계부채 총액", 4, hh_debt, axis="level", unit="조 달러",
          desc="가계·비영리단체 부채 잔액 (연준 자금순환표, 분기)"),
        S("hh_debt_gdp", "GDP 대비 가계부채 비율", 4, hh_debt_gdp,
          desc="BIS 기준 가계부채/GDP. 금리 인상의 가계 충격을 가늠하는 지표"),
        S("dsr", "가계 원리금 상환 부담률 (DSR)", 4, dsr,
          desc="가처분소득 대비 원리금 상환액 비중. 금리가 가계를 얼마나 조이는지 표시"),
        S("mktcap", "주식 시가총액 (전체)", 4, mktcap, axis="level", unit="조 달러",
          desc="미국 전체 상장주식 시가총액 (연준 자금순환표, 분기)"),
        S("foreign_share", "외국인 보유 비중 (추정)", 4, foreign_share,
          desc="해외 부문이 보유한 미국 주식 ÷ 전체 시가총액 (자금순환표 기준 추정치)"),
        S("stock_idx", "S&P 500 지수", 4, sp500, axis="level", unit="",
          desc="월평균. FRED 제공 범위 제한으로 최근 10년만 표시됩니다"),
        S("dollar_idx", "달러인덱스 (광의)", 4, dollar_idx, axis="level", unit="",
          desc="주요 교역상대국 통화 대비 달러 가치 (2006.1=100). 오르면 달러 강세"),
        S("credit_spread", "하이일드 스프레드", 4, hy_spread,
          desc="투기등급 회사채와 국채의 금리차. 5%를 넘으면 금융시장 스트레스 신호"),
        S("output_gap", "산출갭", 3, gap_q, desc="(실질GDP−잠재GDP)/잠재GDP. CBO 잠재GDP 기준"),
        S("nairu", "자연실업률 (NAIRU)", 3, nrou, desc="CBO 추정 비순환적 실업률"),
        S("unemp_gap", "실업률 갭", 3, unemp_gap, desc="실업률 − NAIRU. 마이너스면 과열 노동시장"),
        S("okun", "오쿤 법칙 예측 산출갭", 3, okun_pred,
          desc="산출갭 ≈ −2×실업률갭 (오쿤 계수 2 가정). 실제 산출갭과 겹쳐 보세요"),
    ]
    series += intl_series("USA")
    phillips = {"x": "실업률(%)", "y": "핵심 PCE 인플레이션(%)",
                "points": [[dict(unrate).get(m), v, m] for m, v in core if dict(unrate).get(m) is not None]}
    return {"series": series, "phillips": phillips}


# ---------------------------------------------------------------- 한국 데이터
def build_kr():
    end_m, end_q, end_d = "202612", "2026Q4", "20261231"
    cpi_idx = ecos("901Y009", "M", "198501", end_m, "0")
    core_idx = ecos("901Y010", "M", "198501", end_m, "QB")
    unrate = ecos("902Y021", "M", "199001", end_m, "KOR")     # 계절조정 실업률
    rgdp = ecos("200Y108", "Q", "1985Q1", end_q, "10601")     # 실질GDP (계절조정)
    ngdp = ecos("200Y107", "Q", "1985Q1", end_q, "10601")     # 명목GDP (계절조정)
    base_rate_d = ecos("722Y001", "D", "19990501", end_d, "0101000")
    ktb3 = ecos("721Y001", "M", "199505", end_m, "5020000")
    ktb10 = ecos("721Y001", "M", "200010", end_m, "5050000")
    m2 = ecos("161Y006", "M", "200310", end_m, "BBHA00")
    mbase = ecos("102Y002", "M", "200310", end_m, "ABA1")
    wti = fred("MCOILWTICO")
    brent = fred("MCOILBRENTEU")
    leading = ecos("901Y067", "M", "197001", end_m, "I16E")    # 선행지수 순환변동치
    coincident = ecos("901Y067", "M", "197001", end_m, "I16D")  # 동행지수 순환변동치
    indprod = ecos("901Y033", "M", "200001", end_m, "A00/2")   # 전산업생산지수(계절조정)
    housing = ecos("901Y062", "M", "198601", end_m, "P63A")    # 주택매매가격지수(KB)
    employed = ecos("901Y027", "M", "199906", end_m, "I61BA/I28A")  # 취업자 수(천 명, 원계열)
    try:
        debt = kosis("184", "DT_102006_001", "T001", "A01")        # 국가채무 (조원, 연간)
        debt_ratio = kosis("184", "DT_102006_001", "T001", "A02")  # GDP 대비 국가채무 (%)
    except Exception as e:
        # KOSIS는 CI 환경에서 간헐적으로 접속 차단 → 이전 데이터 유지 (연간 통계라 무해)
        print(f"KOSIS 실패({e}) → 이전 국가채무 데이터 재사용", flush=True)
        debt, debt_ratio = previous_series("kr", ["debt", "debt_ratio"])
    # --- 금융·부채 (섹터 4) ---
    hh_debt_raw = ecos("151Y001", "Q", "2002Q4", end_q, "1000000")   # 가계신용 (십억원)
    kospi = ecos("901Y014", "M", "199501", end_m, "1070000")
    mktcap_kospi = ecos("901Y014", "M", "199501", end_m, "1040000")  # 천원
    mktcap_kosdaq = ecos("901Y014", "M", "200101", end_m, "2040000")
    usdkrw = ecos("731Y004", "M", "199001", end_m, "0000001/0000100")  # 원/달러 (월평균)
    expinf = ecos("511Y003", "M", "200807", end_m, "FMB")            # 향후1년 기대인플레이션
    corp3 = ecos("721Y001", "M", "199505", end_m, "7020000")         # 회사채(3년, AA-)
    iip_equity = ecos("311Y001", "Q", "2002Q4", end_q, "2020100")    # 외국인 보유 지분증권 (백만$)

    cpi = yoy(cpi_idx, 12)
    core = yoy(core_idx, 12)
    gdp_g = yoy(rgdp, 4)
    ngdp_yoy = yoy(ngdp, 4)
    m2_yoy = yoy(m2, 12)
    base_yoy = yoy(mbase, 12)
    base_rate = monthly_last(base_rate_d)
    # 취업자 증감(전년동월대비, 만 명)
    emp_chg = [[employed[i][0], round((employed[i][1] - employed[i - 12][1]) / 10, 1)]
               for i in range(12, len(employed))]
    # 가계부채 (조원) + GDP 대비 비율(가계신용 ÷ 최근 4분기 명목GDP 합)
    hh_debt = [[d, round(v / 1000, 1)] for d, v in hh_debt_raw]
    ngdp4 = {ngdp[i][0]: sum(x[1] for x in ngdp[i - 3:i + 1]) for i in range(3, len(ngdp))}
    hh_debt_gdp = [[d, round(v / ngdp4[d] * 100, 1)] for d, v in hh_debt_raw if ngdp4.get(d)]
    # 시가총액 (천원 → 조원)
    mktcap = [[d, round(v / 1e9, 0)] for d, v in mktcap_kospi]
    # 신용스프레드 = 회사채(3년, AA-) − 국고채(3년)
    k3 = dict(ktb3)
    credit_spread = [[d, round(v - k3[d], 2)] for d, v in corp3 if d in k3]
    # 외국인 보유 비중(추정) = IIP 지분증권 부채잔액(백만$)×환율 ÷ (KOSPI+KOSDAQ 시가총액)
    fx = dict(usdkrw)
    kosdaq_map = dict(mktcap_kosdaq)
    mk_all = {d: v + kosdaq_map[d] for d, v in mktcap_kospi if d in kosdaq_map}  # 천원
    foreign_share = []
    for d, v in iip_equity:
        yy, mm = int(d[:4]), int(d[5:7])
        md = f"{yy}-{mm + 2:02d}-01"  # 분기 마지막 달 기준
        if fx.get(md) and mk_all.get(md):
            foreign_share.append([d, round(v * 1e6 * fx[md] / (mk_all[md] * 1000) * 100, 1)])

    # 산출갭: 실질GDP 로그에 HP필터(λ=1600) 적용
    logs = [math.log(v) * 100 for _, v in rgdp]
    trend = hp_filter(logs, 1600.0)
    gap_q = [[rgdp[i][0], round(logs[i] - trend[i], 2)] for i in range(len(rgdp))]
    # NAIRU 근사: 실업률 HP필터(λ=129600) 추세
    u_vals = [v for _, v in unrate]
    u_trend = hp_filter(u_vals, 129600.0)
    nairu = [[unrate[i][0], round(u_trend[i], 2)] for i in range(len(unrate))]
    unemp_gap = [[unrate[i][0], round(u_vals[i] - u_trend[i], 2)] for i in range(len(unrate))]
    okun_pred = [[m, round(-2.0 * v, 2)] for m, v in unemp_gap]

    series = [
        S("core_cpi", "근원 CPI 인플레이션 (농산물·석유류 제외)", 1, core,
          desc="한국은행이 참고하는 근원물가. 한국은 PCE가 없어 CPI 계열을 사용합니다"),
        S("cpi", "CPI 인플레이션", 1, cpi, desc="소비자물가 전년동월대비. 한은 목표 2%"),
        S("unrate", "실업률 (계절조정)", 1, unrate, desc="계절변동조정 실업률"),
        S("gdp_growth", "실질GDP 성장률", 1, gdp_g, desc="전년동기대비 (분기)"),
        S("policy_rate", "한국은행 기준금리", 1, base_rate, desc="한은 기준금리 (월말 기준)"),
        S("ktb3", "국고채 3년물 금리", 1, ktb3, desc="국고채 3년 월평균 수익률"),
        S("ktb10", "국고채 10년물 금리", 1, ktb10, desc="국고채 10년 월평균 수익률"),
        S("debt", "국가채무 (D1)", 1, debt, axis="level", unit="조원",
          desc="기획재정부 국가채무 (연간, KOSIS)"),
        S("debt_ratio", "GDP 대비 국가채무 비율", 1, debt_ratio,
          desc="국가채무 ÷ 명목GDP (연간, KOSIS)"),
        S("emp_chg", "취업자 증감 (전년동월대비)", 1, emp_chg, axis="level", unit="만 명", norm=False,
          desc="미국의 '비농업 고용 증감'에 대응하는 지표 (통계청 경제활동인구조사)"),
        S("leading", "선행지수 순환변동치", 1, leading, axis="level", unit="", norm=False,
          desc="통계청 경기종합지수. 100 위=경기 확장 신호, 아래=수축 신호 (몇 달 앞서 움직임)"),
        S("coincident", "동행지수 순환변동치", 1, coincident, axis="level", unit="", norm=False,
          desc="현재 경기 상태를 보여주는 지수. 100이 추세선"),
        S("indprod", "전산업생산지수 (계절조정)", 1, indprod, axis="level", unit="",
          desc="월간 경기 흐름을 빠르게 보여주는 생산 지표 (2020=100)"),
        S("housing", "주택매매가격지수 (KB)", 1, housing, axis="level", unit="",
          desc="KB국민은행 전국 주택매매가격지수. 금리와 겹쳐 보면 좋습니다"),
        S("wti", "WTI 유가", 1, wti, axis="level", unit="$/배럴", desc="서부텍사스산 원유 월평균"),
        S("brent", "브렌트 유가", 1, brent, axis="level", unit="$/배럴", desc="브렌트유 월평균"),
    ]
    months = [d for d, _ in core]
    pi = [dict(core).get(m) for m in months]
    gap_m = ffill_to(months, gap_q)
    pol = [dict(base_rate).get(m) for m in months]
    series += policy_rules(months, pi, gap_m, pol, "실제 기준금리와 겹쳐 보세요.", "근원 CPI")
    ngdp_m = ffill_to(months, ngdp_yoy)
    base_m = [dict(base_yoy).get(m) for m in months]
    series += mccallum_ngdp_friedman(months, ngdp_m, base_m, m2_yoy, 4.5, 4.0)
    series += [
        S("exp_inflation", "기대인플레이션 (향후 1년)", 4, expinf,
          desc="한국은행 소비자동향조사: 일반인이 예상하는 향후 1년 물가상승률"),
        S("hh_debt", "가계부채 (가계신용)", 4, hh_debt, axis="level", unit="조원",
          desc="가계대출+판매신용 잔액 (한국은행, 분기)"),
        S("hh_debt_gdp", "GDP 대비 가계부채 비율", 4, hh_debt_gdp,
          desc="가계신용 ÷ 최근 4분기 명목GDP 합 (자체 계산)"),
        S("mktcap", "KOSPI 시가총액", 4, mktcap, axis="level", unit="조원",
          desc="유가증권시장 상장주식 시가총액 (월말)"),
        S("foreign_share", "외국인 보유 비중 (추정)", 4, foreign_share,
          desc="국제투자대조표의 외국인 보유 지분증권 잔액 ÷ (KOSPI+KOSDAQ 시가총액). 환율 환산 추정치"),
        S("stock_idx", "KOSPI 지수", 4, kospi, axis="level", unit="",
          desc="코스피 월말 종가 (1980.1.4=100)"),
        S("usdkrw", "원/달러 환율", 4, usdkrw, axis="level", unit="원",
          desc="매매기준율 월평균. 오르면 원화 약세"),
        S("credit_spread", "신용스프레드 (회사채−국고채)", 4, credit_spread,
          desc="회사채(3년, AA-) − 국고채(3년). 벌어지면 기업 자금조달 스트레스 신호"),
        S("output_gap", "산출갭 (HP필터 추정)", 3, gap_q,
          desc="한국은 공식 잠재GDP가 없어 HP필터(λ=1600)로 추정한 값입니다"),
        S("nairu", "자연실업률 근사 (HP필터)", 3, nairu,
          desc="실업률의 장기 추세(HP필터)를 NAIRU 근사치로 사용"),
        S("unemp_gap", "실업률 갭", 3, unemp_gap, desc="실업률 − 추세. 마이너스면 과열 노동시장"),
        S("okun", "오쿤 법칙 예측 산출갭", 3, okun_pred,
          desc="산출갭 ≈ −2×실업률갭 (오쿤 계수 2 가정). 실제 산출갭과 겹쳐 보세요"),
    ]
    series += intl_series("KOR")
    un_map = dict(unrate)
    phillips = {"x": "실업률(%)", "y": "근원 CPI 인플레이션(%)",
                "points": [[un_map.get(m), v, m] for m, v in core if un_map.get(m) is not None]}
    return {"series": series, "phillips": phillips}


# ---------------------------------------------------------------- 일본 데이터
def build_jp():
    """일본. 주의: 이 환경에서 FRED의 일본 CPI 월간 시리즈가 중단돼
    인플레이션은 GDP 디플레이터(분기)로 계산한다."""
    unrate = fred("LRHUTTTTJPM156S")            # 실업률 (월, 계절조정)
    rgdp = fred("JPNRGDPEXP")                    # 실질GDP (분기)
    ngdp_lvl = fred("JPNNGDP")                   # 명목GDP (분기)
    call_rate = fred("IRSTCI01JPM156N")          # 무담보 콜금리 (월)
    gb10 = fred("IRLTLT01JPM156N")               # 국채 10년
    nikkei = fred("NIKKEI225", freq="m")
    fx = fred("DEXJPUS", freq="m")               # 엔/달러
    wti = fred("MCOILWTICO")
    brent = fred("MCOILBRENTEU")

    rg = dict(rgdp)
    deflator = [[d, round(v / rg[d] * 100, 3)] for d, v in ngdp_lvl if rg.get(d)]
    infl = yoy(deflator, 4)                      # 디플레이터 전년비 (분기)
    gdp_g = yoy(rgdp, 4)
    ngdp_yoy = yoy(ngdp_lvl, 4)

    logs = [math.log(v) * 100 for _, v in rgdp]
    trend = hp_filter(logs, 1600.0)
    gap_q = [[rgdp[i][0], round(logs[i] - trend[i], 2)] for i in range(len(rgdp))]
    u_vals = [v for _, v in unrate]
    u_trend = hp_filter(u_vals, 129600.0)
    nairu = [[unrate[i][0], round(u_trend[i], 2)] for i in range(len(unrate))]
    unemp_gap = [[unrate[i][0], round(u_vals[i] - u_trend[i], 2)] for i in range(len(unrate))]
    okun_pred = [[m, round(-2.0 * v, 2)] for m, v in unemp_gap]

    series = [
        S("infl", "인플레이션 (GDP디플레이터)", 1, infl,
          desc="FRED의 일본 CPI 월간 통계가 중단되어 GDP디플레이터 전년비(분기)로 대체. "
               "IMF 전망(섹터5)의 CPI 연평균과 함께 보세요"),
        S("unrate", "실업률 (계절조정)", 1, unrate, desc="월간 조화 실업률"),
        S("gdp_growth", "실질GDP 성장률", 1, gdp_g, desc="전년동기대비 (분기)"),
        S("policy_rate", "일본은행 정책금리 (콜금리)", 1, call_rate,
          desc="무담보 콜금리 익일물 월평균 — BOJ 정책금리의 실효 지표"),
        S("gb10", "국채 10년물 금리", 1, gb10, desc="일본 국채 10년 월평균"),
        S("wti", "WTI 유가", 1, wti, axis="level", unit="$/배럴", desc="서부텍사스산 원유 월평균"),
        S("brent", "브렌트 유가", 1, brent, axis="level", unit="$/배럴", desc="브렌트유 월평균"),
    ]
    months = [d for d, _ in call_rate]
    pi = ffill_to(months, infl)
    gap_m = ffill_to(months, gap_q)
    pol = [dict(call_rate).get(m) for m in months]
    series += policy_rules(months, pi, gap_m, pol, "실제 콜금리와 겹쳐 보세요.", "GDP디플레이터")
    const_x = [[d, 3.0] for d, _ in ngdp_yoy]
    series += [
        S("ngdp_growth", "명목GDP 증가율", 2, ngdp_yoy,
          desc="명목GDP 목표제: 일본에서 특히 오래 논의된 준칙 (디플레 탈출용)"),
        S("ngdp_target", "명목GDP 목표선 (3.0%)", 2, const_x, dash=True,
          desc="일본 논의에서 흔히 쓰이는 명목성장 목표 수준"),
        S("output_gap", "산출갭 (HP필터 추정)", 3, gap_q,
          desc="실질GDP 로그에 HP필터(λ=1600)를 적용한 추정치"),
        S("nairu", "자연실업률 근사 (HP필터)", 3, nairu,
          desc="실업률의 장기 추세를 NAIRU 근사치로 사용"),
        S("unemp_gap", "실업률 갭", 3, unemp_gap, desc="실업률 − 추세. 마이너스면 과열 노동시장"),
        S("okun", "오쿤 법칙 예측 산출갭", 3, okun_pred,
          desc="산출갭 ≈ −2×실업률갭 (오쿤 계수 2 가정). 실제 산출갭과 겹쳐 보세요"),
        S("stock_idx", "닛케이 225", 4, nikkei, axis="level", unit="", desc="월평균 종가"),
        S("fx_rate", "엔/달러 환율", 4, fx, axis="level", unit="엔",
          desc="월평균. 오르면 엔화 약세"),
    ]
    series += intl_series("JPN")
    return {"series": series, "phillips": None}  # 월간 물가가 없어 필립스 산점도 생략


# ---------------------------------------------------------------- 유로존 데이터
def build_ez():
    """유로존. 실업률 월간 시리즈가 중단돼 IMF 연간(전망 포함)으로 대체."""
    hicp_idx = fred("CP0000EZ19M086NEST")            # HICP 종합 (월, 지수)
    core_idx = fred("TOTNRGFOODEA20MI15XM")          # HICP 에너지·식품 제외 (월, 지수)
    rgdp = fred("CLVMNACSCAB1GQEA19")                # 실질GDP (분기)
    ngdp_lvl = fred("CPMNACSCAB1GQEA19")             # 명목GDP (분기)
    dfr_d = fred("ECBDFR", start="1999-01-01")       # ECB 예금금리 (일별)
    gb10 = fred("IRLTLT01EZM156N")
    fx = fred("DEXUSEU", freq="m")                   # 달러/유로
    wti = fred("MCOILWTICO")
    brent = fred("MCOILBRENTEU")
    unrate_a = wb("SL.UEM.TOTL.ZS", "EMU", 1991)     # 실업률 (연간, ILO — IMF 미제공 대체)

    cpi = yoy(hicp_idx, 12)
    core = yoy(core_idx, 12)
    gdp_g = yoy(rgdp, 4)
    ngdp_yoy = yoy(ngdp_lvl, 4)
    policy = monthly_last(dfr_d)

    logs = [math.log(v) * 100 for _, v in rgdp]
    trend = hp_filter(logs, 1600.0)
    gap_q = [[rgdp[i][0], round(logs[i] - trend[i], 2)] for i in range(len(rgdp))]

    series = [
        S("core_cpi", "근원 HICP 인플레이션 (에너지·식품 제외)", 1, core,
          desc="ECB가 중시하는 근원물가 (전년동월대비)"),
        S("cpi", "HICP 인플레이션", 1, cpi, desc="유로존 조화소비자물가 전년동월대비. ECB 목표 2%"),
        S("unrate", "실업률 (연간, World Bank)", 1, unrate_a,
          desc="유로존 월간 실업률 시리즈가 중단되어 World Bank 연간(ILO 기준)으로 대체"),
        S("gdp_growth", "실질GDP 성장률", 1, gdp_g, desc="전년동기대비 (분기)"),
        S("policy_rate", "ECB 예금금리", 1, policy,
          desc="ECB 예금창구금리(DFR) 월말 기준 — 현재 ECB의 실질적 기준금리"),
        S("gb10", "국채 10년물 금리 (유로존 평균)", 1, gb10, desc="유로존 장기금리 월평균"),
        S("wti", "WTI 유가", 1, wti, axis="level", unit="$/배럴", desc="서부텍사스산 원유 월평균"),
        S("brent", "브렌트 유가", 1, brent, axis="level", unit="$/배럴", desc="브렌트유 월평균"),
    ]
    months = [d for d, _ in core]
    pi = [dict(core).get(m) for m in months]
    gap_m = ffill_to(months, gap_q)
    pol = [dict(policy).get(m) for m in months]
    series += policy_rules(months, pi, gap_m, pol, "실제 ECB 예금금리와 겹쳐 보세요.", "근원 HICP")
    const_x = [[d, 4.0] for d, _ in ngdp_yoy]
    series += [
        S("ngdp_growth", "명목GDP 증가율", 2, ngdp_yoy, desc="명목GDP 목표제 비교용"),
        S("ngdp_target", "명목GDP 목표선 (4.0%)", 2, const_x, dash=True,
          desc="실질성장+물가목표 수준의 명목성장 기준선"),
        S("output_gap", "산출갭 (HP필터 추정)", 3, gap_q,
          desc="실질GDP 로그에 HP필터(λ=1600)를 적용한 추정치"),
        S("fx_rate", "달러/유로 환율", 4, fx, axis="level", unit="달러",
          desc="월평균. 오르면 유로화 강세 (달러 기준 표기)"),
    ]
    series += intl_series("EUQ", "EMU")
    return {"series": series, "phillips": None}  # 월간 실업률이 없어 필립스 산점도 생략


BUILDERS = {"us": build_us, "kr": build_kr, "jp": build_jp, "ez": build_ez}
COUNTRIES = tuple(BUILDERS)


# ---------------------------------------------------------------- 캐시
_build_lock = threading.Lock()


def _cache_fresh(path):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL


def get_payload(country, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{country}.json")
    if not refresh and _cache_fresh(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with _build_lock:
        if not refresh and _cache_fresh(path):  # 대기 중 다른 요청이 이미 수집했으면 재사용
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return _build_and_save(country, path)


def _build_and_save(country, path):
    print(f"[{country}] 데이터 새로 수집 중...", flush=True)
    payload = BUILDERS[country]()
    payload["series"] = [s for s in payload["series"] if s["data"]]  # 빈 시리즈 제거
    payload["updated"] = time.strftime("%Y-%m-%d %H:%M")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"[{country}] 완료 (시리즈 {len(payload['series'])}개)", flush=True)
    return payload





# ---------------------------------------------------------------- AI 결과 캐시
# 생성은 백그라운드 스레드에서 진행 (시뮬레이션은 수 분 걸리므로 HTTP 응답은 즉시 반환)
_ai_lock = threading.Lock()
_ai_jobs = {}    # (kind, country) -> True (생성 중)
_ai_errors = {}  # (kind, country) -> 마지막 실패 메시지


def _ai_path(kind, country):
    os.makedirs(REPORT_DIR, exist_ok=True)
    return os.path.join(REPORT_DIR, f"{kind}_{country}.json")


def _ai_cached(kind, country):
    path = _ai_path(kind, country)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _ai_worker(kind, country, payload):
    key = (kind, country)
    try:
        print(f"[{country}] AI {kind} 생성 시작 (NVIDIA API 호출)", flush=True)
        result = (analysis.generate_report(country, payload) if kind == "report"
                  else analysis.run_fomc(country, payload))
        result["data_updated"] = payload["updated"]
        result["created"] = time.strftime("%Y-%m-%d %H:%M")
        with open(_ai_path(kind, country), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[{country}] AI {kind} 완료", flush=True)
    except Exception as e:
        _ai_errors[key] = str(e)
        print(f"[{country}] AI {kind} 실패: {e}", flush=True)
    finally:
        _ai_jobs.pop(key, None)


def get_ai(kind, country, run=False, force=False):
    """kind: 'report' | 'fomc'.
    run=False: 저장본 조회만 (크레딧 소모 없음). 생성 중이면 status=running.
    run=True: 생성 시작(백그라운드). 데이터가 그대로면 저장본 재사용."""
    key = (kind, country)
    if key in _ai_jobs:
        return {"status": "running"}
    if not run:
        err = _ai_errors.pop(key, None)
        if err:
            return {"none": True, "error": err}
        return _ai_cached(kind, country) or {"none": True}
    payload = get_payload(country)
    cached = _ai_cached(kind, country)
    if cached and not force and cached.get("data_updated") == payload["updated"]:
        return cached  # 데이터 미변경 → 저장본 재사용 (크레딧 절약)
    with _ai_lock:
        if key not in _ai_jobs:
            _ai_jobs[key] = True
            _ai_errors.pop(key, None)
            threading.Thread(target=_ai_worker, args=(kind, country, payload),
                             daemon=True).start()
    return {"status": "running"}


# ---------------------------------------------------------------- HTTP 서버
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/data":
            q = urllib.parse.parse_qs(parsed.query)
            country = q.get("country", ["us"])[0]
            if country not in COUNTRIES:
                self._send(400, b'{"error":"unknown country"}', "application/json")
                return
            try:
                payload = get_payload(country, refresh="refresh" in q)
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json")
            except Exception as e:
                msg = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
                self._send(500, msg, "application/json")
                print("API 오류:", e, flush=True)
            return
        if parsed.path in ("/api/report", "/api/fomc"):
            q = urllib.parse.parse_qs(parsed.query)
            country = q.get("country", ["us"])[0]
            if country not in COUNTRIES:
                self._send(400, b'{"error":"unknown country"}', "application/json")
                return
            try:
                result = get_ai(parsed.path.rsplit("/", 1)[-1], country,
                                run="run" in q, force="force" in q)
                self._send(200, json.dumps(result, ensure_ascii=False).encode("utf-8"),
                           "application/json")
            except Exception as e:
                msg = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
                self._send(500, msg, "application/json")
                print("AI API 오류:", e, flush=True)
            return
        # 정적 파일
        rel = parsed.path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB) or not os.path.isfile(full):
            self._send(404, "not found".encode(), "text/plain")
            return
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"거시경제 대시보드 실행 중: {url}  (종료: Ctrl+C)", flush=True)
    if "--open" in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
