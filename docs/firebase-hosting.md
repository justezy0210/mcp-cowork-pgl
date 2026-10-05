# Firebase Hosting과 허브 API

## 운영 경로

웹 화면과 Google 로그인은 Firebase를 사용한다. 웹 API 요청은 브라우저에서
`https://203.255.11.226/v1/web/`로 직접 보낸다. 허브는 Firebase ID 토큰과 사용자 권한을
검사한다. CORS는 운영 웹 출처만 허용하며 인증을 대체하지 않는다.

```text
Firebase Hosting 웹앱 → 사용자 브라우저 → nginx HTTPS 443 → 내부 허브 8080
```

226 서버의 nginx는 웹 API 경로만 내부 허브로 전달하고 다른 경로는 404로 차단한다.
MCP와 계산 실행기는 기존 내부 허브 연결을 사용한다.

## HTTPS 인증서

Let’s Encrypt IP 인증서는 `/etc/letsencrypt/live/cowork-api-ip/`에 있으며,
`snap.certbot.renew.timer`가 갱신을 확인한다. 갱신 성공 후
`nginx -t && systemctl reload nginx` 훅으로 인증서를 반영한다.
HTTP 80의 `/.well-known/acme-challenge/`는 갱신 검증을 위해 유지한다.
이 설정은 기존 도메인별 HTTP 웹서비스와 함께 사용한다.

## 빌드와 배포

저장소 루트에서 실행한다. 비공개 설정의 `api_base_url`은 위 HTTPS 주소로,
`allowed_origins`는 `https://mcp-cowork-pgl.web.app`으로 설정한다.

```sh
npm --prefix frontend run build
.venv/bin/python scripts/build_web.py \
  --config .local/web.firebase.pending.json \
  --output .local/firebase-release-NEW
.local/firebase deploy \
  --config .local/firebase-release-NEW/firebase.json \
  --project mcp-cowork-pgl --only hosting --non-interactive
```

빌드 도구는 새 출력 디렉터리에 정적 파일·공개 설정·설치 파일과 Hosting 헤더만 생성한다.
사용자 연결 목록, 서비스 계정 키, 비공개 카탈로그는 공개 출력에 포함하지 않는다.
명시적인 HTTPS API 주소가 필요하며 API 주소와 CSP의 `connect-src`를 함께 설정한다.
Firebase Functions나 API 중계 규칙은 생성하지 않는다.

배포 도구는 기존 인증 환경의 `.local/firebase`를 사용한다. 226 호스트에 별도
Firebase CLI 로그인은 필요하지 않다. 웹 빌드·배포만으로 허브를 재시작하지 않는다.
카탈로그 갱신은 [데이터 라이브러리 안내](data-catalog.md)를 따른다.

## 검증과 복구

- Hosting의 공개 `config.json`과 CSP가 직접 HTTPS API 주소를 가리키는지 확인한다.
- 인증 없는 API 요청은 401, 허용된 웹 출처의 OPTIONS 요청은 성공해야 한다.
- 한국어·영어 화면, 모바일 배치, 로그인 복원과 프로젝트·파일 탐색을 확인한다.
- 합성 로그인으로 수행한 브라우저 검증과 실제 사용자 인증 검증을 구분해 기록한다.
- 새 배포를 검증한 뒤 로컬 배포 산출물은 현재 운영본과 직전 정상본을 보존한다.
  조사 결과·카탈로그 원본·검증 기록·인증정보·DB 백업은 산출물 정리에서 제외한다.

정적 웹 복구는 보존한 직전 정상본의 `firebase.json`으로 Hosting만 다시 배포한다.
허브 데이터베이스나 비공개 카탈로그를 덮어쓰지 않는다.

이전 SSH 중계 함수와 Cloud Run 서비스는 2026-10-05 삭제했고 관련 소스·빌드 옵션도 제거했다.
삭제 검증 기록은 `.local/verification/web-ip-api-20261005/relay-deleted.json`에 있다.
이전 중계 방식의 Hosting 릴리스는 복구 대상으로 사용하지 않는다.
