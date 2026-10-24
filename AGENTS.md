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
- 대형 PDF는 한 파일을 처리하는 동안에도 페이지 추출 및 청크 임베딩·저장·업로드 진행률을 표시합니다.

현재 inbox에는 Ettinger 8판과 Small Animal Critical Care Medicine 영문 PDF 두 개가 있습니다. 대표 페이지 브라우저 미리보기에서 본문 추출은 확인했지만, 표·수식의 일부 기호와 줄바꿈은 손상될 수 있으므로 숫자·단위 근거는 원문과 대조해야 합니다.

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

1. 관리자 도구의 PDF 대표 페이지(첫쪽·중간쪽·마지막쪽) 추출 미리보기는 현재 영문 PDF 두 권으로 브라우저 확인했습니다. 본문은 읽을 수 있지만 표·수식의 기호와 강제 줄바꿈은 자동 품질 신호가 잡지 못할 수 있습니다. 필요하면 PyMuPDF 비교 추출을 추가하세요.
2. 실제 X-ray 업로드와 판독 소견의 SOAP 반영, Echo 영상의 브라우저 E2E는 아직 검증하지 않았습니다.
3. 고정 임상 질문 세트로 RAG 검색 품질과 SOAP 환각을 평가하는 회귀 테스트가 없습니다.
4. 대용량 PDF 웹 업로드는 없습니다. 추가한다면 단순 multipart가 아니라 분할·재개 업로드가 필요합니다.
5. 운영 전 HTTPS, Secure cookie, 로그인 잠금, 별도 최소권한 DB 계정, 암호화 백업과 복구훈련을 구성해야 합니다.

PDF 색인은 페이지 추출 및 청크 임베딩·저장·업로드 진행률을 표시합니다. 환자는 삭제하지 않고 보관·복원할 수 있으며, 수의사 관리자는 사용자 관리 화면에서 비밀번호를 변경할 수 있습니다.

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

## 15. 상용 파일럿 워크플로 변경(2026-10-09)

- `sql/007`~`010`은 각각 심장검사, 임상 워크플로, 환자 심장 프로필, 상용 상태 머신을 추가합니다. 기존 migration은 수정하지 마세요.
- 기본 화면은 `static/cardio.html`이며 `app/commercial.py`가 예약 접수, 단계 전환, 검사 검토, 보호자 설명, 구조화 처방, 청구·결제·환불·일마감을 담당합니다.
- encounter 업무 단계는 `intake → examination → diagnostics → documentation → education → checkout → closed`입니다. `encounters.status`는 SOAP 완료 호환 필드이고 최종 진료 종료 여부는 `workflow_stage='closed'`로 판단합니다.
- 기존 완료 진료는 migration 010에서 `closed`로 이관합니다. 기존 `billing_records`와 자유형 처방 API는 호환용으로 보존하며 새 UI는 `invoices`, `payments`, `prescription_items/revisions`를 사용합니다.
- 수의사만 검사 검토, SOAP 생성·확정, 구조화 처방 발행, 설명서 생성을 할 수 있습니다. 스태프는 접수·예약·인구학 정보·설명서 교부·청구·결제를 담당합니다.
- 외부 AI 컨텍스트에서 환자 이름·차트번호·보호자 정보를 제외하고 현재 약물·알레르기·예방정보를 포함합니다.
- Echo 영상 경로 `storage/echo-videos/`는 Git 제외 대상입니다.
- 운영 절차는 `docs/PILOT_OPERATIONS.md`를 따릅니다. 실제 파일럿 전 HTTPS, Secure cookie, 최소권한 DB 계정, 암호화 외부백업과 복구훈련이 필요합니다.
- 접수→검사→SOAP→처방→설명→수납→종료 흐름에는 단계별 서버 가드가 적용되어 있습니다. 진행 중 진료의 reopen 우회, 종료 후 임상 수정, 결과 없는 검사 검토, SOAP/검사/처방 변경 후 하위 산출물 재사용, 스태프의 X-ray 판독 입력을 차단합니다.
- 심장검사 저장 시 `workflow_stage` 누락, 검사 결과 변경 후 검토 상태 유지, 구형 자유형 처방의 상용 단계 우회를 수정했습니다. 검사 결과 변경은 해당 검사 요구사항을 `completed`(결과 삭제 시 `planned`)로 되돌리고 검토자·검토시각을 제거합니다. 상용 처방 단계는 구조화 revision이 있는 발행/불필요 처방만 인정합니다.
- 정적 검사와 Docker pytest 73개가 통과했고, 현재 영문 PDF 두 권의 대표 페이지 미리보기를 브라우저에서 확인했습니다. 실제 MariaDB migration과 X-ray/Echo 브라우저 E2E는 아직 실행하지 않았습니다.
- `sql/012`와 `clinical-book-v2`는 Ettinger 8판 두 권 및 Critical Care 교재를 출처 유형·판본·권·장·페이지와 함께 색인합니다. SOAP RAG는 단계·종·질환별 다중 질의, Ettinger 우선 가중치, 문서별 결과 제한, 급성 CHF/호흡곤란의 Critical Care 질의를 사용합니다. 적용 후 반드시 전체 재색인하고 근거 표시를 확인하세요.

