# Firebase Hosting과 SSH 중계

웹은 Firebase Hosting에서 제공하고 `/v1/web/**` 요청만 Cloud Functions 2세대의
`coworkWeb` 함수로 전달한다. 함수는 Firebase ID 토큰의 서명·프로젝트·만료와 Google
로그인을 확인한 뒤, SSH로 호스트의 고정된 중계 프로그램을 실행한다. 허브는 기존대로
계정 중지·토큰 폐기·사용자 연결·각 관리 권한을 다시 확인한다.

```text
브라우저 → Firebase Hosting → coworkWeb → SSH 2244 → 호스트 중계 → 내부 허브 8080
```

허브·PostgreSQL·실행기는 기존 연구실 서버에서 계속 실행된다. MCP와 계산 실행기는
이 공개 웹 중계를 사용하지 않는다. 계산 시간이나 작업 대기 시간 동안 Functions를
열어두지 않으며, raw 데이터 전송도 이 경로에 포함하지 않는다.

## 실행 및 비용 설정

`functions/index.js`가 설정의 기준이다.

| 설정 | 초기값 |
| --- | --- |
| 지역 | `asia-east1` (Hosting 연동 지원, 한국 서버와 가까운 지역) |
| 런타임 | Node.js 22 |
| CPU / 메모리 | 1 CPU / 512 MiB |
| 최소 / 최대 인스턴스 | 0 / 1 |
| 인스턴스당 동시 요청 | 8 |
| 함수 제한 시간 | 20초 |
| SSH 연결 제한 시간 | 5초 |
| 호스트 중계 제한 시간 | 12초 |

최소 인스턴스 0은 상시 대기 비용을 줄인다. 최대 인스턴스 1은 동시 확장을 제한하는
설정이며 월 청구액 상한이 아니다. 요청이 몰리면 대기하거나 거절될 수 있다.
첫 주의 호출 수·청구된 실행 시간·전송량으로 월 비용을 추정한다. SSH 연결은 실행 중인
인스턴스에서 재사용하지만, 인스턴스를 유지하기 위한 타이머나 별도 호출은 만들지 않는다.
현재 빌드 이미지는 자동 삭제하지 않는다. 자동 승인 검토가 3일 보관 후 삭제 정책을
명시적 삭제 승인 부족으로 거절했으므로, 이미지 보존을 선택했다. 반복 배포 때 쌓이는
Artifact Registry 저장량도 비용 확인 대상이다.

## SSH 권한

기존 개인 SSH 비밀키를 클라우드로 복사하지 않는다. 중계 전용 Ed25519 키를 새로 만들고
Google Secret Manager의 `COWORK_WEB_SSH`에 다음 정보를 JSON으로 저장한다.

```json
{
  "host": "SSH_HOST",
  "port": 2244,
  "username": "SSH_USER",
  "privateKey": "DEDICATED_PRIVATE_KEY",
  "hostSha256": "VERIFIED_ED25519_HOST_KEY_SHA256_HEX"
}
```

호스트 키는 이미 신뢰한 `known_hosts`의 Ed25519 키로 검증한다. 함수는 그 키와 일치하는
호스트에만 연결한다. 전용 런타임 서비스 계정에는 이 비밀 하나의 `secretAccessor`만 부여하고
허브용 Firebase Admin 비밀키나 프로젝트 편집 권한을 주지 않는다.

호스트에 `scripts/ssh_web_relay.py`의 검토된 복사본을 설치한 뒤 `authorized_keys`에
다음 형태로 **공개키**를 추가한다. 기존 키는 보존하고 파일을 백업한다.

```text
restrict,command="/usr/bin/python3 /ABSOLUTE/PATH/ssh_web_relay.py --host HUB_INTERNAL_IP --port 8080" ssh-ed25519 PUBLIC_KEY cowork-firebase-web-relay
```

`restrict`로 TCP 포워딩·에이전트 포워딩·PTY 등을 막고, 강제 명령으로 실행 프로그램을
고정한다. 프로그램은 `SSH_ORIGINAL_COMMAND`를 실행하지 않으며 사용자가 목적지를
지정할 수 없다. `/v1/web/` 경로와 GET·POST·DELETE만 처리한다. 요청 본문은 64 KiB,
허브 응답은 4 MiB로 제한한다. 개인 토큰·인증 헤더·본문은 로그에 남기지 않는다.

