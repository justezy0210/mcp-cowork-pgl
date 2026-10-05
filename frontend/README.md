# Cowork 웹 프런트엔드

React 19, TypeScript, Vite 8, Tailwind CSS 4, shadcn/ui를 사용한다.
`src/components/ui/`는 공식 shadcn CLI로 추가한 컴포넌트이며, 공통 버튼·폼 배치는
`src/components/shared.tsx`와 `src/index.css`에서 관리한다. 기본 색상은 배경 `#F5F3ED`,
전경 `#2F312D`, 악센트 `#3D755D`다. 주요 버튼·로고는 악센트 원색을 사용하고,
선택·호버 배경과 중립 배지는 옅은 톤으로 구분한다.
확인창·모바일 메뉴의 backdrop과 스크롤 잠금은 정적 CSS로 적용해 기존 CSP를 유지한다.
모달 포커스·키보드 동작은 Radix Content가 담당한다. UI 컴포넌트를 CLI로 덮어쓸 때 이 조정을 보존한다.

## 개발과 빌드

저장소 루트에서 실행한다. Node.js 24와 npm이 필요하다.

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
npm --prefix frontend run build
```

개발 서버는 `/config.json`과 `/v1/web/**`를 로컬 허브 `127.0.0.1:8080`으로 전달한다.
Firebase에는 사용하는 localhost 도메인이 허용돼 있어야 한다.
프로덕션 빌드는 `src/cowork_hub/web/dist/`에 생성한다. 이 파일은 Git에 넣지 않는다.
FastAPI의 `/web/`, Python 패키지, 설치 프로그램, Firebase Hosting은 같은 빌드 파일을 사용한다.
Docker 이미지는 별도 Node 빌드 단계에서 이를 생성한다.

```sh
.venv/bin/python scripts/build_web.py \
  --config .local/web.firebase.pending.json \
  --output .local/firebase-release-NEW
```

운영 웹은 Firebase Hosting에서 제공하고, 브라우저가 `https://203.255.11.226`의
허브 웹 API를 직접 호출한다. 설정 파일의 `api_base_url`에는 이 주소를 넣는다.
빌드 도구는 정적 Hosting 파일만 생성하며 명시적인 HTTPS API 주소가 필요하다.
배포 시에는 `--only hosting`을 사용한다.
HTTPS 인증서·갱신·복구 정보는 [운영 연결 안내](../docs/firebase-hosting.md)를 참고한다.

## 구성

- `src/app.tsx`: 데스크톱 메뉴, 모바일 Sheet, 기존 hash URL 라우팅.
- `src/pages/`: 가입, 서버·작업, 컨테이너, 토큰, 설치 안내, 알림, 관리자 화면.
- `src/lib/session.ts`: Firebase 로그인, 인증 API, 계정별 30분 화면 복원 캐시.
- `src/lib/hooks.ts`: 조회 취소, 20초 갱신, 중복 제출 방지.
- `src/lib/install-guide.ts`: 사용자 입력을 셸 인자로 안전하게 인용하는 설치 명령 생성.

설치 안내는 입력에 따라 명령을 바로 갱신하고 같은 화면에서 개인 토큰을 준비한다.
기존 설치 업데이트는 설치 폴더만 받아 `--update`를 실행하며 서버·허브·토큰은 원격 컨테이너의
기존 설정에서 읽는다. 토큰 전달 UI는 개인 토큰 페이지와 공유하고 원문은 React 메모리에만 둔다.

캐시는 화면 복원에만 쓰며 권한 검사는 모든 API에서 수행한다. 로그아웃·계정 전환·권한 거절 시
이전 응답과 화면 상태를 폐기한다. 임시 네트워크 오류는 Google 로그인을 다시 요구하지 않는다.
Firebase 로그인은 `browserLocalPersistence`로 브라우저 재시작 후에도 복원한다.
프로필 화면 캐시는 `localStorage`에 최대 30분간 보관해 새 탭·브라우저 재시작에도 복원한다.
MCP 토큰·웹훅은 브라우저 저장소에 기록하지 않으며 공개 설정에는 Firebase 공개 필드만 넣는다.
관리자 폼은 입력 도중 자동 갱신하지 않는다.

## 검증

```sh
npm --prefix frontend run build
COWORK_PLAYWRIGHT_PACKAGE=/path/to/playwright node tests/web_react.cjs
COWORK_PLAYWRIGHT_PACKAGE=/path/to/playwright node tests/web_auth_persistence.cjs
.venv/bin/pytest -q tests/test_web.py tests/test_server_web.py tests/test_onboarding.py tests/test_setup_client.py
```

브라우저 검증은 실제 프로덕션 번들·CSP와 가상 Firebase/API를 사용한다. 320~1440px의 화면 배치,
가입·승인·권한·알림·SSH 연결, 토큰 발급·복사·다운로드·폐기, 로그인 캐시와 늦은 응답을 확인한다.
운영 계정 승인이나 실제 토큰 발급은 하지 않는다. 스크린샷과 결과는 `.local/verification/react-migration/`에 저장한다.
한국어 화면 확인용 글꼴은 `COWORK_TEST_FONT`로 지정할 수 있다.
`web_auth_persistence.cjs`는 실제 Firebase SDK를 읽으므로 네트워크 접근이 필요하다.
Google 인증 교환과 웹 API만 합성 응답으로 대체해 브라우저 재시작 후 로그인 복원,
탭 사이 로그아웃 반영, 로그아웃 후 재시작을 검증한다.

Login restores a same-account profile hint from local storage for up to 30 minutes,
then revalidates it against the hub. The hint never authorizes API calls. Logout,
account changes, and permission failures clear private in-memory catalog responses.
Catalog selections are reused for 30 seconds within the current login, with at most
50 entries; file metadata is not persisted to browser storage. Explicit refresh
bypasses this cache. Concurrent profile refreshes share one pending request.
