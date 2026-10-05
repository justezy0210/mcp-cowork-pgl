---
name: cowork-data-library
description: Keep the Cowork FASTQ catalog in sync when moving or renaming FASTQ files or their containing directories, locally or over SSH. Use automatically for these moves even when the user does not mention Cowork, MCP, or database updates; finish the authorized move by updating registered paths. Also use for FASTQ metadata registration, completed-move reconciliation, and catalog history. Exclude sequencing analysis, statistics computation, and moves unrelated to sequencing data.
---

# Cowork FASTQ 등록과 경로 갱신

`cowork` MCP로 허브의 데이터 목록을 조회하고 수정한다. 웹의 Data library와 같은 목록이다.
사용자가 FASTQ 이동·이름 변경 또는 FASTQ가 들어 있는 폴더 이동을 요청하면 이 스킬을 자동으로 사용한다.
**이동 요청에는 등록된 파일의 허브 경로 갱신도 포함된다.** 사용자가 스킬명이나 DB 갱신을 따로 말할 필요가 없다.
물리 이동은 사용자가 허용한 로컬·SSH 파일 작업으로 수행하고, `catalog_*` MCP 도구는 목록을 갱신한다.
목록 등록·수정만 요청받았다면 실제 파일을 옮기지 않는다.
이 연구실에서는 승인된 사용자 모두 개인 MCP 토큰으로 등록·경로 수정을 할 수 있다.

## 시작

- `catalog_overview`로 프로젝트·종·샘플·데이터 종류와 현재 `revision`을 확인한다.
- `catalog_files`로 기존 경로를 검색하고 필요하면 `catalog_file(file_id)`로 상세 정보를 읽는다.
  검색 결과가 여러 개이면 파일명만 보고 선택하지 않는다. 프로젝트·샘플·절대 경로로 구분한다.
- `catalog_*` 도구가 없으면 Cowork 클라이언트 업데이트와 MCP 재연결이 필요하다.
  쓰기 권한 오류를 DB 직접 수정이나 관리자 토큰 요청으로 우회하지 않는다.
- 사용자가 이동·등록·갱신을 이미 요청했다면 해당 작업과 필요한 경로 동기화의 승인을 다시 묻지 않는다.
  조직·조건·샘플 연결처럼 결과를 바꾸는 정보가 불명확할 때만 해당 정보를 묻는다.

## 파일을 옮길 때 자동 연동

1. **이동 전에** 원본 서버·경로에 대응하는 등록 파일을 찾고 `file_id`, 기존 경로, 크기,
   목적지 서버·경로를 기록한다. 상위 폴더를 옮기면 그 아래 등록된 FASTQ 전체를 대상으로 삼고
   상대 경로를 유지한다. 검색 결과는 필요한 페이지까지 확인하고 정확한 경로/하위 경로로 대조한다.
   `/data/a`의 하위는 `/data/a/`이며 `/data/abc/`를 포함하지 않는다.
   조회 오류를 미등록으로 간주하지 않는다. 목적지의 실제 파일과 카탈로그 경로 충돌도 먼저 확인한다.
2. 사용자가 요청한 범위의 이동·이름 변경을 실행한다. 실패하거나 아직 전송 중인 파일의 DB 경로는 바꾸지 않는다.
   완료된 파일마다 목적지 존재·크기와 이동 결과를 확인한다. 단순 복사로 원본이 남아 있다면 기존 위치를 교체하지 않는다.
3. **별도 요청을 기다리지 않고**, 아래 경로 갱신 및 미리보기·저장 절차로 완료된 등록 파일을 반영한다.
   오래 걸린 이동 후에는 최신 revision을 읽고 기존 ID·경로가 여전히 맞는지 확인한다.
   새 경로와 기존 통계·샘플 연결을 재조회한 뒤 이동과 목록 갱신 결과를 함께 보고한다.
4. 일부 이동만 성공했으면 성공한 파일만 갱신한다. 물리 이동과 DB 저장은 하나의 원자적 작업이 아니므로,
   이동 완료 후 DB 저장이 실패하면 이동 매핑과 요청 키를 로컬 작업 기록에 보존하고 미반영 파일을 알린다.
   같은 파일을 다시 옮기거나 되돌리지 말고, 저장 상태를 확인해 미완료된 경로 갱신부터 재개한다.

