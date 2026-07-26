# -*- coding: utf-8 -*-
"""
build_static.py의 부분집합: 이미 저장된 _site 데이터를 재사용해서
빠져있는 AI 리포트/FOMC만 채운다 (외부 데이터 API 재호출 없음).
사용: py fill_gaps.py
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import analysis  # noqa: E402

OUT = os.path.join(BASE, "_site")

# (country, kind) 목록 — 지난 빌드에서 "저장본 없음 → 건너뜀" 이었던 것들만
TARGETS = [
    ("jp", "fomc"),
    ("ez", "report"),
    ("ez", "fomc"),
]

for country, kind in TARGETS:
    with open(os.path.join(OUT, f"data_{country}.json"), encoding="utf-8") as f:
        payload = json.load(f)
    print(f"[{country}] AI {kind} 생성...", flush=True)
    result = (analysis.generate_report(country, payload) if kind == "report"
              else analysis.run_fomc(country, payload))
    result["data_updated"] = payload["updated"]
    result["created"] = time.strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(OUT, f"{kind}_{country}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"[{country}] {kind} 완료", flush=True)
    time.sleep(5)

print("done")
