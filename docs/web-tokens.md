# 웹에서 개인 토큰 발급받기

2026-09-21: Google 로그인과 개인 토큰 발급·다운로드·목록·폐기 화면을 구현했다.
운영 Firebase 프로젝트·허브 HTTPS 주소·인증 설정은 아직 연결하지 않았다.
설정 전에도 `/web/` 화면은 열리지만 로그인과 발급은 비활성화된다.

## 사용자 이용 순서

1. 관리자가 안내한 웹 주소에서 **Google로 로그인**한다.
2. 계정이 연결되지 않았다고 나오면 화면의 **Firebase 계정 식별자**를 관리자에게 전달한다.
   이는 비밀번호나 MCP 토큰이 아니며 Linux UID와도 다른 값이다.
3. 관리자가 해당 Firebase 계정을 본인의 허브 사용자와 연결하면 다시 로그인한다.
4. `226-Codex`처럼 사용할 곳을 이름으로 적고 **토큰 발급**을 누른다.
5. **user.token 다운로드**로 파일을 저장한다. 본인 메인 컨테이너의 토큰 경로에 준비하고
   파일 소유자가 본인인지 확인한 뒤 `chmod 600 /본인/경로/user.token`을 적용한다.
6. [신규 사용자 매뉴얼](first-user-guide.md)의 설치·연결을 진행한다.

기존 MCP 토큰이 없어도 이 흐름으로 첫 개인 토큰을 발급받을 수 있다.
Google 로그인 자체가 서버 사용 권한을 주지는 않는다. 관리자가 허브 사용자·UID/GID·허용 서버를
먼저 등록해야 하고, 새 컨테이너의 환경 승인과 Discord 목적지 연결도 기존 절차를 따른다.

토큰 원문은 발급 응답에서만 전달한다. 화면을 닫거나 로그아웃하면 다시 다운로드할 수 없다.
목록에는 이름·발급 시각·폐기 상태만 나온다. 분실한 토큰은 폐기하고 새 토큰을 발급한다.
연결 프로그램·실행기가 사용 중인 토큰은 새 파일로 교체한 뒤 폐기해야 한다.
폐기만으로 계산을 취소하거나 예약을 반환하지 않으며, 이후 실행기의 보고가 거절될 수 있다.

## 관리자: Firebase와 계정 연결

1. Firebase 프로젝트에 웹 앱을 등록하고 Authentication의 **Google** 로그인 제공자를 활성화한다.
2. 로그인 페이지를 제공할 도메인을 Authentication의 승인된 도메인에 추가한다.
3. 웹 앱 설정의 `apiKey`, `authDomain`, `projectId`, `appId`를 준비한다. 이 네 값은 공개 웹 설정이다.
4. 허브에는 같은 프로젝트의 Admin SDK 자격 증명을 준비한다. Google 관리 환경에서는 Application
   Default Credentials를 사용할 수 있고, 개인 서버에서는 서비스 계정 JSON을 허브만 읽는 파일로
   제공한 뒤 `GOOGLE_APPLICATION_CREDENTIALS`로 그 경로를 지정한다. 해당 자격 증명은
   사용자 조회 권한이 필요하다. 서명·프로젝트·만료뿐 아니라 폐기·계정 중지 여부도 확인한다.
5. 기존 허브 사용자와 Firebase Authentication의 사용자 UID를 연결한다. 이메일 문자열이나
   브라우저가 보낸 허브 사용자 ID를 신원으로 사용하지 않는다.

