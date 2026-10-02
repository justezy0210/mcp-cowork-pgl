# 메인 컨테이너 연결 프로그램 설치

`cowork-connector`는 사용자 웹의 SSH 등록 요청을 처리하는 일반 Python 프로그램이다.
LLM·MCP 세션 없이 요청을 받아 기존 사용자 SSH로 대상을 검사·등록하고 메인 실행기에 경로를 저장한다.
계산 명령을 받거나 실행하지 않는다. 계산 제출·대기·종료 알림은 기존 MCP·실행기가 담당한다.

처음 사용하는 구성원은 계정·토큰 준비와 첫 작업 실행까지 포함한 [신규 사용자 매뉴얼](first-user-guide.md)을 먼저 따른다.

## 간편 설치 프로그램

[웹 설치 안내](https://mcp-cowork-pgl.web.app/#guide)에서 환경에 맞는 명령 하나를 복사해 실행한다.
단일 Python 설치 파일이 소스와 스킬을 포함하므로 Git은 필요 없다. 서버·프로그램을 선택하면
설치 명령이 바로 표시된다. 개인 토큰은 같은 설치 화면에서 발급·복사해
설치 프로그램의 숨김 입력에 붙여 넣는다. 기존 토큰 파일은 `--token-file /절대/경로/user.token`으로 사용할 수 있다.
파일은 본인 소유의 권한 `600` 일반 파일이어야 한다. 토큰 값 자체를 명령 인자에 넣지 않는다.

프로그램이 계정·권한 확인 → 설치 → 환경 등록·MCP·스킬 연결 → 연결 확인까지 진행한다.
Linux·Python 3.12 이상과 선택한 Codex/Claude는 미리 준비한다. 의존성 다운로드에는 인터넷이 필요하다.
기존 설정과 MCP 등록을 보존하며 환경 등록·알림 준비가 끝나지 않았다면 마지막에 안내한다.
설치는 본인 계정으로 실행한다. 컨테이너에 기존 Supervisor가 있으면 자동 시작 서비스 등록까지
수행하며, 서비스 설정에 관리자 권한이 필요할 때만 `sudo` 인증을 요청한다. 연결 프로그램은
본인의 UID/GID로 실행된다. 설치 전체에 `sudo`를 붙이지 않는다.
등록 성공 후에는 컨테이너의 Supervisor 시작 시 자동 실행되고, 비정상 종료 후에도 재실행된다.
지원하는 Supervisor 설정이 없으면 `자동 시작 미설정`과 수동 시작 명령을 표시한다.
`SUPERVISOR_NOT_CONFIGURED`는 계정 승인과 별개인 자동 시작 설정 문제이며, `sudo` 재시도로 해결되지 않는다.
이 경우 컨테이너 관리자가 실제 시작 방식에 맞춰 연결 프로그램을 등록해야 한다.
Supervisor가 있고 관리자 인증만 마치지 못한 경우에는 `sudo` 등록 재시도 명령을 표시한다.
`자동 시작 등록 완료`를 확인한 경우 재시작 때마다 명령을 실행할 필요가 없다.

기존 간편 설치에는 같은 설치 파일을 `python3 cowork-setup.pyz --root /기존/설치/폴더 --autostart-only`로
실행해 자동 시작만 추가할 수 있다. 프로그램 재설치·새 토큰 입력·환경 재등록은 수행하지 않는다.
관리자가 별도로 시작 절차를 관리하는 환경에서는 `--no-autostart`로 등록을 생략할 수 있다.

배포 파일은 `python3 scripts/build_installer.py --output dist/cowork-setup.pyz`로 만든다.
웹 빌드에도 자동 포함되며, 비공개 `.local` 파일·토큰·서버 설정은 포함하지 않는다.

### 환경 등록과 GPU 목록

현재 운영 환경은 각 컨테이너에서 해당 서버의 모든 GPU에 접근할 수 있다는 전제를 사용한다.
메인·SSH 환경을 새로 등록할 때 GPU 목록은 허브에 등록된 해당 서버의 장치 목록을 그대로 사용한다.
설치·환경 등록 과정에서는 `nvidia-smi`를 실행하거나 GPU UUID·드라이버·NVML을 검사하지 않는다.
GPU가 없는 서버는 빈 GPU 목록으로 등록한다. UID/GID·서버 권한·자원 검사를 통과하면 추가 승인 없이 `READY`로 등록한다.
설치 완료는 실제 CUDA 작업의 정상 동작을 검증한 결과를 뜻하지 않는다.

기존 실행기 설정은 재사용하며 저장된 GPU 목록을 자동 변경하지 않는다. 이전 설치 파일이
GPU 조회 오류로 중단됐다면 수정된 설치 파일로 같은 `--root`에 재설치한다.

### 같은 폴더에 수정본 재설치

웹에서 **기존 설치 업데이트**를 선택하고 기존 설치 폴더만 입력하면 된다.
다운로드한 설치 파일은 `python3 cowork-setup.pyz --root /기존/설치/폴더 --update`로 실행할 수 있다.
저장된 실행기 설정에서 서버·허브·토큰 경로를 읽고 MCP·스킬 등록은 그대로 둔다.
업데이트에는 Codex/Claude 명령이 PATH에 없어도 되지만, 실행 중인 MCP 프로세스는 이후 다시 연결해야 한다.
환경 등록 전에 중단된 설치는 기존 서버·프로그램을 선택한 처음 설치 명령으로 다시 진행한다.

같은 버전 번호의 수정본도 기존 설치 폴더에 적용할 수 있다. 설치 기록으로 확인된 Cowork
소스와 가상환경을 갱신하며, 기존 토큰·실행기 설정·환경 ID·MCP 실행 경로는 유지한다.
소스 교체 중에는 기존 내용을 임시 보관하고 교체가 끝나면 삭제하므로 이전 소스가 계속 쌓이지 않는다.
교체 도중 실패하면 기존 소스를 복원한다. 패키지 설치 중 실패하면 같은 명령으로 이어서 설치한다.
설치 기록이 없거나 다른 소스 경로에서 만든 프로그램 폴더는 자동으로 변경하지 않는다.

재설치 후 연결 프로그램을 다시 시작해 수정본을 적용한다. 이미 실행 중인 에이전트의 MCP
프로세스는 해당 에이전트에서 MCP를 재연결해야 새 코드를 사용한다. 실행 중인 계산 작업은 중지하지 않는다.

## 현재 구현 범위

- 설치 프로그램, 시작·상태·종료 CLI, 기존 Supervisor에 자동 시작 등록과 실행 스크립트 생성.
- 기존 메인 실행기 설정 재사용 또는 개인 토큰으로 메인 환경 신규 등록.
- 사용자별 연결 상태와 등록 요청 API, 요청 중복 방지, 처리 임대·결과 저장·재전송.
- 개인 토큰 추가 발급·목록·개별 폐기 API, 이전 클라이언트 호환용 관리자 승인 API. 새 등록에는 추가 승인이 없다.
- 배포용 소스 압축파일과 SHA-256 파일 생성. `.local`, 토큰, SSH 키, 가상환경은 배포하지 않는다.

소스 배포 저장소는 [justezy0210/mcp-cowork-pgl](https://github.com/justezy0210/mcp-cowork-pgl)이다.
Google 로그인·토큰 발급/다운로드 화면과 별도 웹 인증 API를 구현했다. [웹 토큰 안내](web-tokens.md)를 참고한다.
Firebase Hosting·SSH 중계와 SSH·관리자 화면을 적용했다. [공개 웹](https://mcp-cowork-pgl.web.app)의
MCP 설치·사용 안내를 따를 수 있다. 버전별 GitHub Release 첨부 파일은 아직 게시하지 않았다.
기존 연결 프로그램 API는 사용자·관리자 Bearer 인증을 사용한다. 웹 로그인만으로 허브 사용자 생성이나 서버 권한 부여를 하지 않는다.

## 현재 운영 적용 상태 (2026-09-21)

운영 허브 `http://192.168.10.41:8080`에서 `connector: 1`, `personal_tokens: 1`을 확인했다.
226 메인 컨테이너의 `ezy-226` 연결 프로그램을 시작했고 허브에서 온라인으로 확인했다.
기존 229 환경에 대한 등록 요청을 API로 제출해 연결 프로그램이 LLM 호출 없이 실제 SSH 검사와
경로 저장을 마치는 것을 검증했다. 요청은 `REGISTERED`로 완료됐고 기존 환경 ID를 재사용했다.
224·226·228·229의 기존 네 환경은 모두 `READY`를 유지했다. 계산 작업은 제출하지 않았다.

- 실행 파일: `/10Gdata/ezy/01_Programs/cowork-mcp/.local/client-release/venv/bin/cowork-connector`
- 설정: `/10Gdata/ezy/01_Programs/cowork-mcp/.local/connector-226.json`
- 로그: `/10Gdata/ezy/01_Programs/cowork-mcp/.local/connector-226.log`
- 검증 기록: `.local/verification/connector-226-live-20260921T002345436819Z.json`

에이전트·터미널 종료 후에도 실행하지만, 컨테이너 재시작 후 자동 실행은 아직 설정하지 않았다.
`.local/connector-226.startup.sh`는 생성했으며 컨테이너의 시작 절차에는 연결하지 않았다.
같은 226 컨테이너에서 다시 시작할 때는 다음을 실행한다.

```sh
bash /10Gdata/ezy/01_Programs/cowork-mcp/.local/start-connector-226.sh
```

## 설치 전제

Linux와 Python 3.12 이상이 필요하다. 기존 SSH 키와 알려진 호스트 설정을 사용한다.
설치 경로·개인 설정·토큰 파일이 SSH 대상에서도 같은 경로로 보이고 실행 가능해야 한다.
이 버전은 다른 NFS 마운트 경로를 변환하거나 대상에 Python을 설치하지 않는다.
가상환경에는 기반 Python이 필요하므로 대상에서도 해당 인터프리터를 사용할 수 있어야 한다.
[Python 가상환경 문서](https://docs.python.org/3/library/venv.html)를 참고한다.

## 1. 프로그램 설치

GitHub 저장소를 내려받은 디렉터리에서 실행한다. 버전별 Release 첨부 파일은 아직 게시하지 않았다.
아래 경로는 예시다. 사용자의 공유 디렉터리에 새 설치 경로를 선택한다.

```sh
git clone https://github.com/justezy0210/mcp-cowork-pgl.git /shared/my-user/cowork-source
cd /shared/my-user/cowork-source
python3 scripts/install_connector.py --prefix /shared/my-user/cowork-client
```

비공개 가상환경에 해시가 고정된 런타임 의존성과 프로그램을 설치한다. 시스템 Python과 기존
설정은 수정하지 않는다. 이미 완료한 같은 설치는 재사용한다. 같은 소스 경로의 수정본이나
중단된 설치는 설치 기록을 확인한 뒤 같은 `--prefix`에서 갱신·재시도한다. 설치 기록이 없는
기존 폴더는 보존한다. 수동 설치로 갱신했다면 연결 프로그램과 MCP를 재시작해 수정본을 적용한다.

인터넷이 없는 환경은 `--wheelhouse /path/to/wheels`로 의존성과 빌드 도구가 들어 있는 디렉터리를 지정한다.
가상환경·설정은 설치 압축파일에 포함하지 않는다.

## 2. 허브 업데이트

허브의 `/v1/capabilities`에 `connector: 1`, `personal_tokens: 1`이 필요하다.
새 테이블을 추가하는 스키마 4는 기존 사용자 토큰·작업·예약을 보존한다.
업데이트 전 백업을 만들며, 이전 바이너리로 되돌릴 때는 해당 백업으로 복원해야 한다.

현재 ezy 운영 환경은 허브 호스트의 프로젝트 디렉터리에서 다음 기존 도구로 업데이트할 수 있다.
현재 승인된 226의 설정을 재확인하며 계산 컨테이너를 생성하거나 재시작하지 않는다.

```sh
python3 scripts/enable_local_runner.py --user ezy \
  --config /10Gdata/ezy/01_Programs/cowork-mcp/.local/runner-226.json --upgrade
```

## 3. 본인 계정에 연결

기존 메인 MCP 설정을 재사용하는 경우:

```sh
/shared/my-user/cowork-client/venv/bin/cowork-connector configure \
  --runner-config /shared/my-user/runner.json \
  --config /shared/my-user/cowork/connector.json
```

새 사용자는 웹에서 다운로드하거나 관리자가 제공한 개인 토큰을 본인 소유 `0600` 일반 파일로 준비한 뒤 다음을 사용한다.
웹 발급은 [Google 로그인 운영 설정](web-tokens.md)을 먼저 적용해야 한다.
토큰 값은 명령 인자에 넣지 않는다. 허브에 해당 사용자의 UID/GID와 서버 권한이 먼저 등록돼 있어야 한다.

```sh
/shared/my-user/cowork-client/venv/bin/cowork-connector configure \
  --hub-url https://hub.example.org \
  --token-file /shared/my-user/private/user.token \
  --node 226 --workdir /shared/my-user/cowork \
  --config /shared/my-user/cowork/connector.json
```

설정 디렉터리는 `0700`, 설정 파일은 `0600`으로 작성한다. 메인 컨테이너가 새 환경이면
검사를 통과하면 `READY`로 자동 등록한다. 출력의 `connector_id`를 웹 등록 요청에 사용한다.
다른 컨테이너에 메인 설정을 복사해 사용하지 않는다. 사용자 UID/GID와 컨테이너가 일치해야 한다.
`configure --workdir`는 환경 등록용 경로다. 기존 설치 폴더처럼 계속 존재하는 경로를 사용하면 된다.
계산은 MCP 제출 시 각 작업의 `spec.workdir`, 수동 CLI에서는 명령을 실행한 현재 폴더에서 수행한다.
프로젝트를 바꿀 때 이 등록값이나 MCP 설치 설정을 변경하지 않는다.

## 4. 실행과 컨테이너 재시작

간편 설치에서 자동 시작 등록을 마쳤다면 Supervisor가 연결 프로그램을 관리한다. 기존
Supervisor를 재시작하거나 다른 서비스를 갱신하지 않고 Cowork 서비스만 추가한다. 허브 연결이
부팅 직후 준비되지 않아도 연결 프로그램이 재시도한다. 설정 충돌이 있으면 기존 파일을 보존하고 중단한다.

수동 설치 또는 기존 설치에도 다음 등록 도구를 사용할 수 있다. Python과 설정 경로는 기존
프로그램의 경로를 지정한다. 이 단계에만 관리자 권한을 사용한다.

```sh
sudo /usr/bin/python3 -I -S scripts/setup_autostart.py \
  --python /shared/my-user/cowork-client/venv/bin/python \
  --config /shared/my-user/cowork/connector.json
```

기본 지원 대상은 `/etc/supervisor/supervisord.conf` 또는 `/etc/supervisord.conf`가 `conf.d/*.conf`를
불러오는 기존 Supervisor 환경이다. 해당 Supervisor가 컨테이너의 시작 절차에서 실행되어야 한다.
연결 프로그램의 자동 시작은 중단된 계산의 자동 재개를 뜻하지 않는다.
Supervisor로 등록한 서비스의 중지·시작은 `sudo supervisorctl -c /etc/supervisor/supervisord.conf
stop|start 서비스이름`으로 관리한다. `cowork-connector stop`만 실행하면 Supervisor가 다시 시작한다.

아래는 Supervisor로 관리하지 않는 환경에서 직접 실행하는 명령이다.

```sh
/shared/my-user/cowork-client/venv/bin/cowork-connector start --config /shared/my-user/cowork/connector.json
/shared/my-user/cowork-client/venv/bin/cowork-connector status --config /shared/my-user/cowork/connector.json
/shared/my-user/cowork-client/venv/bin/cowork-connector stop --config /shared/my-user/cowork/connector.json
```

`start`는 별도 프로세스를 시작하고 반환한다. 에이전트나 터미널을 유지할 필요가 없다.
`status`는 로컬 프로세스 상태이며, 허브에서 보이는 `online`은 최근 통신 여부다.
프로세스가 살아 있다고 허브 연결에 성공했다는 뜻은 아니다. `stop`은 종료 요청이며 진행 중인
등록 처리를 마무리할 시간이 필요할 수 있다. 기존 계산 작업과 그 실행기는 중단하지 않는다.

컨테이너 재시작 후 자동 실행에 사용할 스크립트는 다음으로 생성한다.

```sh
/shared/my-user/cowork-client/venv/bin/cowork-connector startup --config /shared/my-user/cowork/connector.json
```

출력된 `connector.startup.sh`를 메인 컨테이너의 기존 시작 절차나 프로세스 관리자에 한 번 연결한다.
이 스크립트 생성 명령은 사용자의 Docker 설정·셸 시작 파일을 변경하지 않는다. 생성 결과의
`autostart_configured: false`는 실행 스크립트만 만들었다는 뜻이다. 상주 관리자에서는
`run --config ...` 또는 생성된 스크립트를 포그라운드 서비스로 실행하고 재시작 정책을 적용한다.
`run --once`는 요청 한 건 또는 결과 재전송 한 건만 처리하는 점검용 명령이다.

## 웹에서 사용할 API

| API | 역할 |
| --- | --- |
| `POST /v1/tokens` | 사용자 본인의 추가 토큰 발급: `{ "name": "main-container" }` |
| `GET /v1/tokens` | 본인 토큰 메타데이터만 조회 |
| `DELETE /v1/tokens/{id}` | 본인 토큰 하나 폐기 |
| `GET /v1/connectors` | 본인 연결 프로그램 목록·최근 통신·온라인 여부 |
| `POST /v1/registration-requests` | 선택한 연결 프로그램에 SSH 등록 요청 |
| `GET /v1/registration-requests` | 본인 요청 상태·등록 환경 ID·오류 코드 |
| `GET /v1/admin/local/environments/pending` | 이전 등록 중 검사에 통과하지 못한 환경 목록 (호환 API) |
| `POST /v1/admin/local/environments/{id}/approve` | 이전 클라이언트용 환경 등록 재검사 (호환 API) |

목록은 `limit`(최대 100)과 마지막 항목의 `id`를 `after`로 전달해 순회한다.
토큰 원문은 생성 응답에서 한 번만 반환하며 `Cache-Control: no-store`를 적용한다.
DB에는 해시만 저장하고 목록에는 원문·해시를 포함하지 않는다. 폐기는 다른 토큰과 기존 작업 예약에
영향을 주지 않는다. 기존 초기 발급 토큰도 계속 동작하며 새 토큰 목록·폐기는 이 API로 추가 발급한 토큰을 대상으로 한다.
실행 중인 프로그램이 사용하는 토큰을 폐기하면 이후 보고·허브 접속이 실패할 수 있으므로 새 토큰으로
설정을 교체한 후 이전 토큰을 폐기한다. 연결 프로그램은 다음 요청에서 토큰 파일을 다시 읽는다.

등록 요청 예시:

```json
{
  "request_key": "unique-registration-key",
  "connector_id": "connector-id-from-setup",
  "target": {
    "host": "203.255.11.224", "user": "ezy", "port": 11010,
    "node_id": "224", "workdir": "/10Gdata/ezy/01_Programs/cowork-mcp"
  }
}
```

등록은 `PENDING → RUNNING → REGISTERED` 또는 `FAILED`로 진행한다. `REGISTERED`는 SSH 검사와
메인 실행 경로 저장까지 완료했다는 뜻이며 환경 상태 조회와는 별도다. `READY`는 환경 목록에서 확인한다.
연결 프로그램이 꺼져 있으면 요청을 보존하고, 실행되면 접수 순서로 처리한다.
같은 키의 같은 요청은 같은 ID를 반환하고, 다른 내용은 거절한다. 처리 중 장애가 나면 120초 임대 만료 후
같은 요청을 다시 처리하며 기존 SSH 등록의 고정 인스턴스를 재사용한다. 연결 프로그램은 결과를 파일에
먼저 기록해 응답 유실·재시작 후 재전송한다. `FAILED`를 다시 시도하려면 원인을 해결한 뒤 새 요청 키를 사용한다.

## 배포 파일 만들기

```sh
python3 scripts/build_client_release.py --output dist
```

생성된 `.tar.gz`와 `.sha256`을 [배포 저장소](https://github.com/justezy0210/mcp-cowork-pgl)의
Release 첨부 파일로 게시할 수 있다. 현재 설치 안내는 소스 저장소를 내려받는 방식이다.
[GitHub Releases 안내](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)를 참고한다.
