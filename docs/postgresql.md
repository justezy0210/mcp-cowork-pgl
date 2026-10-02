# PostgreSQL 전환

PostgreSQL 저장 계층, SQLite 스키마 4 이전 도구와 Compose 전환 스크립트를 구현했다.
2026-09-29 최초 계정 등록 확장으로 현재 스키마는 6이다. PostgreSQL 4·5는 시작 시 관리 테이블을 추가해
6으로 확장한다. 새 SQLite → PostgreSQL 이전은 먼저 SQLite 허브를 현재 스키마로 업데이트한 뒤 수행한다.
2026-09-21 운영 허브의 전환 후 PostgreSQL 상태와 기존 개인 토큰을 통한 조회를 확인했다.
다른 SQLite 설치를 전환할 때는 실제 허브 호스트에서 아래 명령을 실행한다. 종자·샘플·재고와 raw 데이터
위치 추적 기능의 테이블·API는 후속 구현 범위이며, 이번 이전 대상은 기존 허브 원장이다.

## 구성

- `compose.postgres.yaml`은 PostgreSQL 17 컨테이너와 영속 `postgres-data` 볼륨을 추가한다.
- DB 포트는 호스트에 공개하지 않는다. 같은 Compose 네트워크의 허브가 `postgres:5432`로 접속한다.
- DB 관리자와 허브 계정의 비밀번호를 각각 생성한다. 허브 계정 `cowork`는 DB 소유자이며 슈퍼유저·역할 생성·DB 생성 권한은 없다.
- 관리자·앱 비밀번호 파일은 호스트 `.local/postgres/`에 모드 0600으로 저장한다. 이 디렉터리와 `.local`은 모드 0700이어야 한다. 비밀번호를 출력하거나 Git에 넣지 않는다.
- 허브 접속 URL은 허브 볼륨의 `/data/postgres.url`에 모드 0600, UID/GID 10001로 저장한다. 일반 사용자와 MCP·CLI에는 DB 자격증명을 배포하지 않는다.
- `/data/database-backend`는 전환 사실을 남긴다. 접속 설정 누락이나 PostgreSQL 장애가 발생해도 SQLite로 자동 복귀하지 않는다.
- 원본 SQLite, 이전 직전 SQLite 백업, 기존 `/data/admin.token`과 사용자 자격증명 파일을 보존한다. 실제 raw 파일과 계산 컨테이너는 이 스크립트가 변경하지 않는다.

SQLite처럼 허브의 쓰기 트랜잭션을 직렬화하여 예약 검사와 확정 사이의 중복 배정을 막는다.
PostgreSQL 트랜잭션 advisory lock을 사용하며, 읽기는 일관된 스냅샷을 사용한다.
중앙 배정·알림 루프는 여전히 단일 허브 프로세스다. PostgreSQL을 도입했다고 여러 허브 복제본을
동시에 실행하는 구조로 바뀌지 않으며, DB 세션 잠금으로 중복 실행을 거절한다.
DB 재시작 등으로 이 세션 잠금 연결을 잃으면 추가 DB 작업을 거절한다. DB 복구 후 허브를
재시작해야 하며, Docker의 `unhealthy` 상태만으로 자동 재시작되지는 않는다.

## 실제 허브에서 실행

`cowork-mcp-hub-1`을 운영하는 **기존 Compose 호스트의 저장소 디렉터리**에서 실행한다.
사용자 계산 컨테이너나 다른 Docker 데몬에서 실행하지 않는다. 해당 호스트에 이 변경 코드가 있어야 한다.
기존 `.local/compose.local-runner.yaml`의 Discord·Firebase 설정은 함께 적용한다.
Docker Compose v2의 `--wait` 옵션이 필요하다.

```sh
cd /10Gdata/ezy/01_Programs/cowork-mcp
python3 scripts/enable_postgres.py --apply
```

첫 전환은 스키마 6의 SQLite에서 활성 작업(`DISPATCHING`, `RUNNING`, `UNKNOWN`)과 대기 작업이 없을 때만 가능하다.
실행·대기 작업을 자동 취소하지 않으며, 있으면 초기 확인 단계에서 중단한다. 전환 중에는
신규 작업 제출과 관리 스크립트 실행을 멈춘다. 이미 켜진 연결 프로그램은 API 복구 후 다시 연결한다.

스크립트는 다음을 수행한다.

1. 기존 허브 접근, SQLite 스키마 4와 빈 작업 대기열을 확인한다.
2. PostgreSQL 지원 허브 이미지를 빌드하고 별도 PostgreSQL 컨테이너를 준비한다.
3. 비슈퍼유저 앱 계정의 접속을 확인하고 접속 설정을 `/data/postgres.url.pending`에 준비한다.
4. 허브를 중지한 뒤 작업이 새로 접수되지 않았는지 다시 검사한다.
5. 원본 SQLite의 쓰기를 잠그고 별도 백업을 생성한다. **비어 있는 PostgreSQL 원장에만** 이전한다.
6. 각 테이블의 모든 행을 비교하고, ID·토큰 해시·대기 순서·삭제된 ID의 자동 증가 상한까지 보존한다. 검증 실패 시 대상 DB의 행 변경을 롤백한다.
7. PostgreSQL 설정을 활성화하고 허브를 재시작한다. DB 접속·기존 관리자 토큰·HTTP 상태를 확인한다.

성공 후 기존 MCP와 실행기의 허브 주소·개인 토큰은 그대로 사용한다. 다음 요청에서
`{"status":"ok","database":"postgresql"}`을 확인할 수 있다.

```sh
curl -fsS http://192.168.10.41:8080/healthz
```

## 실패·중단 후 처리

