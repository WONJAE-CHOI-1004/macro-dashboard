# -*- coding: utf-8 -*-
"""
GitHub Pages용 정적 사이트 빌드
- web/ 화면을 _site/로 복사하고 정적 모드 플래그를 주입
- 데이터(FRED/ECOS/KOSIS)와 AI 리포트·시뮬레이션을 JSON 파일로 생성
사용:
  py build_static.py            # 전체 빌드 (AI 생성 포함 — NVIDIA 크레딧 16콜)
  py build_static.py --no-ai    # AI는 기존 reports/ 저장본을 재사용 (로컬 테스트용)
"""
import json
import os
import shutil
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import analysis  # noqa: E402
import server    # noqa: E402

OUT = os.path.join(BASE, "_site")


def _pages_url():
    """GitHub Actions 환경이면 현재 저장소의 Pages 주소를 유추"""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return None


def _fetch_previous(kind, country, dst):
    """AI 생성 실패 시 이미 공개된 사이트의 이전 결과를 그대로 유지"""
    url = _pages_url()
    if not url:
        return False
    try:
        with urllib.request.urlopen(f"{url}{kind}_{country}.json", timeout=30) as r:
            data = r.read()
        with open(dst, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def build(with_ai=True):
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(server.WEB, OUT)

    # index.html에 정적 모드 플래그 주입
    idx_path = os.path.join(OUT, "index.html")
    with open(idx_path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace('<script src="app.js"></script>',
                        '<script>window.STATIC_MODE = true;</script>\n<script src="app.js"></script>')
    html = html.replace("6시간마다 자동 갱신", "매주 자동 갱신 (GitHub Actions)")
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(html)

    for country in ("us", "kr"):
        print(f"[{country}] 데이터 수집...", flush=True)
        payload = server.get_payload(country, refresh=True)
        with open(os.path.join(OUT, f"data_{country}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        for kind in ("report", "fomc"):
            dst = os.path.join(OUT, f"{kind}_{country}.json")
            if with_ai:
                time.sleep(5)  # 연속 호출 레이트리밋 완화
                print(f"[{country}] AI {kind} 생성...", flush=True)
                try:
                    result = (analysis.generate_report(country, payload) if kind == "report"
                              else analysis.run_fomc(country, payload))
                    result["data_updated"] = payload["updated"]
                    result["created"] = time.strftime("%Y-%m-%d %H:%M")
                    with open(dst, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False)
                except Exception as e:
                    # AI가 실패해도 배포는 계속: 이전 공개본을 유지한다
                    print(f"[{country}] AI {kind} 실패({e}) → 이전 공개본 유지 시도", flush=True)
                    if _fetch_previous(kind, country, dst):
                        print(f"[{country}] AI {kind}: 이전 공개본 재사용", flush=True)
                    else:
                        print(f"[{country}] AI {kind}: 이전 공개본도 없음 → 건너뜀", flush=True)
            else:
                src = os.path.join(server.REPORT_DIR, f"{kind}_{country}.json")
                if os.path.exists(src):
                    shutil.copy(src, dst)
                    print(f"[{country}] AI {kind}: 저장본 재사용", flush=True)
                else:
                    print(f"[{country}] AI {kind}: 저장본 없음 → 건너뜀", flush=True)

    print(f"빌드 완료: {OUT}", flush=True)


if __name__ == "__main__":
    build(with_ai="--no-ai" not in sys.argv)
