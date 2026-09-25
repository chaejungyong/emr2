import os
from pathlib import Path


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DEBUG = env_bool("FLASK_DEBUG")
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
    PERMANENT_SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME_SECONDS", "28800"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_REQUEST_BYTES", str(30 * 1024 * 1024)))

    DB_CONFIG = {
        "host": os.environ.get("DB_HOST", "db"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "database": os.environ.get("DB_NAME", "app_db"),
    }

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
    LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "15"))

    STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", BASE_DIR / "storage")).resolve()
    XRAY_ROOT = STORAGE_ROOT / "xrays"
    KNOWLEDGE_ROOT = STORAGE_ROOT / "knowledge" / "inbox"
    MAX_XRAY_BYTES = int(os.environ.get("MAX_XRAY_BYTES", str(25 * 1024 * 1024)))
    MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(512 * 1024 * 1024)))
    MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "10000"))

    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai_compatible")
    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "90"))
    SOAP_CANDIDATE_COUNT = int(os.environ.get("SOAP_CANDIDATE_COUNT", "3"))

    EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", LLM_BASE_URL)
    EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", LLM_API_KEY)
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))

    QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
    QDRANT_ALIAS = os.environ.get("QDRANT_ALIAS", "kb_active")
    QDRANT_TIMEOUT_SECONDS = float(os.environ.get("QDRANT_TIMEOUT_SECONDS", "120"))
    QDRANT_UPSERT_BATCH_SIZE = int(os.environ.get("QDRANT_UPSERT_BATCH_SIZE", "32"))
    RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "6"))
    CHUNKER_VERSION = "paragraph-v1"

    WORKER_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "3"))

    @classmethod
    def validate(cls):
        if not cls.SECRET_KEY:
            raise RuntimeError("SECRET_KEY must be configured.")
        if cls.SOAP_CANDIDATE_COUNT < 1 or cls.SOAP_CANDIDATE_COUNT > 10:
            raise RuntimeError("SOAP_CANDIDATE_COUNT must be between 1 and 10.")
