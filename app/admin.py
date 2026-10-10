from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import generate_password_hash

from .ai import get_vector_store
from .auth import require_roles
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .indexing import scan_pdfs, source_key


bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def veterinarian_only():
    if session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    return None


@bp.get("/users")
def list_users():
    denied = veterinarian_only()
    if denied:
        return denied
    return jsonify(
        {
            "items": fetch_all(
                "SELECT id,username,role,is_active,last_login_at,created_at FROM admins ORDER BY username"
            )
        }
    )


@bp.post("/users")
def create_user():
    denied = veterinarian_only()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = payload.get("role")
    if not username or len(username) > 100 or len(password) < 12 or role not in {"veterinarian", "staff"}:
        return jsonify({"error": "validation_error", "message": "사용자명, 12자 이상 비밀번호, 역할을 확인하세요."}), 400
    user_id = new_id()
    with transaction():
        execute(
            "INSERT INTO admins (id,username,role,password_hash) VALUES (%s,%s,%s,%s)",
            (user_id, username, role, generate_password_hash(password, method="scrypt")),
        )
        audit("admin.user_create", "admin", user_id, {"role": role})
    return jsonify(fetch_one("SELECT id,username,role,is_active,created_at FROM admins WHERE id=%s", (user_id,))), 201


@bp.patch("/users/<user_id>")
def update_user(user_id):
    denied = veterinarian_only()
    if denied:
        return denied
    user = fetch_one("SELECT id,role,is_active FROM admins WHERE id=%s", (user_id,))
    if not user:
        return jsonify({"error": "user_not_found"}), 404
    payload = request.get_json(silent=True) or {}
    removing_veterinarian = user["role"] == "veterinarian" and user["is_active"] and (
        payload.get("role", user["role"]) != "veterinarian"
        or payload.get("is_active", user["is_active"]) is False
    )
    if removing_veterinarian:
        count = fetch_one(
            "SELECT COUNT(*) AS count FROM admins WHERE role='veterinarian' AND is_active=TRUE"
        )["count"]
        if count <= 1:
            return jsonify({"error": "last_veterinarian_required", "message": "활성 수의사 계정은 한 개 이상 필요합니다."}), 409
    updates, params = [], []
    if "role" in payload:
        if payload["role"] not in {"veterinarian", "staff"}:
            return jsonify({"error": "validation_error"}), 400
        updates.append("role=%s")
        params.append(payload["role"])
    if "is_active" in payload:
        if type(payload["is_active"]) is not bool or user_id == session.get("admin_id") and not payload["is_active"]:
            return jsonify({"error": "validation_error"}), 400
        updates.append("is_active=%s")
        params.append(payload["is_active"])
    if "password" in payload:
        password = str(payload["password"] or "")
        if len(password) < 12:
            return jsonify({"error": "validation_error", "message": "비밀번호는 12자 이상이어야 합니다."}), 400
        updates.append("password_hash=%s")
        params.append(generate_password_hash(password, method="scrypt"))
    if not updates:
        return jsonify({"error": "no_supported_fields"}), 400
    with transaction():
        execute(f"UPDATE admins SET {', '.join(updates)} WHERE id=%s", [*params, user_id])
        audit("admin.user_update", "admin", user_id, {"fields": list(payload)})
    return jsonify(fetch_one("SELECT id,username,role,is_active,last_login_at FROM admins WHERE id=%s", (user_id,)))


@bp.get("/audit-events")
@require_roles("veterinarian")
def list_audit_events():
    try:
        limit = min(max(int(request.args.get("limit", "100")), 1), 500)
    except ValueError:
        return jsonify({"error": "invalid_limit"}), 400
    rows = fetch_all(
        """
        SELECT ae.id, ae.action, ae.entity_type, ae.entity_id,
               ae.metadata_json, ae.ip_address, ae.created_at,
               a.username, a.role
        FROM audit_events ae
        LEFT JOIN admins a ON a.id=ae.admin_id
        ORDER BY ae.created_at DESC LIMIT %s
        """,
        (limit,),
    )
    return jsonify({"items": rows})


