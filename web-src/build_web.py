# -*- coding: utf-8 -*-
"""CoreUI 템플릿 빌드 결과(dist)에서 이 대시보드에 필요한 것만 추려 web/을 만든다.
데모 페이지·아이콘 폰트(8.4MB)를 제외해 23MB → 약 830KB로 줄인다.

사용:  py build_web.py <CoreUI 템플릿의 dist 경로>
       (생략하면 이 저장소 옆의 dashboard-template-coreui/dist 를 찾는다)
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))          # web-src/
REPO = os.path.dirname(BASE)                                # macro-dashboard/
SRC = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(os.path.dirname(REPO), "dashboard-template-coreui", "dist"))
DST = os.path.join(REPO, "web")

if not os.path.isfile(os.path.join(SRC, "macro.html")):
    sys.exit(f"CoreUI 빌드 결과를 찾을 수 없습니다: {SRC}\n"
             f"템플릿에서 'npm run build'를 먼저 실행하거나, dist 경로를 인자로 넘기세요.")

# ---------------------------------------------------------------- 1. HTML 변환
with open(os.path.join(SRC, "macro.html"), encoding="utf-8") as f:
    html = f.read()


def cut(start_marker, end_marker, replacement="", *, end_from=None, last_end=False):
    """start_marker부터 end_marker까지를 replacement로 치환"""
    global html
    s = html.index(start_marker)
    search_from = html.index(end_from) if end_from else s
    e = (html.rindex(end_marker, s, search_from) if last_end
         else html.index(end_marker, search_from) + len(end_marker))
    if last_end:
        e += len(end_marker)
    html = html[:s] + replacement + html[e:]


# 코드 예제용 스타일 (템플릿 문서용이라 배포본에는 불필요)
html = html.replace('    <link href="css/examples.css" rel="stylesheet">\n', "")

# 사이드바 브랜드: CoreUI 로고 → 텍스트
cut('<div class="sidebar-brand me-auto">', "</div>",
    '<div class="sidebar-brand me-auto">\n'
    '          <span class="sidebar-brand-full fs-5 fw-semibold">📊 거시경제</span>\n'
    '          <span class="sidebar-brand-narrow fs-5">📊</span>\n'
    '        </div>',
    end_from='<button class="btn-close d-lg-none"', last_end=True)

# 사이드바: 국가 선택 항목은 남기고 템플릿 데모 페이지 링크만 제거한다.
# (1) 'UI Elements' 이후 전부 잘라내고 <ul>을 닫는다
cut('<li class="nav-title">UI Elements</li>', "</ul>", "      </ul>",
    end_from='<div class="sidebar-footer', last_end=True)
# (2) 맨 앞의 CoreUI 데모 Dashboard 항목 제거
i = html.index('<a class="nav-link" href="index.html">')
s = html.rindex('<li class="nav-item">', 0, i)
e = html.index("</li>", i) + len("</li>")
html = html[:s] + html[e:]

# 헤더 좌측의 장식용 아이콘 3개(알림·목록·메일 — 링크 없음) 제거
cut('<ul class="header-nav ms-auto">', "</ul>")
# 남은 헤더 nav를 오른쪽으로 밀어 테마 전환 버튼 위치 유지
html = html.replace('<ul class="header-nav">', '<ul class="header-nav ms-auto">', 1)

# 계정 드롭다운(가짜 프로필·Logout) 제거 — 바로 앞 구분선까지 함께
i_avatar = html.index("avatar-img")
s = html.rindex('<li class="nav-item py-1">', 0, i_avatar)
e = html.index("</ul>", i_avatar)
html = html[:s] + html[e:]

# 브레드크럼: 없는 페이지로 가는 Home 링크 제거
cut('<nav aria-label="breadcrumb">', "</nav>",
    '<nav aria-label="breadcrumb">\n'
    '            <ol class="breadcrumb my-0">\n'
    '              <li class="breadcrumb-item active"><span>거시경제 지표 대시보드</span></li>\n'
    '            </ol>\n'
    '          </nav>')

# STATIC_MODE는 build_static.py가 정적 빌드 때만 주입한다 (로컬 server.py는 API 모드)
html = html.replace("    <script>\n      window.STATIC_MODE = true;\n    </script>\n", "")

# 최소 CSS만 사용
html = html.replace('href="css/style.css"', 'href="css/style.min.css"')

# ---------------------------------------------------------------- 2. 파일 복사
if os.path.isdir(DST):
    shutil.rmtree(DST)
os.makedirs(DST)

KEEP = [
    "css/style.min.css",
    "css/macro.min.css",
    "css/vendors/simplebar.css",
    "js/config.js",
    "js/color-modes.js",
    "js/macro.js",
    "vendors/@coreui/coreui/js/coreui.bundle.min.js",
    "vendors/simplebar/js/simplebar.min.js",
    "vendors/simplebar/css/simplebar.css",
]
for rel in KEEP:
    src, dst = os.path.join(SRC, rel), os.path.join(DST, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(src, dst)

shutil.copytree(os.path.join(SRC, "assets", "favicon"),
                os.path.join(DST, "assets", "favicon"))

html = html.replace('href="css/macro.css"', 'href="css/macro.min.css"')

with open(os.path.join(DST, "index.html"), "w", encoding="utf-8") as f:
    f.write(html)

# 남아있는 참조가 실제로 존재하는지 검사
import re
missing = []
for m in re.finditer(r'(?:src|href)="([^"#][^"]*)"', html):
    ref = m.group(1)
    if ref.startswith(("http", "data:", "./")):
        continue
    if not os.path.exists(os.path.join(DST, ref)):
        missing.append(ref)
print("생성 완료:", DST)
print("깨진 참조:", sorted(set(missing)) or "없음")
