# AGENTS.md

작업 전에 끝까지 읽으세요. 이 문서는 다음 에이전트가 이전 대화를 보지 않아도 프로젝트를 안전하게 수정할 수 있도록 현재 구조, 불변 조건, 검증 상태를 정리한 인수인계 문서입니다.

## 1. 프로젝트 목적과 경계

CardioSOAP Vet은 수의사가 심장질환 진료 SOAP를 S → O → A → P 순서로 작성하도록 돕는 단일 관리자용 웹 서비스입니다.

- AI 출력은 초안이며 자동 진단·자동 처방이 아닙니다. 사용자가 각 단계를 선택·수정·확정해야 합니다.
- 환자·진료·SOAP·감사 이력의 원본은 MariaDB입니다.
- Qdrant는 의학 PDF에서 언제든 재생성할 수 있는 검색 인덱스입니다.
- X-ray 원본은 외부 AI에 보내지 않습니다. 수의사가 쓴 `reading_text`만 AI 컨텍스트에 포함합니다.
- 같은 환자는 이름 비교가 아니라 `patients.id`/`encounters.patient_id`로 식별합니다.

## 2. 현재 실행 환경

- 호스트: Synology NAS x86_64
- 프로젝트: `/volume1/docker/emr2`
- 호스트 사용자: `crosssoldier`(uid 1026)
- 외부 웹 포트: `8081` → 컨테이너 `5000`
- 앱 런타임: Python 3.10, Flask, Gunicorn
- DB: MariaDB 10.6
- 벡터 DB: Qdrant 1.12.6
- 프런트엔드: 빌드 없는 HTML/CSS/바닐라 JavaScript
- 현재 AI: OpenAI 호환 API, `gpt-5.6-terra`
- 현재 임베딩: `text-embedding-3-large`(3,072차원)
- 현재는 직접 HTTP 테스트 환경입니다. `SESSION_COOKIE_SECURE=0`, 로그인 잠금 0분이며 운영 전 반드시 강화해야 합니다.

Docker Compose 서비스는 네 개입니다.

- `db` / `app-db`: 임상 원본 DB
- `qdrant` / `app-qdrant`: PDF 벡터 인덱스
- `web` / `app-web`: Gunicorn API + 정적 UI
- `worker` / `app-worker`: MariaDB 작업 큐를 폴링하여 PDF 색인

MariaDB와 Qdrant 포트는 호스트에 공개하지 않습니다.

## 3. 절대 지켜야 할 데이터·보안 규칙

1. `.env`, `db_data/`, `storage/xrays/`, `storage/knowledge/inbox/`, `storage/qdrant/`를 커밋하지 마세요.
2. `db_data/`를 삭제하지 마세요. 삭제는 모든 임상 데이터 손실이며 사용자의 명시적 요청이 필요합니다.
3. 비밀번호, API 키, 환자 본문을 코드·로그·예제 설정에 출력하지 마세요.
4. X-ray 파일을 LLM/임베딩 요청에 넣지 마세요.
5. Qdrant를 임상 데이터 원본으로 사용하지 마세요.
6. 기존 적용된 SQL migration을 수정하지 말고 `sql/002_...sql`처럼 새 파일을 추가하세요.
7. SOAP 단계 순서와 stale 전이는 서버에서 강제해야 합니다. UI 제어만 믿지 마세요.
8. 모든 임상·파일·관리 API는 세션 인증 대상입니다. 공개 예외는 `/api/health`, `/api/auth/login`뿐입니다.
9. POST/PUT/PATCH/DELETE에는 `X-CSRF-Token` 검증을 유지하세요.
10. NAS 프로젝트 밖의 시스템 설정이나 파일을 건드리지 마세요.

## 4. 코드 지도

- `wsgi.py`: Gunicorn 진입점. `Dockerfile`은 반드시 `wsgi:app`을 사용합니다.
- `app.py`: 컨테이너에서 직접 개발 실행할 때의 진입점
- `app/__init__.py`: application factory, Blueprint 등록, migration, 보안 헤더, health
- `app/config.py`: 모든 환경변수와 제한값
- `app/db.py`: PyMySQL 연결, transaction, migration runner, audit helper
- `app/auth.py`: 단일 관리자 생성, 로그인 잠금, Flask session, CSRF
- `app/clinical.py`: 환자·질병·진료·X-ray API와 파일 검증
- `app/soap.py`: SOAP 상태 머신, 컨텍스트 조립, RAG 검색, 후보/확정 이력
- `app/ai.py`: LLM/임베딩 어댑터와 Qdrant REST client
- `app/indexing.py`: PDF 추출·청킹·임베딩·증분/전체 색인
- `app/admin.py`: 상태와 색인 작업 API
- `app/json_provider.py`: datetime/date를 timezone 없는 ISO 문자열로 응답
- `worker.py`: queued 색인 작업 claim/run loop
- `sql/001_initial.sql`: 현재 전체 스키마
- `static/index.html`: 단일 화면 마크업
- `static/css/app.css`: 전체 UI 스타일
- `static/js/app.js`: API client와 화면 상태
- `tests/test_services.py`: 현재 자동 테스트 10개