@bp.get("/status")
@require_roles("veterinarian")
def status():
    counts = {}
    for table in ("patients", "encounters", "diseases", "kb_documents", "kb_chunks"):
        counts[table] = fetch_one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
    try:
        qdrant_ok = get_vector_store().health()
        active_collection = get_vector_store().active_collection() if qdrant_ok else None
    except Exception:
        qdrant_ok, active_collection = False, None
    files = [
        {"source_key": source_key(path), "size_bytes": path.stat().st_size}
        for path in scan_pdfs()
    ]
    security_warnings = []
    if not current_app.config["SESSION_COOKIE_SECURE"]:
        security_warnings.append("HTTPS 적용 후 SESSION_COOKIE_SECURE=1로 변경하세요.")
    if current_app.config["LOGIN_LOCK_MINUTES"] < 15:
        security_warnings.append("LOGIN_LOCK_MINUTES를 15 이상으로 설정하세요.")
    if current_app.config["DB_CONFIG"]["user"] == "root":
        security_warnings.append("MariaDB 전용 최소권한 계정을 사용하세요.")
    if current_app.config["DEPLOYMENT_MODE"] == "pilot" and security_warnings:
        security_warnings.insert(0, "파일럿 보안 조건이 충족되지 않았습니다.")
    return jsonify(
        {
            "counts": counts,
            "qdrant": {"ok": qdrant_ok, "active_collection": active_collection},
            "knowledge_files": files,
            "knowledge_path": "storage/knowledge/inbox",
            "ai": {
                "provider": current_app.config["LLM_PROVIDER"],
                "model": current_app.config["LLM_MODEL"],
                "embedding_model": current_app.config["EMBEDDING_MODEL"],
            },
            "deployment_mode": current_app.config["DEPLOYMENT_MODE"],
            "security_warnings": security_warnings,
        }
    )


@bp.get("/index-jobs")
@require_roles("veterinarian")
def list_index_jobs():
    jobs = fetch_all(
        """
        SELECT j.*,
               current_item.source_key AS current_source_key,
               current_item.progress_phase,
               current_item.progress_current,
               current_item.progress_total,
               (
                   SELECT i.source_key
                   FROM index_job_items i
                   WHERE i.job_id = j.id AND i.status = 'failed'
                   ORDER BY i.source_key LIMIT 1
               ) AS failed_source_key,
               (
                   SELECT i.error_message
                   FROM index_job_items i
                   WHERE i.job_id = j.id AND i.status = 'failed'
                   ORDER BY i.source_key LIMIT 1
               ) AS item_error_message
        FROM index_jobs j
        LEFT JOIN index_job_items current_item
          ON current_item.job_id = j.id AND current_item.status = 'processing'
        ORDER BY j.created_at DESC LIMIT 50
        """
    )
    return jsonify({"items": jobs})


@bp.get("/index-jobs/<job_id>")
@require_roles("veterinarian")
def get_index_job(job_id):
    job = fetch_one("SELECT * FROM index_jobs WHERE id = %s", (job_id,))
    if not job:
        return jsonify({"error": "index_job_not_found"}), 404
    job["items"] = fetch_all(
        """
        SELECT * FROM index_job_items
        WHERE job_id = %s
        ORDER BY source_key
        """,
        (job_id,),
    )
    return jsonify(job)


@bp.post("/index-jobs")
@require_roles("veterinarian")
def create_index_job():
    payload = request.get_json(silent=True) or {}
    job_type = payload.get("type", "incremental")
    if job_type not in {"incremental", "full"}:
        return jsonify({"error": "invalid_index_job_type"}), 400
    running = fetch_one(
        """
        SELECT id FROM index_jobs
        WHERE status IN ('queued', 'running')
        ORDER BY created_at LIMIT 1
        """
    )
    if running:
        return jsonify({"error": "index_job_already_running", "job_id": running["id"]}), 409
    job_id = new_id()
    with transaction():
        execute(
            """
            INSERT INTO index_jobs
                (id, job_type, requested_by, embedding_model, chunker_version)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                job_id,
                job_type,
                session["admin_id"],
                current_app.config["EMBEDDING_MODEL"],
                current_app.config["CHUNKER_VERSION"],
            ),
        )
        audit("knowledge.index_requested", "index_job", job_id, {"type": job_type})
    return jsonify(fetch_one("SELECT * FROM index_jobs WHERE id = %s", (job_id,))), 202
