# CardioSOAP Vet

수의사가 심장질환 진료 기록을 S → O → A → P 순서로 작성하도록 돕는 AI 웹 서비스입니다. AI가 제시한 후보는 수의사가 선택·수정·확정해야 하며, 자동 진단 또는 자동 처방 시스템이 아닙니다.

## 구성

- `web`: Flask/Gunicorn API와 바닐라 JavaScript UI
- `worker`: 대용량 PDF 추출, 임베딩, Qdrant 갱신
- `db`: MariaDB 10.6. 환자·진료·SOAP·AI 생성 및 수정 이력의 원본
- `qdrant`: 의학 문서의 재생성 가능한 벡터 검색 인덱스
- `storage/xrays`: 인증 API로만 조회하는 X-ray 원본
- `storage/knowledge/inbox`: 관리자가 직접 넣는 의학 PDF

X-ray 원본은 AI 제공자에게 전송하지 않습니다. 수의사가 저장한 판독 소견 텍스트만 SOAP 컨텍스트에 포함됩니다.

## 최초 설정

기존 `.env`가 있다면 삭제하지 말고 [`.env.example`](.env.example)의 새 항목을 추가합니다. 최소한 다음 값은 직접 설정해야 합니다.

```dotenv
SECRET_KEY=<충분히 긴 임의 문자열>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<최초 관리자 비밀번호>

LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com
LLM_API_KEY=<API 키>
LLM_MODEL=gpt-5.6-terra

EMBEDDING_BASE_URL=https://api.openai.com
EMBEDDING_API_KEY=<API 키>
EMBEDDING_MODEL=text-embedding-3-large
```

`ADMIN_PASSWORD`는 관리자 레코드가 아직 없을 때만 사용합니다. 이후 비밀번호를 `.env`에서 바꿔도 기존 관리자 비밀번호는 바뀌지 않습니다.

로컬 UI 흐름만 확인할 때는 `LLM_PROVIDER=mock`을 사용할 수 있습니다. 이 모드는 실제 의학 문서 임베딩 품질을 검증하지 않습니다.

## 실행

의존성과 Compose 서비스가 변경되었으므로 이미 실행 중인 환경도 반드시 재빌드해야 합니다.

```bash
sudo docker compose up -d --build
sudo docker compose logs -f web worker
```

- 서비스: `http://<NAS 주소>:<WEB_PORT>/`
- 헬스체크: `http://127.0.0.1:<WEB_PORT>/api/health`

DB 스키마는 web 기동 시 `sql/`의 번호형 migration으로 추가됩니다. 기존 `db_data`를 삭제하지 않습니다.

## 사용 흐름

1. 로그인하고 질병 분류를 등록합니다.
2. 환자와 새 진료를 생성하고 History, 주호소, 신체검사 정보를 입력합니다.
3. 필요한 경우 JPEG/PNG X-ray와 수의사 판독 소견을 저장합니다.
4. S 후보를 생성하고 하나를 선택하거나 직접 수정한 뒤 확정합니다.
5. 같은 방식으로 O, A, P를 순서대로 확정합니다.
6. 앞 단계를 다시 수정하면 뒤 단계는 `stale`이 되어 재검토가 필요합니다.
7. 이전 진료에서 확정된 SOAP는 같은 환자의 다음 진료 컨텍스트로 사용됩니다.

## 의학 PDF 색인

PDF를 HTTP로 업로드하지 않습니다. NAS에서 아래 폴더에 직접 복사합니다.

```text
storage/knowledge/inbox/
```

그 다음 우측 관리 패널에서 실행합니다.

- 증분 갱신: SHA-256이 바뀐 PDF만 다시 임베딩하고 삭제된 파일을 검색에서 제외
- 전체 재색인: 새 Qdrant 컬렉션을 완성한 뒤 alias를 전환. 실패하면 기존 인덱스 유지

스캔본처럼 텍스트를 추출할 수 없는 PDF는 오류로 표시됩니다. 현재 MVP에는 OCR이 포함되지 않습니다.

## 검증

```bash
sudo docker compose run --rm web pytest
docker compose config
curl -s http://127.0.0.1:${WEB_PORT:-8081}/api/health
```

## 운영 주의

- 외부 접근이 있다면 Synology reverse proxy에서 HTTPS를 적용하고 `FLASK_DEBUG=0`, `SESSION_COOKIE_SECURE=1`을 사용하세요.
- `.env`, `db_data`, `storage/xrays`, `storage/qdrant`, 의학 PDF는 Git에 포함되지 않습니다.
- MariaDB, X-ray, 의학 원문을 함께 백업해야 합니다. Qdrant는 원문 PDF에서 재생성할 수 있습니다.
- 외부 AI 제공자에는 환자 임상 텍스트가 전송될 수 있으므로 기관의 개인정보·의료정보 정책을 확인하세요.
