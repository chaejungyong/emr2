# AGENTS.md

이 문서는 현재 실행 환경에 대한 사실만 정리한 것입니다. 작업 전에 끝까지 읽으세요.
구조도는 `docs/architecture.svg`에 있습니다.

## 한 줄 요약

Synology NAS 위에서 Docker Compose로 돌아가는 Flask + MariaDB 10.6 웹서버입니다. 지금은 빈 페이지와 헬스체크 API만 있습니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `app.py` | Flask 앱 전체. DB 연결 함수 `get_db()`, 라우트 `/`, `/api/health` |
| `static/index.html` | `/`에서 반환하는 빈 페이지. 정적 파일은 `static/`에 두면 `/static/...`로 서빙됨 |
| `requirements.txt` | Python 패키지 (flask, flask-cors, pymysql) |
| `Dockerfile` | `python:3.10-slim` 이미지, `python app.py`로 실행 |
| `docker-compose.yml` | `db`, `web` 두 서비스 정의 |
| `.env` | 실제 설정값과 DB 비밀번호. git에 올리지 않음 |
| `.env.example` | `.env`의 템플릿. `.env`에 키를 추가하면 여기에도 추가할 것 |
| `db_data/` | MariaDB 데이터 파일. git 제외, 소유자 uid 999 |

## 실행 환경

- 호스트: Synology NAS (x86_64), 프로젝트 경로 `/volume1/docker/emr2`
- 에이전트 셸 사용자: `crosssoldier` (uid 1026)
- 호스트의 Python은 3.8이고 flask, pymysql이 설치되어 있지 않습니다. **호스트에서 `python app.py`를 실행하지 마세요.** 앱은 컨테이너 안(Python 3.10)에서만 돌아갑니다.
- 호스트에서 `python3 -m py_compile app.py`로 문법 검사는 할 수 있습니다.

## Docker

- **에이전트 셸은 docker 소켓 권한이 없습니다.** `docker ps`, `docker compose up` 등은 실패합니다. 컨테이너 조작이 필요하면 사용자에게 아래 명령을 `sudo`로 실행해 달라고 요청하세요.
  - 예외: `docker compose config`(설정 해석 확인)는 권한 없이 동작합니다.
- 사용자에게 요청할 명령 (프로젝트 폴더에서):
  - 기동/재빌드: `sudo docker compose up -d --build`
  - 중지: `sudo docker compose down`
  - 로그: `sudo docker compose logs -f web`
  - DB 콘솔: `sudo docker exec -it app-db mariadb -uroot -p app_db`

| 항목 | 값 |
| --- | --- |
| Compose 프로젝트 이름 | `emr2` (폴더 이름에서 자동 결정) |
| 네트워크 | `emr2_default` |
| 웹 컨테이너 | `app-web` (서비스 이름 `web`) |
| DB 컨테이너 | `app-db` (서비스 이름 `db`) |
| 웹 포트 | 호스트 `8081` → 컨테이너 `5000` |
| DB 포트 | `3306`, 컨테이너 내부 전용 (호스트에 노출 안 됨) |
| DB 이름 | `app_db`, 계정 `root` |

## 포트와 설정이 정해지는 곳

- 호스트 포트 8081: `.env`의 `WEB_PORT` → `docker-compose.yml`의 `"${WEB_PORT}:5000"`
- 컨테이너 내부 포트 5000: `app.py`의 `app.run(port=5000)`
- 컨테이너 이름 접두어 `app`: `.env`의 `PROJECT_NAME`
- `web` 컨테이너는 `env_file: .env`로 모든 값을 환경변수로 받고, `app.py`는 `os.environ`에서 읽습니다.
- `DB_NAME`, `DB_PASSWORD`는 `db_data/`가 비어 있을 때(최초 기동)만 적용됩니다. 나중에 `.env`만 바꿔서는 DB에 반영되지 않습니다.

## 개발 흐름

- 소스 폴더가 컨테이너의 `/app`에 마운트되어 있고 `FLASK_DEBUG=1`이라, `app.py`나 `static/` 파일을 고치면 재시작 없이 반영됩니다.
- `requirements.txt`나 `Dockerfile`을 바꾸면 사용자에게 `sudo docker compose up -d --build`를 요청해야 합니다.
- `.env`를 바꾸면 사용자에게 `sudo docker compose up -d`를 요청해야 합니다.
- 동작 확인은 에이전트 셸에서 직접 할 수 있습니다:
  - `curl -s http://127.0.0.1:8081/api/health` → `{"db": true, "status": "ok"}`
  - `curl -s http://127.0.0.1:8081/`
- 웹 서버는 Flask 내장 개발 서버(Werkzeug)입니다. nginx는 이 경로에 없습니다.
- 테스트, 린터, DB 마이그레이션 도구는 아직 없습니다. 테이블이 필요하면 `app.py`에서 `get_db()`로 직접 SQL을 실행하는 방식입니다.

## Git

- 브랜치 `main`, 원격 `origin` = `https://github.com/chaejungyong/emr2.git`
- `.env`, `db_data/`, `__pycache__/`는 `.gitignore`로 제외되어 있습니다. 절대 커밋하지 마세요.
- 비밀번호를 코드나 `.env.example`에 쓰지 마세요.

## 주의사항

- `db_data/`를 지우면 DB 데이터가 모두 사라집니다. 사용자가 명시적으로 요청할 때만 지우세요. 지울 때는 먼저 컨테이너를 내려야 합니다.
- 파일 편집 도구로 `.svg` 파일에 한글 등 비ASCII 문자를 쓰면 글자가 깨집니다. `.md` 확장자로 임시 파일을 쓴 뒤 `mv`로 옮기세요. 다른 확장자도 비ASCII 문자를 쓴 뒤에는 `python3 -c "open('파일',encoding='utf-8').read()"`로 확인하세요.
- NAS의 시스템 설정(`/etc/nginx` 등)을 조회하는 명령은 차단될 수 있습니다. 프로젝트 폴더 밖은 건드리지 마세요.
