# MCP 연결과 작업 제출

처음 설치하는 사용자는 [신규 사용자 매뉴얼](first-user-guide.md) 또는
[공개 웹의 MCP 설치·사용](https://mcp-cowork-pgl.web.app/#guide)을 먼저 따른다.
이 문서의 `ezy` 경로는 기존 운영 설치 기록이며 다른 사용자가 그대로 복사하는 값이 아니다.
웹 주소는 브라우저용이며, MCP·CLI는 컨테이너에서 접근하는 내부 허브 주소를 사용한다.

현재 MCP는 **사용자용 조회·검토·로컬/SSH 작업 제출·취소·SSH 환경 등록·FASTQ 목록 등록 및 경로 갱신**을 제공한다.
`submit_job`이 현재 컨테이너 또는 SSH 대상의 실행기를 시작하고 접수 결과를 반환하면,
실행기가 독립적으로 대기·실행·종료 보고를 수행한다. MCP·초기 SSH 연결이 끝나도 작업은 유지된다.
허용 서버 `224`, `226`, `228`, `229`의 본인 실행환경이 모두 승인됐다.
226은 로컬 실행기, 나머지 세 서버는 메인 MCP에 등록된 SSH 실행 경로를 사용한다.
224·228은 GPU 예약 사전검토까지 확인했으며 실제 계산은 아직 제출하지 않았다.
[226 로컬 검증](verification-226.md)과 [229 SSH·GPU 검증](verification-229.md)을 참고한다.

```text
개인 컨테이너의 MCP 클라이언트(Codex·Claude Code 등)
    → stdio cowork-mcp
    → HTTP(S) 허브 API
```

MCP 어댑터는 에이전트가 실행되는 환경에 설치한다. 중앙 허브를 재빌드하거나 재시작할 필요는 없다.
SSH 대상에는 별도 MCP 서버나 에이전트가 필요 없다. [SSH 환경 등록과 실행](ssh-runner.md)을 참고한다.
현재 허브의 `:8080`은 REST API이며 MCP 서버 URL이 아니다. 이 주소를 클라이언트의
원격 MCP 주소로 직접 넣는 대신 아래 stdio 명령을 등록한다.

## 작업 대기와 종료 보고

새 실행기는 허브의 `local_job_wait` 기능을 확인한 후 작업별 롱 폴링을 사용한다.
에이전트는 제출 결과를 받은 뒤 턴을 종료해도 된다. 실행·대기·Discord 시작/종료 알림에는
추가 LLM 호출이 필요 없다.

- 실행기는 `POST /v1/local/jobs/{id}/wait`로 상태 변경을 기다린다. 허브는 DB를 한 번 읽은 뒤
  트랜잭션과 연결을 닫고 메모리의 이벤트를 기다린다. 이 대기는 DB 락을 점유하지 않는다.
- 앞 작업이 끝나면 완료 기록과 다음 작업 배정을 같은 트랜잭션에 반영한다. 커밋 후 해당 작업을
  기다리는 요청을 깨워 최신 상태를 반환한다. **20초는 최대 연결 유지 시간이며, 실행을 20초 늦추는 간격이 아니다.**
- 변경이 없으면 최대 20초 뒤 상태를 다시 확인해 응답하고, 실행기가 새 대기 요청을 연다.
  재접속·허브 재시작 뒤에도 DB의 현재 상태로 이어간다. 실행 전 단 한 번의 claim 승인은 유지한다.
- 생존 확인은 별도로 기본 10초마다 기존 `/poll` API에 보낸다. 생존 제한이 짧게 설정된 허브에서는
  그 제한의 1/3 간격을 사용한다. 일반 생존 확인 요청은 전체 대기열을 다시 계산하지 않는다.
- HTTP 통신은 프로세스 감시와 분리한다. 통신 대기·재시도 중에도 자식 프로세스 종료를 기록하고
  취소 신호를 처리한다. 장애 시 재시도 간격을 점차 늘리며 최대 10초 범위의 무작위 지연을 적용한다.
- 실행기 상태 파일은 내용이 달라질 때만 저장한다. 실행 승인·시작·완료 이벤트의 디스크 동기화와
  중복 제출/실행 방지는 유지한다.

허브는 매초 전체 대기열을 검토하지 않는다. 제출·완료·취소·권한/예약 예산 변경·실행환경 복구 등
배정 조건이 바뀔 때 즉시 검토한다. 내용이 같은 생존 보고와 작업 시작 보고는 대기열을 다시
계산하지 않는다. 정상 완료 후 다음 작업 배정은 주기 타이머를 기다리지 않는다.

장애 감지는 현재 가장 이른 생존 확인 만료 시각에 타이머로 수행한다. 타이머가 깨어나면 먼저
DB의 최신 보고 시각을 다시 읽으므로, 그 사이 들어온 생존 보고를 무시하고 장애로 표시하지 않는다.
기본 45초 동안 보고가 없던 실행 작업은 `UNKNOWN`으로 표시하며 예약을 유지한다. 대기 실행기가
끊기면 `RUNNER_OFFLINE`으로 표시해 배정에서 제외한다. 이미 처리한 만료 시각은 다음 타이머에서
제외하므로 같은 장애를 반복해서 점검하지 않는다.

복구용 전체 검토는 허브 시작 시 한 번, 이후 기본 60초 간격으로 수행한다. DB 오류가 발생한
점검은 1초 뒤 재시도한다. 타이머 대기 중 DB 연결이나 트랜잭션은 유지하지 않는다.
Discord 전송/재시도 처리의 별도 루프와 웹 현황의 20초 자동 갱신은 기존대로 유지한다.
실제 사용량에 따른 강제 자원 제한이나 예약 정책은 바꾸지 않는다.

실행기 측 롱 폴링 개선은 업데이트된 클라이언트로 새로 제출하는 작업부터 적용된다.
이미 실행 중인 구버전 실행기는 완료될 때까지 기존 통신 방식으로 동작한다. 공유 소스 설치는 새 실행기 프로세스부터 반영되며,
별도로 설치한 클라이언트는 업데이트해야 한다. 새 실행기가 구버전 허브에 접속하면 기존 1초 조회로
동작하므로 허브와 클라이언트를 순서대로 업데이트할 수 있다.

2026-09-28 롱 폴링과 이벤트 기반 스케줄러를 운영 허브에 적용했다. SQLite·임시 PostgreSQL 전체
회귀 테스트 265개를 통과했고 DB 종류별 전용 시험 4개는 해당하지 않는 DB에서 제외됐다.
만료 경계·생존 보고 연장·타이머 조회 중 새 작업·재시작·누락 복구 및 기존 실행기 호환을 검증했다.
격리 환경에서 작업 하나가 21.5초 동안 대기할 때 생존 확인
2회, 롱 폴링 상태 읽기 2회, 상태 파일 저장 0회를 관측했다. 이는 대규모 처리량 측정은 아니다.
운영 적용 전후 기존 실행 작업 2개를 유지했고, 226 로컬 및 224·228·229 SSH 실행기 준비 상태와
인증된 대기 API 응답을 확인했다. 229의 실행 경로도 새 공유 실행기로 연결했다.
스케줄러 변경은 허브 재시작부터 적용되며, 이를 위해 클라이언트를 다시 설치할 필요는 없다.

## 설치와 MCP 클라이언트 등록

프로젝트의 Python 환경에 선택 의존성을 설치한다. 중앙 허브만 설치할 때는 `mcp` extra가 필요 없다.

```sh
cd /10Gdata/ezy/01_Programs/cowork-mcp
uv sync --locked --extra mcp --group dev
```

아래 경로는 현재 `ezy` 컨테이너 기준이다. 다른 사용자는 자신의 설치·개인 토큰·승인된 실행기 설정 경로를 사용한다.
실행기 설정과 MCP의 허브 주소·토큰 경로는 일치해야 한다. `--runner-config`가 없으면 조회·검토·취소는 가능하지만 제출은 거절된다.
등록은 명령을 실행한 환경의 해당 클라이언트 사용자 설정에 적용된다. 다른 PC의 클라이언트 설정까지 변경하지 않는다.
새 클라이언트 세션에서 도구 목록을 확인한다. 이 문서의 등록 명령은 현재 환경에서 실행 완료했다.

Codex의 stdio 등록과 설정 방식은 [공식 MCP 문서](https://developers.openai.com/codex/mcp/)를 따른다.
Claude Code의 stdio 등록과 설정 방식은 [공식 MCP 문서](https://docs.claude.com/en/docs/claude-code/mcp)를 따른다.

### Codex

```sh
codex mcp add cowork -- \
  /10Gdata/ezy/01_Programs/cowork-mcp/.venv/bin/cowork-mcp \
  --hub-url http://192.168.10.41:8080 \
  --token-file /10Gdata/ezy/01_Programs/cowork-mcp/.local/credentials/user-ezy.token \
  --runner-config /10Gdata/ezy/01_Programs/cowork-mcp/.local/runner-226.json

codex mcp get cowork
```

### Claude Code

```sh
claude mcp add cowork -s user -- \
  /10Gdata/ezy/01_Programs/cowork-mcp/.venv/bin/cowork-mcp \
  --hub-url http://192.168.10.41:8080 \
  --token-file /10Gdata/ezy/01_Programs/cowork-mcp/.local/credentials/user-ezy.token \
  --runner-config /10Gdata/ezy/01_Programs/cowork-mcp/.local/runner-226.json

claude mcp get cowork
```

`-s user`는 해당 사용자의 모든 프로젝트에서 사용한다. 생략하면 기본값은 `local`이며 명령을 실행한 디렉터리에서만 보인다.
`-s project`는 저장소에 공유되는 `.mcp.json`을 만들므로 사용하지 않는다. 개인 토큰 경로와 실행기 설정 경로가 사용자마다 다르기 때문이다.
등록 확인 결과가 `Status: ✔ Connected`이면 stdio 연결이 성립한 것이며 사용자 인증 성공을 뜻하지 않는다.

## 계산 작업에서 자동 사용

[cowork-jobs 스킬](../skills/cowork-jobs/SKILL.md)의 원본은 이 저장소의 `skills/cowork-jobs`이며 링크로 원본을 계속 사용한다.
frontmatter의 `name`·`description`은 Codex와 Claude Code가 공통으로 읽는다.

자동 선택은 에이전트가 요청과 스킬 설명을 보고 판단하는 기능이며 셸 명령을 강제로 가로채지 않는다.
형식 검사와 Codex의 스킬 발견·활성화는 검증했으며, 모든 자연어 요청에서의 선택을 보장하는 것은 아니다.
현재 설치 범위는 이 컨테이너의 사용자 Codex와 Claude Code다. 다른 사용자는 에이전트를 실행할 메인 환경에
스킬과 MCP 연결을 준비한다. SSH로 계산만 수행하는 대상 컨테이너에는 설치하지 않는다.

새 스킬이 보이지 않으면 사용 중인 클라이언트(Codex 또는 Claude Code)를 다시 시작한다.
이전 도구 목록만 남아 있으면 `cowork` MCP를 다시 연결해
`submit_job`, `cancel_job`, `register_ssh_environment`와 `catalog_*` 6개를 포함한 16개 도구가 보이는지 확인한다.

## FASTQ 등록과 이동 경로 갱신

[cowork-data-library 스킬](../skills/cowork-data-library/SKILL.md)을 설치 패키지와 업데이트에 포함한다.
“이 FASTQ를 Wolffia 프로젝트에 등록해줘”, “이 FASTQ 폴더를 /nas/archive로 옮겨줘” 같은 요청에서 자동 사용한다.
에이전트가 등록된 FASTQ를 이동·이름 변경하면 이동 확인 후 허브 경로 갱신까지 이어서 처리한다. 스킬명이나 DB 갱신을 따로 요청할 필요가 없다.
새 파일은 프로젝트·종·샘플·데이터 종류·저장 서버·경로·크기를 입력하고, 조직·조건·반복·보고서·통계는 아는 만큼 추가한다.
이동 갱신은 동일 파일의 위치만 수정하며 통계·샘플 연결·통합 입력 관계를 보존한다. 목록 수정만 요청했을 때는 실제 파일을 옮기지 않으며, SeqKit 실행도 별도 요청이다.
외부 터미널에서 직접 실행한 이동은 감시하지 않으므로, 이미 옮긴 파일은 이동 내역을 알려주면 목록에 반영한다.
미리보기 후 같은 요청 키로 저장하고 웹 Data library에서 확인한다. 이 연구실은 승인된 모든 사용자의 개인 토큰에 수정 권한을 부여한다.
자세한 저장 구조와 제약은 [데이터 목록 문서](data-catalog.md#mcp-registration-and-moved-paths)를 참고한다.

### Codex

[cowork-jobs 스킬](../skills/cowork-jobs/SKILL.md)을 현재 사용자 Codex에 설치했다.
`/home/ezy/.codex/skills/cowork-jobs`는 이 프로젝트의 스킬 원본을 가리킨다.
Codex의 실제 `skills/list` 조회로 이 프로젝트와 `/tmp`에서 사용자 스킬로 검색되고,
활성화 상태이며 `cowork` MCP 의존성이 인식되는 것을 확인했다.
`agents/openai.yaml`의 `allow_implicit_invocation: true`로 자동 선택을 허용한다.

이제 “이 데이터 매핑해줘”, “학습 돌려줘”처럼 계산을 요청하면 `cowork`를 명시하지 않아도
스킬을 선택해 자원 검토·제출 절차를 따르도록 설정했다. 이미 정해진 명령·자원 조건과 실행 요청을
재확인하지 않으며, 필요한 자원 정보가 없거나 부족할 때만 추가 정보·선택을 요청한다.
파일 조회·코드 수정·짧고 가벼운 점검은 대상에서 제외한다. 상세 실행 규칙은 스킬 원본에서 관리한다.

자동 선택과 스킬 갱신 동작은 [공식 스킬 안내](https://developers.openai.com/codex/skills/)를 참고한다.
새 사용자 설치는 공식 사용자 경로인 `~/.agents/skills/cowork-jobs`를 사용한다.
위 `~/.codex/skills` 경로는 기존 설치 기록이다. 이미 발견되는 스킬을 두 경로에 중복 설치하지 않는다.

### Claude Code

`~/.claude/skills/cowork-jobs` 심볼릭 링크로 설치한다. 기존 스킬이 있으면 덮어쓰지 않는다.

```sh
mkdir -p "$HOME/.claude/skills"
ln -sT /10Gdata/ezy/01_Programs/cowork-mcp/skills/cowork-jobs "$HOME/.claude/skills/cowork-jobs"
```

Claude Code는 `agents/openai.yaml`을 읽지 않고 무시하며 별도 설정 파일이 필요 없다.
자동 선택 판단은 `description`만으로 한다. 링크 설치 후 Claude Code 세션의 스킬 목록에
`cowork-jobs`가 나타나는 것을 확인했다. 자연어 요청에서의 실제 자동 선택과 Claude Code를 통한 작업 제출·실행은 아직 확인하지 않았다.

## 개인 토큰 준비

현재는 [Google 로그인 후 개인 토큰 다운로드](web-tokens.md)로 첫 토큰을 받는다.
아래는 웹 적용 전 기존 관리자가 발급한 토큰을 준비했던 운영 기록이다. 신규 사용자는 이 절차를 실행하지 않는다.

토큰을 임의로 새로 만들지 않는다. 허브가 이미 발급한 개인 토큰을 아래 명령으로 준비한다.
**처음 `docker compose up -d`를 실행했던 허브 호스트 터미널에서, `ezy`(UID 1101) 계정으로** 실행한다.
이 프로젝트 경로가 허브 호스트와 개인 컨테이너에서 같은 공유 디렉터리여야 한다.

```sh
cd /10Gdata/ezy/01_Programs/cowork-mcp
python3 scripts/prepare_mcp_token.py --user ezy
```

이 스크립트는 허브 DB를 읽기 전용으로 조회해 기존 사용자 토큰이 유효한지 확인한 뒤,
토큰 내용을 화면에 출력하지 않고 `.local/credentials/user-ezy.token`을 준비한다.
저장 디렉터리는 `0700`, 파일은 `0600`이며 기존 파일이 다르면 덮어쓰지 않는다.
`.local/`은 `.gitignore`에 포함되어 있다. 실패 시 오류 문구만 전달하고 토큰 파일 내용은 공유하지 않는다.

- 현재 등록된 MCP는 `/10Gdata/ezy/01_Programs/cowork-mcp/.local/credentials/user-ezy.token`에서 **허브의 ezy 사용자 토큰**을 읽는다.
- 초기 등록에서 생성된 원본은 **허브 컨테이너 안**의 `/data/registration-credentials/user-ezy.token`이다.
  이 경로가 개인 계산 컨테이너에서도 같은 파일을 가리킨다고 가정하지 않는다.
- 관리자가 개인 토큰을 안전하게 제공하고, 사용자가 MCP 실행 환경의 지정 경로에 준비한다.
  파일 소유자는 MCP 실행 사용자여야 하고, 다른 사용자가 읽을 수 없는 권한(`0600`)이어야 한다.
  토큰 파일은 일반 파일이어야 하며 심볼릭 링크는 허용하지 않는다.
- 관리자·Worker 토큰은 사용하지 않는다. 토큰 값은 채팅·MCP 인자·클라이언트 설정·로그에 넣지 않는다.
- 파일이 없으면 MCP 시작·도구 목록·`hub_health`는 동작하고, 인증이 필요한 도구는
  `TOKEN_NOT_CONFIGURED`를 반환한다. 각 호출 때 파일을 다시 읽으므로 준비 후 MCP 재시작 없이 재시도할 수 있다.
- 현재 내부망 HTTP 주소로 연결을 확인했다. 인증 호출을 운영할 때는 기존 허브 운영 방침에 따라
  HTTPS 프록시 또는 보호된 네트워크 경로를 사용한다. 어댑터는 HTTP 리다이렉션을 따르지 않는다.

## 확인 명령

개인 토큰 없이 실제 stdio MCP와 공개 허브 응답을 확인한다.

```sh
cd /10Gdata/ezy/01_Programs/cowork-mcp
.venv/bin/python scripts/check_mcp.py \
  --hub-url http://192.168.10.41:8080 --health-only
```

개인 토큰을 준비한 뒤 사용자 권한으로 조회한다.

```sh
.venv/bin/python scripts/check_mcp.py \
  --hub-url http://192.168.10.41:8080 \
  --token-file /10Gdata/ezy/01_Programs/cowork-mcp/.local/credentials/user-ezy.token
```

두 번째 명령은 서버 목록·환경 목록·작업 목록·알림 설정 상태를 조회한다. 출력은 도구 성공 여부,
서버 ID·온라인 상태, 목록 개수로 제한하며 토큰을 출력하지 않는다. 실패하면 종료코드 1을 반환한다.
조회 성공과 실제 실행 가능은 별개다. 승인된 로컬 환경은 실행 스크립트로 작업을 시작할 수 있으며,
작업이 없는 동안 노드의 `online`이 `false`일 수 있다.

새 MCP 클라이언트 세션에서는 다음과 같이 요청할 수 있다.

> cowork MCP로 허브 상태를 확인하고, 나에게 허용된 서버와 등록된 실행환경을 조회해줘.

## 제공 도구

Codex와 Claude Code는 같은 stdio 어댑터에서 아래 10개 도구를 제공받는다.

| 도구 | 동작 |
| --- | --- |
| `hub_health` | 공개 허브 정상 응답 확인. 사용자 인증 성공을 의미하지 않음 |
| `cluster_status` | 본인에게 허용된 서버·Worker 상태·예산·예약량 조회 |
| `list_environments` | 본인 환경과 검증 상태 조회 |
| `list_jobs` | 본인 작업 목록 조회 |
| `job_status` | 본인 작업 한 건의 상태·알림 결과 조회 |
| `notification_status` | 본인 Discord 설정 상태 조회. 메시지 전송 없음 |
| `plan_job` | 배정 가능 여부와 대기·준비된 축소안·다른 서버 선택지 검토. 예약·실행 없음 |
| `submit_job` | 현재 컨테이너 또는 등록된 SSH 대상의 실행기를 시작하고 접수 결과·로그 경로 반환 |
| `cancel_job` | 본인 작업 취소 요청. 실행 중이면 실행기의 실제 종료 보고 후 예약 반환 |
| `register_ssh_environment` | 기존 사용자 SSH로 대상 환경을 준비·등록하고 메인 MCP의 제출 경로에 연결 |

목록은 `limit` 1–100으로 제한한다. 환경은 마지막 `id`, 작업은 마지막 `seq`를 `after`로 넘긴다.
`plan_job`의 `primary`와 `alternatives`는 기존 허브의 JobSpec 형식을 사용하며 메모리 단위는 MiB다.
신청량은 실제 CPU·메모리 사용률이 아니며, 검토 결과는 자원 예약이 아니다.

## 자원이 부족할 때

`plan_job`은 다음 선택지를 `choices`로 반환한다. 사용자가 이미 선택한 경우를 제외하면 에이전트가 선택을 묻는다.

- `wait`: 원래 조건으로 대기한다. `queue_if_unavailable`로 제출한다.
- `run_alternative`: 명시적으로 준비한 `alternatives` 중 현재 가능한 안을 선택한다.
  예를 들어 CPU 8개·`--threads 8` 대신 CPU 4개·`--threads 4`를 함께 검토한다.
- `use_other_environment`: 본인에게 허용된 다른 서버의 준비된 환경에서 원래 자원량으로 실행한다.
  등록된 SSH 경로가 있으면 현재 MCP의 `submit_job`이 그곳의 실행기를 시작한다.
  `requires_target_runner: true`이면 먼저 SSH 실행기 등록이 필요하다.

실행 가능한 축소안은 실제 argv와 자원량을 함께 제시한 경우에만 반환한다. 메모리를 임의로 줄이지 않는다.
대안이 없으면 `alternatives_need_preparation`과 서버별 남은 예약량을 안내한다.
`setup_required_nodes`는 본인 READY 환경이 없는 서버다. 현재 네 서버 모두 READY 환경이 있다.
다른 환경 탐색은 최대 100개 환경 목록 중 8개 후보를 확인하며, 전부 확인하지 못하면
`other_environment_scan_complete: false`를 반환한다. 대상의 파일·프로그램 접근까지 검사한 결과는 아니다.

## 제출·재시도·취소

`submit_job`의 `request`에는 고유한 `request_key`, 실행 정책 `mode`, 선택한 `spec`을 넣는다.
`spec.environment_ids`는 메인 MCP에 연결된 로컬 또는 SSH 환경 하나여야 하며 절대 경로 `workdir`을 반드시 지정한다.

```json
{
  "request": {
    "request_key": "unique-request-identifier",
    "mode": "start_if_available",
    "spec": {
      "name": "hello",
      "environment_ids": ["2b717a94c94d4179b08963be175e99ab"],
      "argv": ["/bin/echo", "hello"],
      "cpus": 1,
      "memory_mib": 64,
      "gpu_count": 0,
      "workdir": "/10Gdata/ezy/01_Programs/cowork-mcp"
    }
  }
}
```

즉시 실행을 선택했다면 `start_if_available`을 쓴다. 검토 후 자원이 부족해졌으면 몰래 대기 등록하지 않고 거절한다.
대기를 선택했을 때 `queue_if_unavailable`을 쓴다. 실제 작업은 `job_status`로 확인한다.

`submit_job`이 `accepted: true`와 `agent_action: finish_turn`을 반환하면 에이전트는 작업 ID·로그 경로와
Discord 알림 안내를 최종 답변으로 전달하고 턴을 끝낸다. 대기 선택은 실행기의 대기열 등록을 뜻한다.
시작 전까지 상태를 반복 조회하거나 sleep·로그 감시·추가 에이전트를 실행하지 않는다.
대기와 실행은 일반 실행기 코드가 수행하며 LLM 호출이 필요 없다. 나중에 사용자가 상태를 물으면
그때 한 번 조회한다. 지속 감시나 실행 수명 검증을 명시적으로 요청한 경우에만 예외로 관찰한다.
에이전트가 수동 CLI 경로를 사용할 때는 `cowork-run --detach`로 접수한다.

접수 성공은 작업 완료를 뜻하지 않는다. 응답이 불확실하거나 MCP를 재연결했을 때는 **같은 키·같은 요청**으로 재시도한다.
실행기 기록이 남아 있으면 같은 작업 ID를 반환하며 새 실행기를 만들지 않는다. 같은 키로 내용을 바꾸면 거절한다.
`SUBMISSION_PENDING` 또는 `SSH_RESULT_UNCERTAIN`이면 같은 키로 확인하고, 영속 기록을 지우거나 새 키로 우회하지 않는다.
실행기 자체가 비정상 종료된 경우에는 기존 기록과 실제 프로세스를 대조해야 하며 자동으로 재실행하지 않는다.

`cancel_job`에는 `job_id`를 전달한다. 반환된 `cancel_requested`는 요청 접수이며,
실제 종료 상태는 `job_status`의 `CANCELLED`로 확인한다. 다른 사용자의 작업은 취소할 수 없다.

## 남은 범위

웹 단계에는 [개인 MCP 토큰 관리](../plans/container-job-orchestration.md#개인-mcp-토큰-관리)를 포함한다.
현재 준비 스크립트는 기존 토큰을 전달하는 초기 설치 수단이다. 추가 개인 토큰의 발급·목록·폐기 API와
웹 SSH 등록 요청을 처리하는 연결 프로그램을 구현했다. [설치 안내](connector-setup.md)를 참고한다.
2026-09-21 운영 허브 업데이트와 226 연결 프로그램 활성화, 실제 229 SSH 등록 요청 처리를 확인했다.
Google 로그인·토큰 다운로드 화면은 구현했고 [운영 설정](web-tokens.md)은 아직 연결하지 않았다.
나머지 관리 화면과 컨테이너 재시작 시 연결 프로그램 자동 실행 설정은 남아 있다.

SSH는 대상 실행기 준비·제출에 사용한다. 허브의 관리자 기능과 Worker 완료 보고 도구는 MCP에 노출하지 않는다.
229의 SSH 실행과 RTX 3090 CUDA 메모리 접근까지 검증했다. 224·228의 실제 작업 실행·실제 연구 작업 검증·
다른 사용자/클라이언트에 스킬 배포·웹 화면은 후속 범위다.