연결 오류 시 쓰기 요청을 자동 재시도하지 않는다. 토큰 발급·권한 변경 중 응답을 잃으면
목록에서 반영 여부를 먼저 확인한다.

## 빌드와 배포

Blaze 결제 연결, Firebase CLI 로그인, Google 로그인 제공자와 Hosting 도메인 허용,
위 전용 키·호스트 프로그램·Secret Manager·런타임 계정을 먼저 준비한다.

```sh
npm ci --prefix frontend
npm run build --prefix frontend
npm ci --prefix functions
npm test --prefix functions
.venv/bin/pytest -q tests/test_ssh_web_relay.py tests/test_web.py
.venv/bin/python scripts/build_web.py \
  --config .local/web.firebase.pending.json \
  --output .local/firebase-release-NEW --relay
```

출력은 새 디렉터리여야 한다. 먼저 React·Vite 프런트엔드를 빌드한다.
`public/`에는 해시가 붙은 JS/CSS, 공개 웹 설정과 설치 프로그램만 들어가며,
허브의 사용자 연결·관리자 목록·서비스 계정 키는 포함하지 않는다. `functions/`는
별도 배포 소스다. 그 아래 `.env.PROJECT_ID`에는 비밀이 아닌 런타임 계정만 지정한다.

화면 수정만 배포할 때는 `--only hosting`을 사용한다. 프런트엔드 개발·검증은
[프런트엔드 안내](../frontend/README.md)를 참고한다.

```text
RELAY_SERVICE_ACCOUNT=cowork-web-relay@PROJECT_ID.iam.gserviceaccount.com
```

출력 디렉터리에서 의존성을 준비하고 명시한 프로젝트에 배포한다.

```sh
npm ci --prefix functions
firebase deploy --project PROJECT_ID --only functions:cowork-web,hosting --non-interactive
```

배포 후 Hosting `/`, 공개 `config.json`, API 익명·잘못된 토큰 거절, Functions의
실제 인스턴스 제한과 런타임 계정을 확인한다. 사용자가 Google로 로그인해 허용 서버
목록이 보이는지도 확인한다. 이 마지막 확인은 익명 요청 테스트로 대체할 수 없다.

중계를 중지할 때는 해당 함수만 중지/삭제하고, 중계 전용 SSH 공개키와 Secret Manager
권한을 회수한다. 기존 SSH 키·허브·DB·계산 컨테이너는 이 작업의 대상이 아니다.

## 2026-09-29 적용 기록

- `mcp-cowork-pgl`의 [공개 웹](https://mcp-cowork-pgl.web.app)과 `coworkWeb` 함수를 배포했다.
- 실제 함수 설정에서 1 CPU·512 MiB, 최소 0·최대 1, 동시 요청 8, 제한 시간 20초를 확인했다.
- 런타임 계정의 프로젝트 전체 역할은 없으며 중계 비밀 하나에만 읽기 권한을 부여했다.
- Python 웹·중계 테스트 22개, Node 중계 테스트 5개가 통과했다.
- 전용 SSH 키로 허브의 401 응답, 비웹 API 차단, TCP 포워딩 차단을 확인했다.
- 공개 웹에서 익명·잘못된 토큰은 401, 비웹 API는 404였다. Chromium에서 로그인 버튼 활성화,
  페이지 오류 0건·실패 요청 0건을 확인했다. 내부 허브 상태는 PostgreSQL `ok`였다.
- 사용자 Google 계정으로 로그인한 뒤 서버 목록을 조회하는 전체 경로는 사용자 확인을 요청한 상태다.
  위 익명 요청·로그인 화면 검증을 인증된 서버 목록 조회 성공으로 해석하지 않는다.
- 배포·브라우저 검증 기록은 비공개 `.local/firebase-hosting-relay/deploy-final.log`와
  `.local/verification/firebase-hosting/report.json`에 보관한다.

참고: [Hosting–Functions 연결](https://firebase.google.com/docs/hosting/functions),
[함수 실행 설정](https://firebase.google.com/docs/functions/manage-functions),
[비밀 설정](https://firebase.google.com/docs/functions/config-env).
