from flask import Blueprint, current_app, jsonify, request, session

from .ai import get_vector_store
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .indexing import scan_pdfs, source_key


bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/status")
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
        }
    )


@bp.get("/index-jobs")
def list_index_jobs():
    jobs = fetch_all(
        "SELECT * FROM index_jobs ORDER BY created_at DESC LIMIT 50"
    )
    return jsonify({"items": jobs})


@bp.get("/index-jobs/<job_id>")
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
