# App

Flask + MariaDB 10.6 기반 빈 웹서버.

## 구성

- `web`: Flask (컨테이너 5000 포트 → 호스트 `WEB_PORT`), 소스는 `.:/app`로 마운트
- `db`: MariaDB 10.6, 데이터는 `./db_data`

## 실행

```bash
cp .env.example .env   # 최초 1회, 값 수정
sudo docker compose up -d --build
```

- 페이지: `http://<host>:<WEB_PORT>/`
- 헬스체크: `http://<host>:<WEB_PORT>/api/health` → `{"status": "ok", "db": true}`

`FLASK_DEBUG=1`이면 코드 수정 시 Flask가 자동 재시작됩니다. `requirements.txt`를 바꾼 경우에는 `--build`로 다시 빌드하세요.

## .env

| 키 | 설명 |
| --- | --- |
| `PROJECT_NAME` | 컨테이너 이름 접두어 (`<PROJECT_NAME>-web`, `<PROJECT_NAME>-db`) |
| `WEB_PORT` | 호스트에 노출할 포트 |
| `DB_HOST`, `DB_PORT` | Flask가 접속할 DB 주소 (compose 내부에서는 `db`) |
| `DB_NAME` | 최초 기동 시 생성되는 데이터베이스 |
| `DB_USER`, `DB_PASSWORD` | DB 접속 계정 (`DB_PASSWORD`는 root 비밀번호로도 사용) |
| `FLASK_DEBUG` | `1`이면 디버그 모드 + 자동 재시작 |

`DB_NAME`, `DB_PASSWORD`는 `db_data`가 비어 있을 때(최초 기동)만 적용됩니다. 바꾸려면 컨테이너를 내린 뒤 `db_data`를 비우고 다시 띄우세요.
