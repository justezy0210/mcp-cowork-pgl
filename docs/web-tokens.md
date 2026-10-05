# 웹에서 개인 토큰 발급받기

2026-09-21: Google 로그인과 개인 토큰 발급·다운로드·목록·폐기 화면을 구현했다.
2026-09-26: 사용자가 Firebase 프로젝트를 `mcp-cowork-pgl-test`에서 `mcp-cowork-pgl`로 변경했다.
새 프로젝트의 웹 앱 설정 일치, Google 로그인 활성화와 기본 Hosting 도메인 허용을 확인했다.
2026-09-28: 사용자 승인 후 새 프로젝트의 허브 전용 서비스 계정·조회 권한·인증 키를 생성했다.
새 키로 Firebase Authentication 조회가 성공했고, 파일 권한 0600과 Git 제외 여부를 확인했다.
실제 허브 호스트에서 웹 인증 설정을 적용했고, 사용자가 Google 로그인 후 제공한 Firebase UID를
기존 허브 사용자 `ezy`에 연결했다. 해당 Google 계정의 이메일 인증·활성 상태와 허브 사용자 상태를 확인했다.
웹 설정은 `enabled: true`이고, 익명·잘못된 토큰으로 웹 API를 호출하면 401로 거절된다.
설정 적용 뒤에도 실행 중인 작업 2개와 실행기의 상태 보고가 정상 유지됐다.
사용자가 웹에서 발급·다운로드한 개인 토큰을 메인 컨테이너의
`.local/credentials/user-ezy-web.token`으로 전송했다. 소유자 UID 1101과 권한 0600을 확인하고,
이 파일을 지정한 실제 stdio MCP에서 도구 10개 및 허브·허용 서버·환경·작업·알림 설정 조회에 성공했다.
조회된 허용 서버는 `224`·`226`·`228`·`229`, 등록 환경은 4개다.
이번 확인은 새 토큰의 읽기 전용 인증 검증이며, 상시 MCP·실행기의 기존 토큰 설정은 유지한다.
새 토큰을 사용한 계산 제출과 운영 토큰 폐기 후 인증 거절은 아직 확인하지 않았다.
2026-09-29: [Firebase Hosting](https://mcp-cowork-pgl.web.app)과 공개 HTTPS 중계를 배포했다.
웹 배포와 직접 API 연결 운영 절차는 [Firebase Hosting과 허브 API](firebase-hosting.md)에 정리했다.
같은 날 `/web/`를 [서버 관리 플랫폼](server-platform.md)으로 확장했고 개인 토큰은 왼쪽 메뉴에서 관리한다.
사용자 승인에 따라 `ezy`를 첫 웹 관리자로 지정했다. 스키마 5 적용 후에도 새 토큰의 실제 MCP 조회에 성공했다.

새 프로젝트의 Google 로그인은 활성화되어 있고 `localhost`와 기본 Hosting 도메인이 허용되어 있다.
현재 에이전트가 접근하는 Docker에는 운영 허브가 없으므로, 아래 적용 도구는 실제 허브 호스트에서 실행한다.

추가 확인: 사용자가 443번도 사용할 수 없다고 답했다. 80번과 443번을 사용하는 직접 IP 인증서
방식은 현재 선택할 수 없다. Cloud Billing 조회에서 현재 프로젝트의 결제 연결 활성화를 확인했다.
공개 연결은 Firebase Hosting → Cloud Functions → 기존 SSH 포트 → 내부 허브 중계를 선택해 적용했다.
새로 만든 전용 SSH 키는 웹 API 중계 프로그램만 실행할 수 있다. 기존 개인 SSH 비밀키는
클라우드에 복사하지 않았다. 함수는 최소 0개·최대 1개 인스턴스로 시작한다.
허브 호스트의 접속 경로는 사용자 확인값 `ssh -p 2244 ezy@203.255.11.226`이다.
사용자가 `ssh-copy-id`를 실행한 뒤 현재 메인 컨테이너에서 키 인증에 성공했고,
호스트 키는 `.local/hub-host-known_hosts`에 보관해 후속 접속에서 확인한다.
아래 SSH 포트 전달은 `http://localhost:18080/web/`에서 내부 화면을 직접 점검할 때만 필요하다.

## 사용자 이용 순서

1. 관리자가 안내한 웹 주소에서 **Google로 로그인**한다.
2. 처음 로그인하면 **내 계정 등록**에서 컨테이너 계정 이름·UID·GID·Discord 웹훅 URL을 모두 입력한다.
3. 관리자가 정보를 확인하고 허용 서버를 선택해 계정을 승인하면 **승인 상태 확인**을 누른다.
4. 사용할 곳을 이름으로 적고 **토큰 발급**을 누른다. 이름을 비우면 로그인 계정에 맞춘
   예시 이름을 사용한다(`ezy` 계정은 `ezy-MCP`). 공백만 입력한 경우도 같다.
5. **user.token 다운로드**로 파일을 저장한다. 본인 메인 컨테이너의 토큰 경로에 준비하고
   파일 소유자가 본인인지 확인한 뒤 `chmod 600 /본인/경로/user.token`을 적용한다.
6. [신규 사용자 매뉴얼](first-user-guide.md)의 설치·연결을 진행한다.

기존 MCP 토큰이 없어도 이 흐름으로 첫 개인 토큰을 발급받을 수 있다.
Google 로그인 자체가 서버 사용 권한을 주지는 않는다. 관리자가 허브 사용자·UID/GID·허용 서버를
확인·승인해야 하고, 새 컨테이너는 별도 승인 절차를 따른다.
Discord 채널은 최초 계정 등록 때 입력하고 이후 변경은 관리자가 처리한다. 사용자는 **Discord 알림**에서 본인 연결 상태를 확인한다.

같은 브라우저에서 새로고침하거나 브라우저를 닫았다 다시 열면 Firebase가 저장한 로그인 상태를 복원한다. 마지막으로 조회한 허브
프로필도 `sessionStorage`에 최대 30분간 보관해, 같은 Firebase 계정·프로젝트·허브일 때 화면을
먼저 열고 최신 권한을 뒤에서 조회한다. 갱신 중 입력한 값은 유지하고 변경된 서버 권한은 반영한다.
로그아웃·계정 전환·인증 거절 시 캐시를 지우며, 저장소가 차단됐거나 캐시가 만료되면 새로 조회한다.
이 캐시는 화면 표시용이며 API의 인증·권한 검사는 계속 수행한다. MCP 토큰 원문과 발급 목록,
작업 내역은 캐시하지 않는다.

토큰 원문은 발급 응답에서만 전달한다. 화면을 닫거나 로그아웃하면 다시 다운로드할 수 없다.
목록에는 이름·발급 시각·폐기 상태만 나온다. 분실한 토큰은 폐기하고 새 토큰을 발급한다.
연결 프로그램·실행기가 사용 중인 토큰은 새 파일로 교체한 뒤 폐기해야 한다.
폐기만으로 계산을 취소하거나 예약을 반환하지 않으며, 이후 실행기의 보고가 거절될 수 있다.

## 관리자: Firebase와 계정 연결

### 현재 선택한 프로젝트

| 항목 | 값 |
| --- | --- |
| 프로젝트 ID | `mcp-cowork-pgl` |
| 웹 앱 ID | `1:565083816095:web:4a967aad6b86d8c5ad62aa` |
| 인증 도메인 | `mcp-cowork-pgl.firebaseapp.com` |
| 웹 출처 | `https://mcp-cowork-pgl.web.app` |
| 로컬 설정 초안 | `.local/web.firebase.pending.json` |

설정 초안은 현재 웹이 사용하는 Firebase 공개 설정 네 필드와 새 웹 출처를 반영했다.
Google Analytics·Storage·메시징은 현재 기능에서 사용하지 않는다.
2026-10-05부터 `api_base_url`은 `https://203.255.11.226`으로 설정한다.
브라우저가 HTTPS 허브 웹 API를 직접 호출하며, Firebase 함수와 SSH 중계를 거치지 않는다.
현재 설정의 `users`에는 사용자 본인이 제공한 Firebase UID와 `ezy`의 연결 한 건이 있다.
계정 식별자는 비공개 설정 파일에서 관리한다.

기존 `.local/web.firebase-test.pending.json`과 `.local/firebase-admin.json`은 이전 시험 프로젝트용이다.
새 프로젝트에 적용하지 않는다. 새 서버 자격 증명은
`.local/firebase-admin.mcp-cowork-pgl.json`에 준비되어 있다(0600, Git 제외).
계정은 `cowork-hub-web@mcp-cowork-pgl.iam.gserviceaccount.com`, 역할은
`roles/firebaseauth.viewer`로 로그인 계정 상태 조회에 필요한 읽기 권한만 사용한다.
2026-09-28 새 자격 증명으로 Admin SDK의 계정 조회에 성공했고, 첫 Google 계정을 `ezy`에 연결했다.
IAM 재조회에서 이 계정의 프로젝트 직접 부여 역할은 위 조회 역할 하나이며,
활성 사용자 생성 키가 정확히 하나임을 확인했다. 검증 결과는 비공개 `.local/verification/firebase/mcp-cowork-pgl/`에 보관한다.
이전 프로젝트의 계정·권한·키는 변경하지 않았다.

### 공통 설정 절차

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
기존 `users` 매핑은 관리자 설정 파일에서 관리하며 변경 후 허브를 재시작한다.
추가 사용자의 Google 계정 연결은 관리자 화면에서 DB의 `web_accounts`에 저장하므로 재시작이 필요 없다.
`admin_users`에는 웹 관리자로 허용한 허브 사용자 ID를 지정한다. 일반 사용자는 이 설정을 변경할 수 없다.
웹에서 최초 계정 등록·승인·SSH 컨테이너 연결과 승인·서버 이용 제한을 관리하는 절차는 [서버 관리 안내](server-platform.md)를 따른다.

## 관리자: 허브 적용

현재 연구실의 PostgreSQL 허브에는 `scripts/enable_web.py`를 사용할 수 있다.
새 프로젝트용 인증 키가 준비된 뒤 **실제 허브 Compose 호스트**에서 실행한다.
기본 입력은 `.local/web.firebase.pending.json`과 `.local/firebase-admin.mcp-cowork-pgl.json`이며
두 파일은 실행 사용자 소유·권한 0600이어야 한다. 이전 시험 프로젝트의 키는 거절한다.

```sh
# 기존 허브·SDK·프로젝트·계정 매핑·DB 확인만 수행
python3 scripts/enable_web.py

# 위 확인이 성공한 뒤 백업·설정 적용·허브만 재시작
python3 scripts/enable_web.py --apply
```

도구는 기존 허브가 실행 중이지 않으면 새 허브를 생성하지 않고 중단한다.
PostgreSQL과 기존 Compose 설정을 `.local/backups/`의 새 파일에 저장하고,
웹 설정·키를 각각 `/data/web.mcp-cowork-pgl.json`, `/data/firebase-admin.mcp-cowork-pgl.json`에
권한 0600으로 설치한다. 기존 키를 교체하지 않으며 웹 설정을 수정할 때는 이전 파일을 보관한다.
Discord·포트·기존 볼륨 설정을 유지하면서 `.local/compose.local-runner.yaml`에 두 환경변수를 추가한다.
이미 실행 중인 이미지로 허브만 재시작하므로 웹 SDK가 없는 이미지는 먼저 기존 업데이트 절차로 준비한다.
DB 덤프 성공과 비어 있지 않음을 확인하지만 별도 DB로의 복원 시험까지 수행하지는 않는다.
실패 시 출력한 단계와 운영 허브 상태를 확인하고, 무조건 재실행하거나 백업으로 덮어쓰지 않는다.

초기 `users: {}` 상태에서는 Google 로그인 후 허브 계정 미연결 안내만 보이고 토큰을 발급할 수 없다.
본인 Firebase UID를 확인해 `users`에 연결한 다음 같은 도구로 설정을 다시 적용한다.
이 도구는 Google 계정 허용 여부를 스스로 판단하거나 IAM 권한·키를 생성하지 않는다.

2026-09-28 실제 호스트에서 웹 인증 활성화와 `ezy` 연결을 적용했다. 같은 날 계정별 토큰 이름 예시와
빈 입력 시 예시 이름을 사용하는 동작도 반영했다. 이 토큰 화면 변경 적용 전 DB 백업은
`.local/backups/cowork-before-web-20260928T051025695742Z.dump`이며 `pg_restore --list`로
목록을 읽을 수 있음을 확인했다. 별도 DB 복원 시험은 수행하지 않았다.
허브만 재시작했고 PostgreSQL·사용자 계산 컨테이너는 재시작하지 않았다.
실제 Chromium에서 Google 로그인 버튼 활성화와 페이지 오류·실패 요청 0건을 확인했다.
이 브라우저 점검은 로그인 준비 확인이며 실제 토큰 다운로드 검증을 대신하지 않는다.
계정별 이름 변경은 시험 API를 사용하는 Chromium에서 빈 입력·공백·직접 입력·계정 변경·로그아웃을
확인했다. 운영 허브가 수정된 화면 파일을 제공하는지와 기존 작업 2개의 상태 보고도 확인했다.

공개 웹 외에 SSH로 내부 화면의 로그인을 검증할 수도 있다. 이때 허브 설정의
`api_base_url`은 비워 두고, 브라우저를 사용하는 PC 터미널에서 다음 연결을 유지한다.

```sh
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18080:192.168.10.41:8080 \
  -p 2244 ezy@203.255.11.226
```

PC에서 `http://localhost:18080/web/`를 열어 로그인한다. 이 경로는 개인 검증용이며
Firebase Hosting에서 모두가 접근하는 공개 HTTPS 허브 연결을 대신하지 않는다.
Google 계정 비밀번호·ID 토큰·개인 토큰을 채팅에 보내지 않고 계정 연결에는 Firebase UID만 사용한다.

Docker 이미지에는 서버용 `web` 선택 의존성을 포함했다. 기존 클라이언트 설치에는 Firebase SDK가
추가되지 않는다. Python으로 허브를 직접 실행할 때는 `web` extra를 포함해 설치한다.
계정 등록 화면 확장으로 현재 DB 스키마는 6이다. 기존 사용자·토큰·작업은 보존하며 관리용 테이블만 추가한다.

허브 프로세스에 다음 환경변수를 설정한다. **경로는 허브 컨테이너 안에서 읽을 수 있는 경로**다.
서비스 계정 파일과 관리자 설정 파일은 허브 실행 사용자만 읽을 수 있게 준비한다.

```text
HUB_WEB_CONFIG=/data/web.json
GOOGLE_APPLICATION_CREDENTIALS=/data/firebase-admin.json
```

현재 연구실에서는 기존 `.local/compose.local-runner.yaml`의 `services.hub.environment`에 이 두 값을
추가하면 기존 업데이트 도구에서도 유지된다. Discord 설정과 기존 볼륨을 보존한다.
파일을 준비한 뒤 실제 허브 호스트에서 적용한다. 현재 운영 허브는 PostgreSQL을 사용하므로
[PostgreSQL 백업·업데이트 절차](postgresql.md#전환-후-재시작업데이트백업)에 따라 백업을 확인한 뒤
기존 로컬 설정과 PostgreSQL Compose 파일을 함께 사용한다.

```sh
docker compose -f compose.yaml -f .local/compose.local-runner.yaml -f compose.postgres.yaml up -d --build --wait hub
```

허브 프로세스는 시작할 때 웹 설정을 읽는다. 기존 이미지에 웹 기능이 있고 `/data/web.json`이나
서비스 계정 파일 내용만 바꾼 경우에도 허브를 재시작해야 한다.

아래 이전 명령은 **아직 SQLite를 사용하는 설치에만** 적용한다. PostgreSQL 허브에서는
SQLite 백업 단계가 거절되므로 사용하지 않는다.

```sh
python3 scripts/enable_local_runner.py --user ezy \
  --config /10Gdata/ezy/01_Programs/cowork-mcp/.local/runner-226.json --upgrade
```

Firebase Hosting에 페이지를 올리는 것만으로
내부망 `http://192.168.10.41:8080`이 외부에서 접근 가능해지거나 HTTPS로 바뀌지는 않는다.
현재는 nginx가 `https://203.255.11.226`의 443 포트에서 웹 API만 내부 허브로 전달한다.
IP 인증서와 자동 갱신을 적용했으며, 브라우저는 Firebase 함수·SSH를 거치지 않고 연결한다.
접근 범위는 기존 허브의 인증·권한 검사를 따른다.

허브와 같은 HTTPS 주소에서 제공하려면 `/web/`로 접속한다. 이 경우 `api_base_url`은 빈 문자열로
두어 같은 주소를 사용할 수 있으며 다른 웹 출처를 쓰지 않으면 `allowed_origins`도 빈 목록으로 둔다.
HTTP는 로컬 개발의 `localhost`/`127.0.0.1`에서만 허용한다.

### 이전 직접 HTTPS 연결 검토 기록 (2026-09-23)

아래는 당시 적용하지 않았던 8443 포트 검토 기록이다. 현재 운영은 2026-10-05에 적용한
443 포트 직접 연결이며, [현재 운영 안내](firebase-hosting.md)를 따른다.

2026-09-23에는 Firebase Hosting·Google 로그인과 연구실 서버의 HTTPS API를 직접 연결하는 방식을 검토했다.
허브 호스트의 공인 IP는 사용자 확인값 `203.255.11.226`이며, 내부 허브 주소는
`http://192.168.10.41:8080`이다. 웹에서 사용할 API 주소의 후보는
`https://203.255.11.226:8443`이다. 포트 사용 여부와 방화벽·외부 접근은 적용 전에 확인한다.
PostgreSQL은 기존 내부 연결을 유지한다.

당시 컨테이너에서 확인한 결과 내부 허브는 `status=ok`, `database=postgresql`을 반환했다.
공인 IP의 80번은 Nginx에서 HTTP 502를 반환했고 8443번은 연결을 거절했다.
이는 관측 결과이며 8443번이 호스트에서 비어 있다는 확인이나 외부 인터넷에서의 접속 검증은 아니다.

80번의 기존 Nginx에 `/.well-known/acme-challenge/` 경로를 추가할 수 있다면
HTTP-01로 IP 인증서를 발급·갱신하고, 허브 HTTPS는 별도 8443번에서 제공한다.
HTTP-01 검증의 외부 포트는 80번으로 고정되며 8443번으로 대체하거나 리디렉션하지 않는다.
Certbot webroot 방식의 IP 인증서는 5.4 이상을 사용하고, 6일 유효기간에 맞춘 자동 갱신과
갱신 후 Nginx 인증서 재읽기를 함께 설정한다.
기존 Nginx의 실행 위치·설정 파일과 공유 webroot 경로를 확인하기 전에는 적용하지 않는다.
사용자는 해당 80번 서비스를 다른 사람이 사용 중이라고 추가로 설명했다. 기존 Nginx 설정은
변경하지 않았으며, 80번 인증 경로를 사용할 수 있다고 가정하지 않는다.
80·443 모두 사용할 수 없다면 위 직접 HTTPS 방식의 IP 인증서 발급 조건을 충족하지 못하므로
연결 방식부터 다시 정한다. 8443번을 열기만 하는 것으로 인증서 문제를 해결할 수는 없다.
[인증 방식](https://letsencrypt.org/docs/challenge-types/)과
[IP 인증서·자동 갱신 안내](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)를 따른다.

## Firebase Hosting으로 화면 배포

직접 API 연결 배포는 아래 정적 Hosting 절차를 따른다.
운영 값과 검증 절차는 [별도 안내](firebase-hosting.md)에 있다.
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
MCP 토큰 원문은 페이지 메모리에서만 유지하며 브라우저 localStorage/sessionStorage에 저장하지 않는다.
Google 로그인은 Firebase의 `browserLocalPersistence`로 브라우저를 닫았다 다시 열어도 유지한다.
Firebase SDK가 인증 상태를 로컬 저장소에 보관하고 갱신한다. 직접 로그아웃하거나 사이트 데이터를
삭제하면 해제되며, 시크릿 모드나 브라우저 종료 시 사이트 데이터 삭제 설정에서는 유지되지 않을 수 있다.
[Firebase 인증 유지 안내](https://firebase.google.com/docs/auth/web/auth-state-persistence)를 참고한다.

실제 운영 적용 완료 기준은 Google 로그인 성공, 미허용 계정 차단, 첫 토큰 다운로드,
다운로드한 파일을 사용한 MCP 인증 성공과 폐기 후 인증 거절이다.
자동 테스트의 외부 Google 로그인은 시험용으로 대체하므로 이 운영 확인을 대신하지 않는다.

2026-09-21 검증: 전체 Python 테스트 113개와 정적 검사 통과. 임시 허브·Chromium에서 첫 발급,
다운로드 파일로 인증, 모바일 배치, 이름의 HTML 삽입 방지, 로그아웃 후 원문 제거,
재로그인 시 기존 원문 비노출, 폐기 후 인증 거절을 확인했다. 당시에는 운영 허브에 배포하지 않았다.
2026-09-28 운영 적용과 검증 범위는 이 문서 상단의 현재 상태를 따른다.
