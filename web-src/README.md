# web-src — 화면(web/)의 원본

`web/`은 **빌드 결과물**입니다. 직접 고치지 말고 여기 원본을 고친 뒤 다시 빌드하세요.

## 구성

| 파일 | 설명 |
|---|---|
| `macro.html` | 화면 구조 (CoreUI 템플릿 기반) |
| `macro.js` | 차트·데이터 로딩·AI 패널 로직 |
| `macro.scss` | 커스텀 스타일 (지표 목록, 표, FOMC 말풍선 등) |
| `build_web.py` | CoreUI 빌드 결과에서 필요한 파일만 추려 `web/`을 만드는 스크립트 |

## 왜 이런 구조인가

화면은 [CoreUI 무료 부트스트랩 템플릿](https://github.com/coreui/coreui-free-bootstrap-admin-template)(MIT)을
기반으로 합니다. 템플릿 전체 빌드는 23MB인데 그중 대부분이 이 대시보드가 쓰지 않는
아이콘 폰트(8.4MB)와 데모 페이지라, `build_web.py`가 실제로 필요한 것만 추려
`web/`(약 830KB)을 만듭니다.

## 수정 방법

1. CoreUI 템플릿을 받는다 (한 번만)

   ```
   git clone https://github.com/coreui/coreui-free-bootstrap-admin-template.git
   cd coreui-free-bootstrap-admin-template
   npm install
   ```

2. 이 폴더의 원본을 템플릿 안으로 복사한다

   ```
   copy macro.html  <템플릿>\src\views\
   copy macro.js    <템플릿>\src\js\
   copy macro.scss  <템플릿>\src\scss\
   ```

3. 고치고 싶은 내용을 `<템플릿>\src\` 쪽에서 수정한 뒤 빌드한다

   ```
   npm run build
   ```

4. 추출 스크립트를 실행한다. `web/`이 새로 만들어지고, 남은 참조가 전부
   존재하는지도 함께 검사한다.

   ```
   py build_web.py <템플릿>\dist
   ```

   저장소 바로 옆에 `dashboard-template-coreui/`가 있으면 인자를 생략해도 된다.

5. 수정한 원본을 이 폴더에도 다시 복사해 둔다 (다음 사람이 이어갈 수 있도록)

## 주의

- `build_web.py`는 `web/`을 통째로 지우고 다시 만든다. `web/`에 직접 한 수정은 사라진다.
- 데이터 JSON은 `build_static.py`가 `web/assets/macro-data/`가 아니라
  빌드 산출물(`_site/assets/macro-data/`)에 넣는다. 화면은 그 경로에서 읽는다.
- 정적 배포일 때만 `window.STATIC_MODE`가 주입된다(`build_static.py`). 로컬에서
  `py server.py --open`으로 띄우면 API 모드로 붙어 리포트 생성 버튼이 살아 있다.