ORM은 사용하지 않습니다. SQL은 repository 계층 없이 각 모듈에서 PyMySQL helper로 실행합니다.

## 5. 핵심 데이터 모델

- 인증: `admins`, `audit_events`
- 임상: `patients`, `diseases`, `encounters`, `xray_assets`
- SOAP: `soap_documents`, `soap_sections`, `soap_section_revisions`
- AI 이력: `ai_generation_runs`, `ai_candidates`, `candidate_evidence`
- 지식베이스: `kb_documents`, `kb_document_versions`, `kb_chunks`
- 작업 큐: `index_jobs`, `index_job_items`

모든 외부 노출 ID는 UUID 문자열입니다. 파일은 DB BLOB이 아니라 `storage/`에 두고 DB에는 상대 storage key와 SHA-256을 저장합니다.

## 6. SOAP 상태와 컨텍스트 규칙

단계는 `S`, `O`, `A`, `P` 고정입니다.

- 이전 단계가 모두 `confirmed`일 때만 다음 단계 후보를 생성·확정할 수 있습니다.
- 후보 생성 후 section은 `generated`입니다.
- 선택하거나 편집한 텍스트를 확정하면 불변 revision을 추가하고 section은 `confirmed`입니다.
- 앞 단계를 재생성·재확정하면 뒤 단계의 기존 내용은 삭제하지 않고 `stale`로 표시합니다.
- 질병, 주호소, History, 신체검사 내용을 수정해도 기존 SOAP 전체가 `stale`이 됩니다.
- 네 단계가 모두 `confirmed`일 때만 encounter/document가 `completed`입니다.

현재 단계의 LLM 컨텍스트는 다음으로 구성됩니다.

1. 환자 기본정보
2. 현재 진료의 질병, 주호소, History, 신체검사
3. X-ray `reading_text`(이미지 제외)
4. 현재 단계보다 앞선 확정 SOAP
5. 같은 `patient_id`의 과거 진료 중 현재 진료일보다 이전인 확정 SOAP 최대 5건
6. Qdrant Top-K 의학 문서 근거

이전 SOAP 테스트는 같은 환자 화면에서 `새 진료`를 만들어야 합니다. 같은 이름의 새 환자를 만들면 다른 환자입니다.

## 7. PDF RAG 규칙

- 웹 PDF 업로드는 없습니다. PDF를 `storage/knowledge/inbox/`에 직접 복사합니다.
- `pypdf`로 페이지 텍스트를 추출하고 약 3,200자, 400자 overlap으로 청킹합니다.
- 스캔 PDF OCR은 지원하지 않습니다.
- 증분 갱신은 파일 SHA-256, 임베딩 모델, chunker version이 같으면 skip합니다.
- 임베딩 모델 또는 차원이 바뀌면 증분 갱신하지 말고 전체 재색인해야 합니다.
- 전체 재색인은 새 컬렉션을 완성한 뒤 `kb_active` alias를 원자적으로 전환합니다.
- Qdrant alias 조회는 존재하지 않는 `/aliases/{name}`이 아니라 `GET /aliases` 결과를 필터링합니다.
- 대형 PDF는 Qdrant upsert를 32개 point 배치로 나눕니다. timeout은 120초입니다.
- worker를 재시작하면 `running` 작업을 `queued`로 되돌립니다. worker는 한 개만 실행하세요.
- 진행률은 현재 파일 단위입니다. 한 개의 113MB PDF는 오랫동안 `running`으로 보일 수 있습니다.

현재 `심장학현창백 내과.pdf` 약 113MB를 실제 색인했습니다. `pypdf`가 `/KSC-EUC-H`, `/KSC-EUC-V` 경고를 냈으므로 한글 chunk와 인용문이 읽을 수 있는지 계속 확인해야 합니다.

## 8. 주요 API

- 인증: `/api/auth/login`, `/logout`, `/me`
- 환자: `GET/POST /api/patients`, `GET/PATCH /api/patients/<id>`
- 질병: `GET/POST /api/diseases`, `PATCH /api/diseases/<id>`
- 진료: `GET/POST /api/encounters`, `GET/PATCH /api/encounters/<id>`
- X-ray: `/api/encounters/<id>/xrays`, `/api/xrays/<id>`, `/file`
- SOAP: `/api/encounters/<id>/soap`, `/soap/<stage>/candidates`, `/confirm`
- 관리: `/api/admin/status`, `/api/admin/index-jobs`

API 응답/요청 구조는 해당 route 함수와 `static/js/app.js` 호출부를 함께 확인하세요.

## 9. 환경변수와 관리자 주의