- 초기 확인·빌드·DB 준비 중 실패하면 기존 허브를 계속 사용한다.
- 허브 중지 후 활성화 전 확정된 실패가 발생하면 기존 SQLite 컨테이너를 다시 시작한다. 대상 PostgreSQL에 이전이 완료됐을 수도 있으므로, 재시도 시 빈 DB 검사가 거절하면 대상 상태를 먼저 확인한다.
- 명령 시간 초과는 결과가 불확실하므로 자동 복귀하지 않는다. `docker compose ps -a`와 해당 전환용 일회성 컨테이너 상태를 확인한 뒤 처리한다.
- 활성화 시도 후 실패하면 두 DB와 설정 파일을 보존한다. PostgreSQL 서비스·`/data/postgres.url`·기존 환경 설정을 점검하고 PostgreSQL 허브를 복구한다.
- **PostgreSQL에서 새 기록을 받은 후에는 옛 SQLite를 연결하는 방식으로 복구하지 않는다.** 새 기록을 잃을 수 있다. PostgreSQL 백업 복원 또는 이후 기록을 반영하는 별도 복구가 필요하다.
- 기존 DB·볼륨·자격증명은 자동 삭제하지 않는다. 비밀번호가 포함되는 `postgres.url`이나 전체 Compose 설정을 대화·로그에 출력하지 않는다.

## 전환 후 재시작·업데이트·백업

기존 환경 설정과 PostgreSQL 구성을 함께 사용한다. 로컬 설정 파일이 없는 설치는 해당 `-f`만 생략한다.

```sh
docker compose -f compose.yaml -f .local/compose.local-runner.yaml -f compose.postgres.yaml up -d --wait
```

업데이트 전에는 PostgreSQL 컨테이너의 `pg_dump -U postgres -d cowork -Fc`로 논리 백업을 만든다.
출력은 터미널에 표시하지 않고 접근이 제한된 **새 백업 파일**에 저장한다. 복원 검증은 운영 DB가
아닌 별도 시험 DB에서 수행한다. 예를 들어 파일명이 겹치지 않게 선택한 뒤 아래처럼 실행할 수 있다.

```sh
umask 077
mkdir -p .local/backups
set -C
docker compose -f compose.yaml -f .local/compose.local-runner.yaml -f compose.postgres.yaml exec -T postgres pg_dump -U postgres -d cowork -Fc > .local/backups/cowork-before-update.dump
```

명령 성공과 백업 파일을 확인한 뒤 `up -d --build --wait hub`로 허브만 업데이트한다.
DB 덤프 외에 `/data`의 Discord·Firebase 설정, 토큰 파일, PostgreSQL 접속 설정과 호스트의
비밀번호 파일도 별도의 보호된 백업 대상으로 관리한다. 원본 raw 파일의 백업은 별도 정책이다.
`enable_local_runner.py --upgrade`의 SQLite 백업 경로는 PostgreSQL 허브에서 거절한다.

## 관리형 PostgreSQL 또는 Docker 외 실행

`cowork-hub[postgres]`를 설치하고, 보호된 파일에 PostgreSQL 접속 URL을 저장해
`HUB_DATABASE_URL_FILE`로 지정한다. 관리형 서비스는 제공자의 TLS·인증 설정을 따른다.
새 DB는 `cowork-hub init`, 기존 허브 이전은 다음 명령을 사용한다.

```sh
cowork-hub migrate-postgres --data-dir /private/hub-data --database-url-file /private/postgres.url
```

이 명령은 데이터만 이전한다. 원본 허브를 중지하고 백업·이전 결과를 확인한 뒤 새 허브가 같은
접속 설정을 사용하도록 배포한다. 현재 이전 도구는 스키마 4와 빈 대상 DB만 지원하며, 자동 병합하지 않는다.

## 검증

기본 테스트는 SQLite로 실행한다. `COWORK_TEST_POSTGRES_URL`을 **폐기 가능한 시험 DB**로 지정하면
공통 예약·인증·실행기·MCP·웹 테스트가 PostgreSQL에서도 실행된다. 각 테스트는 고유 스키마를 만들고 정리한다.
`tests/test_postgres.py`는 실제 PostgreSQL에서 이전·중복 실행 방지·롤백·동시 예약·초기화를 검증한다.

2026-09-21 최종 결과: 190개 통과, PostgreSQL에 적용되지 않는 SQLite 구버전 이전 테스트 2개 제외.
격리된 Compose 프로젝트에서도 실제 전환 스크립트·기존 토큰과 서버 등록 유지·재시작 후 접속·
중복 전환 거절을 확인했다.

같은 날 사용자가 운영 전환 명령을 실행한 뒤 내부망 허브를 읽기 전용으로 점검했다.
`/healthz`는 `{"status":"ok","database":"postgresql"}`로 응답했다.
기존 개인 토큰으로 실제 stdio MCP 연결·10개 도구 목록과 상태·서버·환경·작업·알림 조회를 확인했다.
`ezy`의 UID/GID `1101:1100`, 허용 서버 `224`·`226`·`228`·`229`, 기존 ID의 READY 환경 4개,
작업 기록 41개와 Discord 목적지 설정이 조회됐으며, `ezy-226` 연결 프로그램도 온라인이다.
전환 후 새 작업 제출·실행·Discord 실제 전송은 이 점검에서 수행하지 않았다.

공식 참고: [PostgreSQL 잠금](https://www.postgresql.org/docs/17/explicit-locking.html),
[Psycopg 트랜잭션](https://www.psycopg.org/psycopg3/docs/basic/transactions.html),
[PostgreSQL 공식 이미지](https://hub.docker.com/_/postgres).
