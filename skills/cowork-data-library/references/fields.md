# 입력 항목

## 등록

`catalog_register_files(request={request_key, expected_revision, dry_run, create_missing, files})`

각 파일의 필수 항목:

| 항목 | 내용 |
| --- | --- |
| `project` | 프로젝트 이름 |
| `species` | 종명 |
| `sample` | 품종·개체·샘플 ID/이름 |
| `data_type` | 예: `ONT`, `HiFi`, `PacBio CLR`, `WGS short read`, `RNA-seq`, `Hi-C`, `Omni-C` |
| `node_id` | 허브에 등록된 저장 서버 ID |
| `path` | 절대 `.fastq`, `.fq`, `.fastq.gz`, `.fq.gz` 경로 |
| `bytes` | 파일의 실제 저장 크기 |

선택 항목:

- `read_part`: `R1`, `R2`, `I1`, `I2`, 또는 미기록 `""`.
- `tissue`: `leaf`, `root`, `flower`, `stem`, `haustoria`, `seedling` 등.
  줄기·뿌리·꽃 혼합은 `stem+root+flower`이며 세 개의 독립 조직 데이터가 아니다.
- `condition`, `condition_description`: 처리 조건과 설명. 조직 정보와 구분한다.
- `replicate`, `replicate_status`: 반복 번호 문자열과 `unknown`/`inferred`/`confirmed`.
  숫자 파일명을 발견했다고 생물학적 반복을 confirmed로 만들지 않는다.
- `match_status`: 샘플·데이터 유형 연결이 `inferred`인지 `confirmed`인지. 기본은 inferred.
- `quality_group`: `pass`, `fail`, 또는 미기록 `""`.
- `role`: `independent`(기본), `source`, `merged`.
  `input_file_ids`는 같은 샘플·데이터 종류에 등록된 통합 입력 파일의 ID 목록이다.
- `stats`: 계산이 완료된 경우에만 `{reads, bases, q30_percent?, computed_at}`.
  `computed_at`은 시간대가 있는 ISO 날짜·시간이다. Q30은 0–100의 백분율이다.
  빈 파일·미계산 파일의 Q30을 0으로 만들지 않는다.
- `reports`: `[{id, provider, report_date?, received_date?}]`. 날짜는 `YYYY-MM-DD`.
  업체 보고서 날짜와 실제 수령일을 구분한다.
- `notes`: 기타 확인한 사항. 토큰·웹훅·인증정보를 넣지 않는다.

미리보기 예시(실제 revision·서버 ID·파일 크기로 대체):

```json
{
  "request_key": "rice-rna-registration-001",
  "expected_revision": "조회 응답의 revision",
  "dry_run": true,
  "create_missing": false,
  "files": [{
    "project": "Green-rice", "species": "Oryza sativa", "sample": "Baegilmi",
    "data_type": "RNA-seq", "node_id": "226",
    "path": "/nas/project/baegilmi_leaf_rep1_R1.fq.gz", "bytes": 123456,
    "tissue": "leaf", "replicate": "1", "replicate_status": "inferred", "read_part": "R1"
  }]
}
```

## 경로 갱신

`catalog_relocate_files`는 `request_key`, `expected_revision`, `dry_run`, `files`를 받는다.
각 원소는 `file_id`, `expected_path`, `new_path`, `node_id`, `observed_bytes`, `content_unchanged=true`다.
내용이 달라진 파일의 예전 통계를 재사용하는 용도로 사용할 수 없다.

현재 목록은 절대 경로의 중복 등록을 거부한다. 서로 다른 서버에서 같은 절대 경로가 발견돼도
임의로 합치거나 기존 경로를 교체하지 말고 경로 충돌을 사용자에게 알린다.
`CATALOG_METADATA_INCOMPLETE`는 기존 RNA 파일의 상세 메타데이터 보강이 필요한 상태다.
기존 조직·조건 정보를 지우거나 새 샘플을 만들어 우회하지 않는다.