미등록 파일도 요청대로 옮길 수 있지만, 샘플·프로젝트를 추측해 자동 등록하지 않는다.
이 규칙은 에이전트가 수행하는 이동에 적용된다. 사용자가 외부 터미널에서 직접 실행한 `mv`를
감시하는 상주 서비스는 아니다. 이미 직접 옮겼다고 알려주면 아래 절차로 목록을 맞춘다.

## 새 FASTQ 등록

1. 프로젝트명, 종명, 샘플 ID/이름, 데이터 종류, 저장 서버의 node ID, 절대 경로,
   실제 파일 크기(bytes)를 준비한다. 기존 이름은 조회 결과의 정확한 이름을 사용한다.
   파일 크기는 해당 서버에서 짧은 `stat` 확인으로 얻는다. 허브는 원격 파일의 존재를 검사하지 않는다.
2. 조직·조건·반복 번호·R1/R2·업체 보고서·수령일·기존 통계는 아는 항목만 작성한다.
   모르는 통계는 생략하면 미계산으로 등록된다. 이번 등록만을 위해 SeqKit을 자동 실행하지 않는다.
   상세 입력과 예시는 [references/fields.md](references/fields.md)를 확인한다.
3. R1/R2는 별도 파일 두 개로 등록하되 같은 반복 번호를 사용한다. R1/R2를 두 반복으로 세지 않는다.
   혼합 조직은 한 혼합 항목이다. 분할·통합 파일의 연결은 확인된 `input_file_ids`로만 설정한다.
   여러 RNA 조직·조건·반복 파일을 임의로 통합 파일 아래에 넣지 않는다.
4. 새 프로젝트·종·샘플을 실제로 만드는 요청일 때만 `create_missing=true`를 사용한다.
   기존 항목의 철자 오류를 신규 항목 생성으로 해결하지 않는다.
5. `catalog_register_files`로 미리 확인한 뒤 아래 저장 절차를 따른다.

## 이동한 파일의 경로 갱신

1. 기존 `file_id`, `path`, `bytes`와 현재 `revision`을 조회한다.
2. 이동 완료 또는 이름 변경이 확인된 **동일 파일**인지 확인한다. 새 서버·절대 경로와 실제 크기를 확인한다.
   사용자에게서 받은 이동 기록 또는 직접 수행한 이동의 결과를 근거로 삼는다.
   크기가 같다는 사실만으로 정체를 모르는 파일을 같은 파일이라고 판단하지 않는다.
   다른 파일로 교체했거나 재압축해 크기가 달라졌다면 경로 갱신으로 처리하지 않는다.
3. `catalog_relocate_files`에 `file_id`, `expected_path`, `new_path`, `node_id`,
   `observed_bytes`, `content_unchanged=true`를 전달한다.
   저장되면 파일 ID·통계·샘플 연결이 유지되고, 이 파일을 참조하던 통합 입력 경로도 함께 바뀐다.
   복사본 추가와 원본 이동은 다르다. 원본도 계속 보관하는 경우 임의로 기존 경로를 교체하지 않는다.

## 미리보기와 저장

- 작업마다 고유한 `request_key`를 만들고 조회한 `expected_revision`을 사용한다.
- 먼저 `dry_run=true`로 호출한다. 응답의 `changes`와 새로 생길 `created` 항목을 확인한다.
  사용자 요청과 일치하면 같은 요청에서 `dry_run=false`만 바꿔 저장한다.
- `applied=true`일 때만 저장 완료라고 말한다. 파일 ID와 새 경로 또는 등록한 파일 수를 전달한다.
  `catalog_file` 또는 목록 재조회로 반영을 확인한다. 필요하면 `catalog_history`로 변경자를 확인한다.
- 네트워크 오류로 접수 여부가 불명확하면 **같은 키·같은 내용**으로 재시도한다.
  `CATALOG_CONFLICT`는 최신 목록을 읽고 다시 검토한다. `IDEMPOTENCY_CONFLICT`는 이전 요청을 먼저 확인한다.
  충돌을 숨기려고 새 키를 계속 만들지 않는다. 변경이 거절됐음이 확실하고 새 내용을 확정한 경우에만 새 키를 쓴다.
- 한 요청은 최대 100개 파일이며 모두 함께 저장되거나 모두 취소된다.
  더 큰 등록은 요청을 나누고, 각 저장 후 새 revision으로 다음 묶음을 검토한다.

계산이 별도로 요청되면 `cowork-jobs` 절차로 예약한다. 등록 및 경로 갱신은 CPU 작업 예약이 아니다.