[Google 로그인 안내](https://firebase.google.com/docs/auth/web/google-signin)와
[서버 ID 토큰 검증 안내](https://firebase.google.com/docs/auth/admin/verify-id-tokens)를 따른다.
Admin SDK 개인키 파일은 브라우저·GitHub·웹 배포 디렉터리에 넣지 않는다.
Firebase Authentication 에뮬레이터를 지정한 허브는 웹 인증을 시작하지 않는다.

`config/web.example.json`을 참고해 실제 설정을 비공개 파일(예: 허브의 `/data/web.json`)에 준비한다.

```json
{
  "firebase": {
    "apiKey": "FIREBASE_WEB_API_KEY",
    "authDomain": "your-project.firebaseapp.com",
    "projectId": "your-project",
    "appId": "FIREBASE_WEB_APP_ID"
  },
  "users": {"FIREBASE_AUTH_USER_UID": "ezy"},
  "allowed_origins": ["https://your-project.web.app"],
  "api_base_url": "https://hub.example.org"
}
```

`users`는 관리자 허용 목록이다. 연결된 허브 사용자가 없거나 비활성화됐거나 관리자/Worker 역할이면
웹 토큰 발급을 거절한다. 사용자마다 Firebase 계정 하나를 연결한다.
이번 범위의 계정 허용 목록은 관리자 설정 파일에서 관리하며 변경 후 허브를 재시작한다.
웹에서 사용자 승인·SSH 추가·서버 이용 제한을 관리하는 화면은 후속 범위다.

## 관리자: 허브 적용

Docker 이미지에는 서버용 `web` 선택 의존성을 포함했다. 기존 클라이언트 설치에는 Firebase SDK가
추가되지 않는다. Python으로 허브를 직접 실행할 때는 `web` extra를 포함해 설치한다.
DB 스키마는 4를 유지하며 기존 사용자·토큰·작업을 변경하지 않는다.

허브 프로세스에 다음 환경변수를 설정한다. **경로는 허브 컨테이너 안에서 읽을 수 있는 경로**다.
서비스 계정 파일과 관리자 설정 파일은 허브 실행 사용자만 읽을 수 있게 준비한다.

```text
HUB_WEB_CONFIG=/data/web.json
GOOGLE_APPLICATION_CREDENTIALS=/data/firebase-admin.json
```

현재 연구실에서는 기존 `.local/compose.local-runner.yaml`의 `services.hub.environment`에 이 두 값을
추가하면 기존 업데이트 도구에서도 유지된다. Discord 설정과 기존 볼륨을 보존한다.
파일을 준비한 뒤 허브 호스트에서 기존 업데이트 명령을 실행한다.

```sh
python3 scripts/enable_local_runner.py --user ezy \
  --config /10Gdata/ezy/01_Programs/cowork-mcp/.local/runner-226.json --upgrade
```

브라우저가 접근할 허브 HTTPS 주소를 별도로 준비해야 한다. Firebase Hosting에 페이지를 올리는 것만으로
내부망 `http://192.168.10.41:8080`이 외부에서 접근 가능해지거나 HTTPS로 바뀌지는 않는다.
사용자 브라우저가 접근 가능한 HTTPS 프록시 주소를 사용하고, 접근 범위는 기존 연구실 운영 방침을 따른다.

허브와 같은 HTTPS 주소에서 제공하려면 `/web/`로 접속한다. 이 경우 `api_base_url`은 빈 문자열로
두어 같은 주소를 사용할 수 있으며 다른 웹 출처를 쓰지 않으면 `allowed_origins`도 빈 목록으로 둔다.
HTTP는 로컬 개발의 `localhost`/`127.0.0.1`에서만 허용한다.

## Firebase Hosting으로 화면 배포

허브와 화면을 서로 다른 주소로 제공할 때만 아래 절차가 필요하다.
설정 파일의 `api_base_url`은 허브 HTTPS 주소, `allowed_origins`는 실제 웹 주소로 맞춘다.

```sh
.venv/bin/python scripts/build_web.py \
  --config /비공개/경로/web.json --output .local/web-hosting

cd .local/web-hosting
firebase deploy --only hosting --project YOUR_PROJECT_ID
```

두 번째 명령은 Firebase CLI 설치·로그인과 해당 프로젝트의 배포 권한이 준비된 환경에서 실행한다.
생성 도구는 새 출력 디렉터리에만 작성하며 기존 결과를 덮어쓰지 않는다. 다시 만들 때는 새 경로를 쓴다.
웹 파일·공개 Firebase 설정·허브 주소·Hosting 헤더 설정만 내보내며 `users`와 서비스 계정 파일은 제외한다.
빌드 명령은 배포를 수행하지 않는다. [Firebase Hosting 안내](https://firebase.google.com/docs/hosting/quickstart)를 참고한다.

## 인증과 API 범위

| API | 인증과 역할 |
| --- | --- |
| `GET /web/config.json` | 공개 웹 설정만 제공. 관리자 허용 목록·개인키 제외 |
| `GET /v1/web/me` | 검증된 Firebase Google 로그인 → 본인 허브 사용자 ID |
| `POST /v1/web/tokens` | 본인의 새 MCP/CLI 토큰 발급, 원문은 이 응답에서만 제공 |
| `GET /v1/web/tokens` | 본인 토큰 메타데이터, 페이지 단위 조회 |
| `DELETE /v1/web/tokens/{id}` | 본인 토큰 개별 폐기 |

웹 API는 Firebase ID 토큰을 Bearer 헤더로 검증한다. 기존 MCP·관리자·Worker 토큰으로 웹 로그인을
우회하지 않으며, Firebase ID 토큰도 기존 계산·관리 API의 인증을 대신하지 않는다.
토큰 발급·폐기는 기존 관리 서비스와 DB를 사용한다. Firebase에는 MCP 토큰을 저장하지 않는다.
MCP 토큰·로그인 인증은 브라우저 localStorage/sessionStorage에 저장하지 않고 페이지 메모리에서만 유지한다.
[Firebase 메모리 내 인증 유지 안내](https://firebase.google.com/docs/auth/web/auth-state-persistence)를 참고한다.

실제 운영 적용 완료 기준은 Google 로그인 성공, 미허용 계정 차단, 첫 토큰 다운로드,
다운로드한 파일을 사용한 MCP 인증 성공과 폐기 후 인증 거절이다.
자동 테스트의 외부 Google 로그인은 시험용으로 대체하므로 이 운영 확인을 대신하지 않는다.

2026-09-21 검증: 전체 Python 테스트 113개와 정적 검사 통과. 임시 허브·Chromium에서 첫 발급,
다운로드 파일로 인증, 모바일 배치, 이름의 HTML 삽입 방지, 로그아웃 후 원문 제거,
재로그인 시 기존 원문 비노출, 폐기 후 인증 거절을 확인했다. 운영 허브에는 아직 배포하지 않았다.
