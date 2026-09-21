# 처음 사용하는 연구실 구성원 안내

2026-09-21 구현 기준이다. **관리자에게 계정·서버 권한·첫 개인 토큰을 받은 뒤, 본인의 메인 컨테이너에 설치한다.**
소스는 [GitHub 저장소](https://github.com/justezy0210/mcp-cowork-pgl)에서 받는다.
현재 웹 가입·토큰 발급 화면은 준비되지 않았다. 아래는 지금 사용할 수 있는 절차다.
메인 컨테이너는 평소 에이전트를 켜거나 작업을 제출하는 개인 컨테이너를 뜻한다.

## 1. 관리자에게 사용 등록 요청

다음 정보를 관리자에게 전달한다. 비밀번호·SSH 개인키를 보내는 절차는 없다.

| 항목 | 전달할 내용 |
| --- | --- |
| 사용자 | 본인의 허브 사용자 ID, 컨테이너 계정 이름 |
| UID/GID | 메인 컨테이너에서 `id -u`, `id -g`로 확인한 값 |
| 사용할 서버 | 예: `226`, `229`. 실제 사용할 서버만 요청 |
| 메인 환경 | 메인 컨테이너가 있는 물리 서버 ID |
| 공유 경로 | 본인 소유 NFS 디렉터리와 작업 디렉터리의 절대 경로 |
| Discord | 같은 연구실 Discord 서버의 본인 알림 채널 |

동일 사용자의 UID/GID는 사용하는 모든 서버·컨테이너에서 일치해야 한다.
**다른 사람까지 같은 UID를 쓰는 규칙은 아니다.** 다른 사용자는 다른 UID를 사용하며,
GID는 연구실의 그룹 정책에 따라 공유할 수 있다. `ezy`의 UID나 설정을 그대로 복사하지 않는다.

관리자는 기존 허브에 사용자·UID/GID·허용 서버를 등록하고 첫 개인 토큰을 안전하게 전달한다.
계정 생성 API는 `POST /v1/admin/users`이며, `id`, `allowed_nodes`, `identity: {uid, gid}`를 받는다.
응답에 포함된 토큰 원문은 채팅·로그에 출력하지 않고 해당 사용자만 읽을 수 있는 파일로 전달한다.
이후 추가 토큰 발급 API는 기존 사용자 인증이 필요하므로 첫 계정 등록을 대신하지 않는다.

관리자는 본인 Discord 채널의 Webhook도 허브에 연결해야 한다. 현재 실행기는 알림 목적지가
설정되지 않으면 작업 제출을 거절한다. 사용자에게는 **허브 주소·설치 안내·개인 토큰 파일·서버 ID**를 전달한다.
사용자는 허브 컨테이너에 로그인하거나 Docker 관리 권한을 받을 필요가 없다.

## 2. 메인 컨테이너에서 설치 준비

본인 계정으로 실행한다. `sudo`로 설치하지 않는다. Linux와 Python 3.12 이상이 필요하다.

```sh
id -u
id -g
python3 --version
curl --fail --max-time 5 http://192.168.10.41:8080/healthz
```

허브 주소는 관리자가 안내한 주소를 사용한다. 현재 연구실 내부망 주소는 위와 같다.
허브 정상 응답은 연결 확인이며 개인 인증이나 환경 승인 완료를 뜻하지 않는다.

아래 `alice`, `226`, 경로는 예시다. **본인 경로와 실제 메인 서버 ID로 바꾼 뒤 같은 터미널에서 진행한다.**
`COWORK_ROOT`는 본인 소유 공유 디렉터리여야 한다. 메인과 SSH 대상에서 설치 경로·개인 설정·토큰·
작업 경로가 같은 절대 경로로 보이고, 설치에 사용한 기반 Python도 대상에서 실행 가능해야 한다.

```sh
export COWORK_ROOT=/10Gdata/alice/cowork
export COWORK_HUB_URL=http://192.168.10.41:8080
export COWORK_NODE=226
export COWORK_WORKDIR=/10Gdata/alice/work
export COWORK_BIN="$COWORK_ROOT/client-0.1.0/venv/bin"

umask 077
mkdir -p "$COWORK_ROOT/state" "$COWORK_WORKDIR"
chmod 700 "$COWORK_ROOT/state"
```

관리자가 전달한 **본인 개인 토큰 파일**을 `$COWORK_ROOT/state/user.token`에 준비한다.
본인 소유 일반 파일이어야 하며 심볼릭 링크는 사용할 수 없다. 내용은 출력하지 않는다.

```sh
chmod 600 "$COWORK_ROOT/state/user.token"
```

Git이 설치된 환경에서 소스를 본인 공유 경로로 내려받는다. 비공개 저장소라면 먼저 본인 GitHub 계정에
접근 권한을 받아야 한다. 아래 `source`는 새 디렉터리여야 한다.

```sh
git clone https://github.com/justezy0210/mcp-cowork-pgl.git "$COWORK_ROOT/source"
export COWORK_SOURCE="$COWORK_ROOT/source"
python3 "$COWORK_SOURCE/scripts/install_connector.py" \
  --prefix "$COWORK_ROOT/client-0.1.0"
```

`installed: true`가 성공 기준이다. 인터넷 연결이 없으면 관리자가 준비한 의존성 파일 디렉터리를
`--wheelhouse`로 지정하고 소스도 관리자가 파일로 제공해야 한다.
현재 버전별 GitHub Release 첨부 파일은 게시하지 않았다. 자세한 전제는 [설치 안내](connector-setup.md)를 참고한다.

## 3. 메인 환경 등록과 연결 프로그램 시작

```sh
"$COWORK_BIN/cowork-connector" configure \
  --hub-url "$COWORK_HUB_URL" \
  --token-file "$COWORK_ROOT/state/user.token" \
  --node "$COWORK_NODE" --workdir "$COWORK_WORKDIR" \
  --config "$COWORK_ROOT/state/connector.json"

"$COWORK_BIN/cowork-connector" start \
  --config "$COWORK_ROOT/state/connector.json"

"$COWORK_BIN/cowork-connector" status \
  --config "$COWORK_ROOT/state/connector.json"
```

메인 실행기 설정은 `$COWORK_ROOT/state/runner.json`에 자동으로 생성된다.
`configure`가 반환한 `environment_id`를 관리자에게 전달해 최초 승인을 받는다.
관리자는 물리 서버·사용자 대응을 확인하고 `POST /v1/admin/local/environments/{id}/approve`로 승인한다.
기존 관리 API로 가능하며 사용자 설치 때마다 허브를 재설치하지 않는다.

`status`의 `running: true`는 로컬 프로세스가 살아 있다는 뜻이다. 허브의
`GET /v1/connectors`에서 `online: true`, `GET /v1/environments`에서 해당 환경의 `READY`까지 확인한다.
연결 프로그램은 SSH 등록 요청을 처리한다. 계산 작업의 대기·실행은 별도 작업 실행기가 담당한다.

## 4. 에이전트를 사용하는 경우 MCP 연결

수동 명령만 사용할 사람은 이 단계를 건너뛰고 6단계로 간다.
Codex가 설치된 **메인 컨테이너**에서 다음을 실행한다.

```sh
codex mcp add cowork -- \
  "$COWORK_BIN/cowork-mcp" \
  --hub-url "$COWORK_HUB_URL" \
  --token-file "$COWORK_ROOT/state/user.token" \
  --runner-config "$COWORK_ROOT/state/runner.json"

codex mcp get cowork
```

허브 주소의 `:8080`은 REST API다. 이 주소를 원격 MCP 주소로 등록하는 대신 위 실행 명령을 등록한다.
다른 MCP 클라이언트도 같은 `cowork-mcp` 실행 파일과 인자를 **stdio 서버**로 설정한다.
클라이언트별 설정 파일 형식은 다르므로 Codex 설정을 그대로 복사하지 않는다.

Codex에서 계산 요청 시 자동으로 참고할 `cowork-jobs` 스킬도 설치한다.
현재 연구실 Codex에서 확인한 사용자 스킬 위치를 사용하며, 기존 스킬이 있으면 덮어쓰지 않는다.

```sh
mkdir -p "$HOME/.codex/skills"
ln -sT "$COWORK_SOURCE/skills/cowork-jobs" "$HOME/.codex/skills/cowork-jobs"
```

링크 원본을 계속 사용하므로 내려받은 소스 디렉터리를 지우지 않는다.
에이전트를 다시 연결하고 다음과 같이 요청한다.

> cowork MCP로 내 서버 권한과 환경을 조회해줘. 메인 환경이 READY인지, Discord가 설정됐는지 확인해줘.

`submit_job`, `cancel_job`, `register_ssh_environment`를 포함한 10개 도구가 보여야 한다.
이름만 보이는 것으로 끝내지 말고 사용자 인증·환경 승인·알림 설정을 확인한다.
자동 스킬 선택은 모든 셸 명령을 강제로 가로채는 기능은 아니다. [MCP 안내](mcp-setup.md)를 참고한다.

## 5. 다른 서버의 개인 컨테이너 추가 (선택)

메인 환경에서만 작업하면 건너뛴다. 메인 컨테이너에서 대상의 본인 계정으로 SSH 키 접속이 되어야 한다.
기존 키 접속이 되면 `ssh-copy-id`를 다시 할 필요는 없다. 처음 연결할 때 대상 호스트를 확인한다.
다음은 접속 확인 예시이며 계정·주소·포트를 본인 값으로 바꾼다.

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -p 11010 alice@203.255.11.229 id
```

SSH 접속은 허브의 서버 사용 허가와 별개다. 관리자에게 허용받은 서버에만 등록할 수 있다.
에이전트에는 다음처럼 요청한다.

> 229 서버의 alice@203.255.11.229, SSH 포트 11010, 작업 경로 /10Gdata/alice/work를 cowork 환경으로 등록해줘.

MCP가 본인 SSH로 검사·등록하고 메인 실행기에 경로를 저장한다. 새 환경은 관리자의 승인을 받아
`READY`가 되어야 실행할 수 있다. 대상에는 별도 에이전트·MCP 설치가 필요 없다.
단, 공유된 실행기와 Python을 사용할 수 있고 대상에서도 허브에 접속할 수 있어야 한다.

에이전트 없이 등록하려면 현재는 본인 인증으로 `POST /v1/registration-requests`를 사용한다.
연결 프로그램 ID와 SSH 주소·계정·포트·서버 ID·작업 경로를 제출하면 상주 연결 프로그램이 처리한다.
요청 형식은 [연결 프로그램 API 안내](connector-setup.md#웹에서-사용할-api)를 따른다.
웹의 SSH 등록 입력 화면은 아직 없다. 등록 완료와 관리자 승인은 별개다.

## 6. 첫 작업 실행

먼저 메인 환경의 `READY`와 Discord 목적지 설정을 확인한다. 첫 실행은 작은 명령으로 점검한다.

```sh
"$COWORK_BIN/cowork-run" --config "$COWORK_ROOT/state/runner.json" \
  --cpus 1 --mem 64MiB --name first-test --detach -- /bin/echo cowork-ok
```

작업 ID와 로그 경로가 나오면 접수된 것이다. 자원이 부족하면 실행기가 기다렸다가 실행한다.
Discord에서 실제 시작·종료 알림을 받고 출력된 로그 경로에서 `cowork-ok`를 확인한다.
이것은 사용자가 실행할 점검 절차이며, 이 안내를 작성하면서 새 계산 작업을 제출하지는 않았다.

에이전트로 첫 작업을 실행하려면 같은 조건을 자연어로 전달한다.

> 메인 환경에서 /bin/echo cowork-ok를 CPU 1개, 메모리 64MiB로 실행해줘. 부족하면 대기열에 넣고, 접수 후에는 감시하지 말고 마무리해줘.

MCP와 수동 명령 중 **한 방식만** 선택한다. 둘 다 실행하면 별도 작업 두 건이 된다.
본 작업도 다음처럼 제출한다. 아래 분석 파일·Python은 사용자가 실제 준비한 환경의 것으로 바꾼다.

```sh
"$COWORK_BIN/cowork-run" --config "$COWORK_ROOT/state/runner.json" \
  --cpus 8 --mem 32GiB --detach -- python /10Gdata/alice/work/analysis.py --threads 8
```

`--cpus 8`은 허브 예약량, `--threads 8`은 해당 분석 프로그램의 실행 옵션이다.
GPU가 필요한 작업은 GPU가 준비된 환경에서 `--gpus 1`처럼 요청한다. GPU 한 장은 작업 하나가 예약한다.
수동 CLI는 현재 컨테이너에서 실행하며 자원 부족 시 대기한다. 자동 서버 선택·축소 질문은 하지 않는다.
다른 서버에서 수동 실행하려면 그 컨테이너에 SSH로 접속해 **그 환경의 실행기 설정**으로 호출한다.
메인 설정의 호스트 정보만 바꿔 원격 실행으로 사용하지 않는다. 메인에서의 SSH 제출은 MCP가 지원한다.

MCP는 자원 검토 후 대기·준비된 축소안·다른 준비된 서버 중 선택을 도울 수 있다.
접수 후에는 에이전트를 종료해도 실행기가 대기·실행·종료 보고를 하고 허브가 Discord 알림을 보낸다.
메인/대상 컨테이너의 중지는 에이전트 종료와 다르며, 그 안의 실행 중인 프로그램도 중단될 수 있다.

## 7. 상태 확인·취소·재시작

에이전트에는 작업 ID로 상태 조회나 취소를 요청한다. 수동 이용자는 사용자 API
`GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel`을 사용한다. 별도 상태·취소 CLI는 아직 없다.
Ctrl-C나 터미널 종료는 작업 취소가 아니다. 취소 요청 후 실제 `CANCELLED`가 되어야 종료된 것이다.
접수 응답이 불확실하면 기존 작업 ID·기록을 먼저 확인한다. 같은 CLI를 다시 실행하면 새 작업이 될 수 있다.

컨테이너 재시작 후에는 본인의 절대 경로로 `cowork-connector start --config ...`를 다시 실행한다.
자동 실행 스크립트는 `cowork-connector startup --config ...`로 생성한 뒤 관리자와 기존 시작 절차에 연결한다.
스크립트 생성만으로 자동 시작이 설정되지는 않는다. 재시작 때마다 설치·새 토큰 발급·재등록할 필요는 없다.
컨테이너를 새로 만들거나 교체했다면 기존 설정을 그대로 쓰지 말고 별도 환경 등록을 확인한다.

| 막힌 지점 | 확인할 내용 |
| --- | --- |
| 허브 연결 실패 | 내부망 접근과 관리자가 안내한 허브 주소 |
| 인증 실패 | 본인 토큰, 파일 소유자·권한, 토큰 유효성 |
| `USER_IDENTITY_REQUIRED` / `IDENTITY_MISMATCH` | 관리자 기준 UID/GID와 현재 계정 |
| `FORBIDDEN_NODE` | 본인에게 허용된 서버 ID |
| 승인 대기 | 관리자가 환경의 물리 서버·소유자를 확인한 뒤 승인 |
| `NOTIFICATION_NOT_CONFIGURED` | 본인 Discord 채널의 허브 연결 |
| SSH 등록 실패 | 키 접속·호스트 확인·공유 경로·대상의 Python과 허브 접근 |
| `UNKNOWN` 또는 실행기 장애 | 실제 프로세스와 기존 기록을 관리자와 확인. 바로 재제출하지 않음 |

## 웹 플랫폼을 연결한 뒤의 흐름

목표 흐름은 **웹 로그인 → 개인 토큰 발급 → 메인 컨테이너 설치 → 웹에서 SSH 추가 → 관리자 승인 → 작업 제출**이다.
추가 토큰·SSH 요청·환경 승인 API와 연결 프로그램은 구현되어 있지만, 로그인·토큰 다운로드·등록/승인 화면은 남아 있다.
현재 매뉴얼의 관리자 준비와 API 절차를 웹 화면으로 옮기는 것이 다음 단계다.
