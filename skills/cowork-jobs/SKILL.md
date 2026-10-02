---
name: cowork-jobs
description: Reserve lab server resources through cowork MCP before launching analysis, training, mapping, assembly, simulations, or other CPU/memory/GPU batch jobs locally or over SSH from the main container. Use automatically for these lab workloads even without mentioning cowork or MCP. Also use for SSH environment registration and Cowork job status and cancellation. Exclude code-only edits, explanations, file inspection, and short lightweight checks or tests.
---

# Cowork 계산 작업

연구실 개인 컨테이너에서 계산 명령을 실행하기 전에 `cowork` MCP로 자원을 검토하고 제출한다.
사용자가 실행을 요청했다면 MCP 사용 여부를 다시 묻지 않는다. 코드 작성만 요청했다면 계산까지 시작하지 않는다.
MCP 이름을 말하지 않은 “이 데이터 분석해줘”, “학습 돌려줘”, “매핑 진행해줘”도 실제 계산을 시작하는 시점에 적용한다.

## 실행 조건 준비

- 연결된 `cowork` 도구를 검색하고 `list_environments`로 본인 환경을 확인한다.
  MCP의 로컬 또는 등록된 SSH 실행환경 하나를 선택한다. 대상 컨테이너에서 사용할 작업 경로의 절대 경로를 준비한다.
  설정 경로·환경 ID가 불명확하면 로컬 MCP 등록의 `--runner-config` 경로와 해당 설정의 환경 ID만 확인한다.
  토큰 파일 내용은 읽거나 출력하지 않는다. 조회된 서버가 여러 개라는 이유로 현재 컨테이너를 추측하지 않는다.
- 사용자가 새 SSH 환경 등록을 요청하면 `register_ssh_environment(target={host,user,port,node_id,workdir})`을 사용한다.
  메인 컨테이너의 기존 SSH 인증으로 공유 코드·Python·개인 토큰 경로를 사용해 원격 실행기를 준비한다.
  대상에는 에이전트나 MCP 서버를 설치하지 않는다. 등록은 서버 사용 권한을 늘리지 않으며,
  계정 승인 시 정한 허용 서버·UID/GID·자원 검사를 통과하면 추가 승인 없이 자동 등록된다.
  `requires_approval: true`가 남아 있으면 이전 허브 버전 또는 등록 조건 오류를 확인한다. `READY`가 아닌 환경을 실행 가능으로 표시하지 않는다.
- `submit_job`이 없고 조회 도구만 있으면 이전 MCP 연결일 수 있다. 연결 갱신이 필요하다고 안내한다.
  도구·인증·환경 등록·Discord 설정 오류가 있으면 원인을 알리고 계산 제출을 보류한다.
  실패한 작업을 일반 셸 명령으로 실행하여 예약 절차를 우회하지 않는다.
- 실제 `argv`, 작업명, CPU 수, 메모리 MiB, GPU 개수, 절대 경로 `workdir`을 준비한다.
  사용자 요청·이미 합의한 설정·프로젝트 실행 프로필을 우선한다. 자원량이 불명확하면 필요한 값만 묻는다.
  CPU 예약량과 실제 프로그램의 threads 옵션을 맞춰 검토한다. GPU는 한 장당 작업 하나다.
  `argv`는 인자 배열로 보존한다. 파이프라인·conda 활성화 등이 필요하면 명시적인 진입 스크립트를 준비한다.
  MCP의 실행 환경이 현재 대화형 셸의 conda·환경변수와 같다고 가정하지 않는다.

## 검토와 선택

`plan_job(primary=선택한 실행안, alternatives=준비한 대안들)`을 호출한다. 검토는 예약이 아니다.
CPU·메모리 값은 다른 사용자를 포함한 신청량 원장이며 실제 사용률이 아니다.

- 원래 실행안이 가능하고 사용자가 실행을 요청했다면 해당 조건으로 제출한다. 별도 허가를 반복해서 묻지 않는다.
- 부족하면 `choices`에 따라 원래 조건으로 대기, 가능한 축소안, 준비된 다른 서버를 설명하고 선택을 묻는다.
  사용자가 이미 “기다려”, “안 되면 4 threads로 실행” 같은 선택을 정했다면 그 범위대로 진행한다.