- 실제 비밀은 `.env`에만 있습니다. 파일 내용을 채팅이나 로그로 출력하지 마세요.
- 필수: `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, LLM/임베딩 API 키
- `ADMIN_PASSWORD`는 admins 테이블이 비어 있을 때 최초 관리자 생성에만 사용됩니다.
- `.env`의 관리자 비밀번호를 바꿔도 DB의 기존 비밀번호 해시는 바뀌지 않습니다.
- `.env` 변경 후 `restart`만 하면 환경변수가 갱신되지 않습니다. `sudo docker compose up -d --force-recreate web worker`를 사용하세요.
- 현재 테스트 설정의 `LOGIN_LOCK_MINUTES=0`은 운영 전에 15 이상으로 바꾸세요.
- HTTPS 적용 후 `SESSION_COOKIE_SECURE=1`로 바꾸세요.
- GPT-5.6 계열과 호환되도록 Chat Completions 요청에 `temperature`를 보내지 않습니다.

## 10. 개발·검증 명령

에이전트 셸은 Docker socket 권한이 없습니다. Docker 작업은 사용자에게 아래 명령을 실행해 달라고 요청하세요.

```bash
# 전체 재빌드: requirements/Dockerfile/Compose 변경
sudo docker compose up -d --build

# Python 코드 변경
sudo docker compose restart web worker

# .env 변경
sudo docker compose up -d --force-recreate web worker

# 테스트
sudo docker compose run --rm web pytest

# 로그
sudo docker compose logs -f web worker

# health
curl -s http://127.0.0.1:8081/api/health
```

호스트 Python 3.8에는 앱 의존성이 없습니다. 호스트에서는 앱·pytest를 실행하지 말고 다음 정적 검사만 합니다.

```bash
python3 -m py_compile app.py wsgi.py worker.py app/*.py tests/*.py
node --check static/js/app.js
docker compose config --quiet
git diff --check
```

Python 변경 후 관련 컨테이너 재시작, 정적 파일 변경 후 브라우저 강력 새로고침, 마지막으로 pytest를 요청하세요.

## 11. 이미 검증한 상태

- Compose 4개 서비스 기동과 DB health 정상
- 단위 테스트 `10 passed`
- 로그인/세션/CSRF 정상
- 실제 `gpt-5.6-terra` 후보 생성 정상
- 실제 `text-embedding-3-large` 3,072차원 생성 정상
- Qdrant health와 PDF 증분 색인 정상
- S→O→A→P 완료, 앞 단계 수정 stale 처리 정상
- 같은 환자의 이전 SOAP가 다음 진료 컨텍스트에 들어가는 것 확인
- 진료 기본정보 재조회·수정 UI 확인
- X-ray는 실제 샘플이 없어 end-to-end 미검증

## 12. 알려진 한계와 다음 우선순위

1. PDF 색인 진행률이 파일 단위뿐입니다. 페이지/chunk 진행률이 필요합니다.
2. 한국어 구형 CMap PDF 추출 품질을 미리보기로 검증해야 합니다. 필요하면 PyMuPDF fallback 또는 OCR을 추가하세요.
3. 테스트 환자·진료·질병을 삭제/보관하는 UI/API가 없습니다.
4. 대용량 PDF 웹 업로드는 없습니다. 추가한다면 단순 multipart가 아니라 분할·재개 업로드가 필요합니다.
5. 실제 X-ray 업로드와 판독 소견의 SOAP 반영은 아직 검증하지 않았습니다.
6. 단일 관리자 비밀번호 변경 UI가 없습니다.
7. 운영 전 HTTPS, Secure cookie, 로그인 잠금, 별도 최소권한 DB 계정, 백업을 구성해야 합니다.
8. 고정 임상 질문 세트로 RAG 검색 품질과 SOAP 환각을 평가하는 회귀 테스트가 없습니다.

## 13. 이미 겪은 함정

- `gunicorn app:app`은 루트 `app.py` 대신 `app/` 패키지와 충돌합니다. `wsgi:app`을 유지하세요.
- async DOM handler에서 `await` 뒤 `event.currentTarget`은 `null`일 수 있습니다. 먼저 `const form = event.currentTarget`처럼 캡처하세요.
- 로그인 성공 후 초기 데이터 로딩 오류를 자격 증명 실패로 표시하지 마세요.
- 정적 파일 캐시 때문에 수정이 안 보일 수 있습니다. 현재 응답은 `no-store`지만 강력 새로고침도 사용하세요.
- 113MB PDF의 모든 vector를 한 번에 Qdrant에 보내면 timeout이 납니다. 배치 upsert를 제거하지 마세요.
- OpenAI API 키를 `.env`에서 바꾼 뒤 container restart만 하면 이전 키가 남습니다. force recreate가 필요합니다.

## 14. Git

- 브랜치: `main`
- 원격: `https://github.com/chaejungyong/emr2.git`
- 사용자 요청 없이 commit/push하지 마세요.
- `.env`나 runtime 의료 데이터를 stage/commit하지 마세요.