## 16. 읽기 전용 AI 진료 점검(2026-10-18)

- `app/agent.py`와 `sql/013_agent_review_runs.sql`은 수의사 전용 읽기 전용 진료 점검을 추가합니다.
- `POST /api/encounters/<id>/agent-review`는 누락 정보, 미검토 검사, 이전 수치 변화, 문헌 근거와 수의사 확인 질문을 생성하며 `GET`은 최근 성공 결과를 조회합니다.
- 이 에이전트에는 임상기록 수정, 진단 확정, 처방 발행, 워크플로 전환 도구가 없습니다. 기존 SOAP 확정·처방·종료 서버 가드를 우회하지 마세요.
- 외부 AI 및 실행 스냅샷으로 보내기 전에 이름, 차트번호, 보호자 정보와 내부 환자·진료 ID를 제거합니다. X-ray 및 Echo 원본 파일은 에이전트 컨텍스트에 포함하지 않습니다.
- 새 migration 적용을 위해 web을 먼저 재시작해 migration 완료를 확인한 뒤 worker를 재시작하세요. 브라우저에서는 환자 진료를 선택하고 `AI 진료 점검` 패널을 사용합니다.

## 17. 진료 집중 UX와 자동저장(2026-10-24)

- `sql/014_soap_section_drafts.sql`은 SOAP 확정본과 별도로 자동저장 초안을 보관합니다. 확정 SOAP를 다시 편집하면 확정 revision은 보존한 채 해당 단계와 후속 단계가 `stale`이 되고 상용 워크플로는 `documentation`으로 되돌아갑니다.
- 환자 기본정보, 진료 기본정보, 심장검사·ECG·Lab, 검사 계획, X-ray 판독문, SOAP 편집 내용은 지연 자동저장되며 하단 고정 바에 저장 대기·저장 중·저장 완료·오류 상태가 표시됩니다. 유효하지 않은 입력이나 저장 실패가 있으면 화면 이동과 단계 전환을 막습니다.
- 환자차트는 기본적으로 현재 업무 단계에 필요한 패널만 보여주는 집중 보기를 사용합니다. 하단의 `전체 보기`/`집중 보기`로 전환할 수 있습니다.
- 하단 고정 바의 단일 주요 버튼은 서버 workflow blocker를 기준으로 필요한 입력 화면으로 이동하거나 다음 단계로 전환합니다. 서버 단계 가드는 그대로 최종 권한을 가집니다.
- 정적 검사(`py_compile`, `node --check`, Compose config, `git diff --check`)는 통과했습니다. migration 적용 후 Docker pytest와 브라우저 확인이 필요합니다.

## 18. 자동 청구 가격표(2026-10-24)

- `sql/015_billing_catalog.sql`은 병원 공통 가격표를 추가합니다. 설정의 `자동 청구 가격표`에서 항목명·금액·사용 여부를 한 번 정합니다.
- 새 청구서는 기본 심장 진찰, 검토 완료된 검사, 발행된 구조화 처방을 기준으로 자동 채웁니다. 작성 중인 청구서는 자동으로 덮어쓰지 않으며 `완료 기록에서 다시 불러오기`를 눌렀을 때만 교체합니다.
- 자동 항목은 편의 기능일 뿐입니다. 발행 전 직원이 항목과 금액을 확인해야 하며, 서버가 총액을 다시 계산하는 원칙은 유지합니다.
- `sql/016_medication_billing_catalog.sql`은 일괄 약제비를 약품명별 가격표로 교체합니다. 발행 처방의 각 약품은 정확한 이름으로 가격표와 연결되며 미등록 약품은 가격 확인 전 청구서 발행을 막습니다.
- 결제 완료 후 수납 화면에 결제 금액·수단·영수증 번호를 크게 표시합니다. 종료 단계에는 다음 단계 안내 대신 모든 절차 완료 문구를 표시하며, 발행 후 미수 사유 입력은 한 곳만 보여줍니다.