- 축소안은 프로그램에서 지원하는 실제 threads 옵션과 자원량을 함께 작성해 검토한다.
  메모리·GPU 요청을 임의로 줄이지 않는다. `alternatives_need_preparation`은 축소 실행이 안전하다는 뜻이 아니다.
- 다른 서버는 본인에게 허용되고 준비된 환경만 제안한다. `setup_required_nodes`는 준비 필요로 안내한다.
  `submission_transport: ssh`이고 `requires_target_runner: false`이면 메인 MCP의 `submit_job`이 SSH로 실행기를 시작한다.
  SSH 경로가 미등록이면 먼저 `register_ssh_environment`로 준비한다. 대상의 입력·작업 경로·프로그램 접근을 확인한다.
  현재 메인 셸의 환경변수가 SSH 대상에 그대로 전달된다고 가정하지 않는다. 추천을 실행 완료로 설명하지 않는다.
- 이미 허브로 예약된 작업 내부에서 하위 명령을 다시 제출해 이중 예약하지 않는다.
  첫 버전은 하나의 명령·스크립트 전체를 한 작업으로 예약한다.

## 제출과 후속 처리

1. 새 작업에는 고유한 `request_key`를 정한다. `submit_job(request={request_key, mode, spec})`을 호출한다.
   `spec`은 선택된 실제 명령·자원량이며 `environment_ids`에는 해당 실행기의 환경 하나만 넣는다.
   즉시 실행을 선택했다면 `start_if_available`, 대기를 선택했다면 `queue_if_unavailable`을 명시한다.
2. 검토 후 용량이 바뀌어 즉시 실행이 거절되면 다시 검토한다. 대기·축소를 임의로 선택하지 않는다.
   확정적으로 거절되어 접수되지 않은 요청을 새 선택으로 다시 제출할 때만 새 키를 쓴다.
   응답 유실·시간 초과·`SUBMISSION_PENDING`·`SSH_RESULT_UNCERTAIN`이면 반드시 같은 키·같은 내용으로 확인한다.
   접수 여부가 불명확한 상태에서 기록을 지우거나 키를 바꾸어 재제출하지 않는다.
3. 접수 결과의 작업 ID와 로그 경로를 전달한다. 접수 성공을 계산 완료로 표현하지 않는다.
   **`accepted: true`이면 시작·완료를 기다리지 말고 최종 답변으로 현재 턴을 끝낸다.**
   “작업 접수 완료: ID … . 자원이 확보되면 자동 실행되며 시작·종료는 Discord로 알려드립니다.”처럼 안내한다.
   “대기하겠다”, “기다려”는 대기열 등록 선택이며 에이전트의 지속 감시 요청으로 해석하지 않는다.
   접수 후 `job_status`·`list_jobs` 반복 호출, sleep·wait 루프, 로그 감시나 별도 감시 에이전트를 시작하지 않는다.
   별도 실행기가 대기·실행·종료 보고를 맡는다. 셸에서 `cowork-run`을 사용할 때도 에이전트는 `--detach`로 접수한다.
   사용자가 나중에 상태를 물으면 한 번 조회해 답한다. 현재 턴의 지속 감시나 실행 수명 검증을 명시적으로
   요청한 경우에만 그 범위에서 관찰한다. 접수 불확실성의 동일 키 확인은 위 재시도 규칙을 따른다.
4. 시작·종료 알림은 허브가 사용자의 등록된 Discord 채널로 보낸다.
   같은 cowork 작업에 별도 Discord 래퍼·완료 감시기를 추가하지 않는다.
   알림 전송이 실패해도 완료된 계산을 재실행하지 않는다. 전송 성공 여부는 작업의 알림 상태로 확인한다.
5. 취소 요청은 `cancel_job(job_id)`로 전달한다. 실행 중 작업은 실행기가 실제 종료를 보고해야 예약이 반환된다.
   취소 요청 접수와 `CANCELLED` 완료를 구분한다. `UNKNOWN`·실행기 장애는 기존 기록과 프로세스 확인이 필요하며
   같은 계산을 자동으로 다시 실행하지 않는다.

이 스킬은 계산을 cowork 경로로 수행하는 에이전트 지침이다. 일반 셸 명령을 강제로 가로채거나
컨테이너의 실제 CPU·메모리 사용량을 제한하는 기능은 아니다.
