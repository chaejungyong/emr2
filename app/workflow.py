import hashlib
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file, session

from .auth import require_roles
from .clinical import mark_soap_stages_stale, parse_iso_datetime
from .clinical_records import record_clinical_revision
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .workflow_state import invalidate_diagnostic_review


bp = Blueprint("workflow", __name__, url_prefix="/api")


def _encounter(encounter_id):
    return fetch_one(
        "SELECT id, patient_id, workflow_stage FROM encounters WHERE id = %s",
        (encounter_id,),
    )


def _closed_response(encounter):
    if encounter.get("workflow_stage") != "closed":
        return None
    return jsonify(
        {
            "error": "encounter_closed",
            "message": "종료된 진료입니다. 수의사가 진료를 다시 연 뒤 수정하세요.",
        }
    ), 409


def _text(value, maximum=60000, required=False):
    value = str(value).strip() if value not in (None, "") else None
    if required and not value:
        raise ValueError("required")
    if value and len(value) > maximum:
        raise ValueError("text_too_long")
    return value


def _integer(value, minimum, maximum):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_integer") from error
    if str(parsed) != str(value).strip() and not isinstance(value, int):
        raise ValueError("invalid_integer")
    if parsed < minimum or parsed > maximum:
        raise ValueError("number_out_of_range")
    return parsed


def _decimal(value, minimum=Decimal("0"), maximum=Decimal("999999999")):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("invalid_number") from error
    if not parsed.is_finite() or parsed < minimum or parsed > maximum:
        raise ValueError("number_out_of_range")
    return parsed


def _payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("json_body_required")
    return value


def _present(row, decimal_fields=()):
    if not row:
        return None
    result = dict(row)
    for field in decimal_fields:
        if result.get(field) is not None:
            result[field] = float(result[field])
    return result


def _upsert_one(table, encounter_id, cleaned, audit_action, stale=True):
    existing = fetch_one(f"SELECT * FROM {table} WHERE encounter_id = %s", (encounter_id,))
    changed = {key: value for key, value in cleaned.items() if not existing or existing.get(key) != value}
    if changed:
        with transaction():
            if existing:
                result_id = existing["id"]
                assignments = ", ".join(f"{key} = %s" for key in changed)
                execute(f"UPDATE {table} SET {assignments} WHERE encounter_id = %s", [*changed.values(), encounter_id])
            else:
                result_id = new_id()
                columns = ["id", "encounter_id", *cleaned]
                placeholders = ", ".join(["%s"] * len(columns))
                execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [result_id, encounter_id, *cleaned.values()],
                )
            result_type = {"ecg_exams": "ecg", "lab_results": "lab"}.get(table)
            if result_type:
                snapshot = dict(existing or {"id": result_id, "encounter_id": encounter_id})
                snapshot.update(cleaned)
                record_clinical_revision(
                    encounter_id,
                    result_type,
                    result_id,
                    snapshot,
                    "updated" if existing else "created",
                    changed,
                )
            if stale:
                mark_soap_stages_stale(encounter_id, ("O", "A", "P"))
                execute(
                    "UPDATE encounters SET workflow_stage='diagnostics', workflow_updated_at=CURRENT_TIMESTAMP(6) WHERE id=%s AND workflow_stage IN ('documentation','education','checkout')",
                    (encounter_id,),
                )
                execute(
                    "UPDATE client_education_documents SET status='superseded' WHERE encounter_id=%s AND status IN ('draft','delivered')",
                    (encounter_id,),
                )
                execute(
                    "UPDATE prescriptions SET review_required=TRUE WHERE encounter_id=%s AND status IN ('draft','issued')",
                    (encounter_id,),
                )
                exam_type = {"ecg_exams": "ecg", "lab_results": "lab"}.get(table)
                if exam_type:
                    invalidate_diagnostic_review(encounter_id, exam_type)
            audit(audit_action, "encounter", encounter_id, {"fields": list(changed)})
    return fetch_one(f"SELECT * FROM {table} WHERE encounter_id = %s", (encounter_id,))


@bp.get("/encounters/<encounter_id>/ecg")
def get_ecg(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify({"exam": fetch_one("SELECT * FROM ecg_exams WHERE encounter_id = %s", (encounter_id,))})


@bp.put("/encounters/<encounter_id>/ecg")
def save_ecg(encounter_id):
    if session.get("admin_id") and session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _closed_response(encounter)
    if closed:
        return closed
    try:
        data = _payload()
        cleaned = {
            "recorded_at": parse_iso_datetime(data.get("recorded_at")),
            "heart_rate_bpm": _integer(data.get("heart_rate_bpm"), 1, 500),
            "rhythm": _text(data.get("rhythm"), 120),
            "pr_ms": _integer(data.get("pr_ms"), 1, 1000),
            "qrs_ms": _integer(data.get("qrs_ms"), 1, 1000),
            "qt_ms": _integer(data.get("qt_ms"), 1, 2000),
            "interpretation": _text(data.get("interpretation")),
        }
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"ECG 입력값을 확인하세요. ({error})"}), 400
    return jsonify({"exam": _upsert_one("ecg_exams", encounter_id, cleaned, "ecg.update")})


LAB_DECIMALS = ("nt_probnp_pmol_l", "troponin_i_ng_ml", "bun_mg_dl", "creatinine_mg_dl", "sodium_mmol_l", "potassium_mmol_l")


@bp.get("/encounters/<encounter_id>/lab")
def get_lab(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    row = fetch_one("SELECT * FROM lab_results WHERE encounter_id = %s", (encounter_id,))
    return jsonify({"result": _present(row, LAB_DECIMALS)})


@bp.put("/encounters/<encounter_id>/lab")
def save_lab(encounter_id):
    if session.get("admin_id") and session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _closed_response(encounter)
    if closed:
        return closed
    try:
        data = _payload()
        cleaned = {"collected_at": parse_iso_datetime(data.get("collected_at"))}
        cleaned.update({field: _decimal(data.get(field), Decimal("0"), Decimal("100000")) for field in LAB_DECIMALS})
        cleaned["notes"] = _text(data.get("notes"))
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"Lab 입력값을 확인하세요. ({error})"}), 400
    row = _upsert_one("lab_results", encounter_id, cleaned, "lab.update")
    return jsonify({"result": _present(row, LAB_DECIMALS)})


def _appointment_payload(data):
    patient_id = _text(data.get("patient_id"), 36, required=True)
    if not fetch_one("SELECT id FROM patients WHERE id = %s", (patient_id,)):
        raise LookupError("patient_not_found")
    starts_at = parse_iso_datetime(data.get("starts_at"))
    if starts_at is None:
        raise ValueError("starts_at_required")
    status = data.get("status") or "scheduled"
    if status not in {"scheduled", "arrived", "completed", "cancelled", "no_show"}:
        raise ValueError("invalid_status")
    return {
        "patient_id": patient_id,
        "starts_at": starts_at,
        "duration_minutes": _integer(data.get("duration_minutes") or 30, 5, 480),
        "purpose": _text(data.get("purpose"), 240, required=True),
        "status": status,
        "notes": _text(data.get("notes"), 10000),
    }


def _appointment_query(start, end):
    return fetch_all(
        """
        SELECT a.*, p.name AS patient_name, p.chart_number
        FROM appointments a JOIN patients p ON p.id = a.patient_id
        WHERE a.starts_at >= %s AND a.starts_at < %s
        ORDER BY a.starts_at
        """,
        (start, end),
    )


@bp.get("/appointments")
def list_appointments():
    raw = request.args.get("date")
    try:
        selected = date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return jsonify({"error": "invalid_date"}), 400
    start = datetime.combine(selected, time.min)
    return jsonify({"date": selected.isoformat(), "items": _appointment_query(start, start + timedelta(days=1))})


@bp.post("/appointments")
def create_appointment():
    try:
        cleaned = _appointment_payload(_payload())
    except LookupError:
        return jsonify({"error": "patient_not_found"}), 404
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"일정 입력값을 확인하세요. ({error})"}), 400
    appointment_id = new_id()
    with transaction():
        execute(
            "INSERT INTO appointments (id, patient_id, starts_at, duration_minutes, purpose, status, notes) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [appointment_id, *cleaned.values()],
        )
        audit("appointment.create", "appointment", appointment_id)
    return jsonify(fetch_one("SELECT * FROM appointments WHERE id = %s", (appointment_id,))), 201


@bp.patch("/appointments/<appointment_id>")
def update_appointment(appointment_id):
    existing = fetch_one("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
    if not existing:
        return jsonify({"error": "appointment_not_found"}), 404
    try:
        merged = {**existing, **_payload()}
        cleaned = _appointment_payload(merged)
    except LookupError:
        return jsonify({"error": "patient_not_found"}), 404
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"일정 입력값을 확인하세요. ({error})"}), 400
    with transaction():
        execute(
            "UPDATE appointments SET patient_id=%s, starts_at=%s, duration_minutes=%s, purpose=%s, status=%s, notes=%s WHERE id=%s",
            [*cleaned.values(), appointment_id],
        )
        audit("appointment.update", "appointment", appointment_id, {"fields": list(cleaned)})
    return jsonify(fetch_one("SELECT * FROM appointments WHERE id = %s", (appointment_id,)))


@bp.get("/tasks")
def list_tasks():
    raw = request.args.get("date")
    try:
        selected = date.fromisoformat(raw) if raw else date.today()
    except ValueError:
        return jsonify({"error": "invalid_date"}), 400
    start = datetime.combine(selected, time.min)
    rows = fetch_all(
        """
        SELECT t.*, p.name AS patient_name, p.chart_number
        FROM clinic_tasks t
        LEFT JOIN patients p ON p.id = t.patient_id
        WHERE t.due_at >= %s AND t.due_at < %s
        ORDER BY t.status, t.due_at
        """,
        (start, start + timedelta(days=1)),
    )
    return jsonify({"date": selected.isoformat(), "items": rows})


@bp.post("/tasks")
def create_task():
    try:
        data = _payload()
        patient_id = data.get("patient_id") or None
        if patient_id and not fetch_one("SELECT id FROM patients WHERE id = %s", (patient_id,)):
            return jsonify({"error": "patient_not_found"}), 404
        task_type = data.get("task_type") or "general"
        if task_type not in {"result_review", "callback", "general"}:
            raise ValueError("invalid_task_type")
        due_at = parse_iso_datetime(data.get("due_at"))
        if due_at is None:
            raise ValueError("due_at_required")
        title = _text(data.get("title"), 240, required=True)
        notes = _text(data.get("notes"), 10000)
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"할 일 입력값을 확인하세요. ({error})"}), 400
    task_id = new_id()
    with transaction():
        execute(
            "INSERT INTO clinic_tasks (id, patient_id, due_at, task_type, title, notes) VALUES (%s,%s,%s,%s,%s,%s)",
            (task_id, patient_id, due_at, task_type, title, notes),
        )
        audit("task.create", "clinic_task", task_id)
    return jsonify(fetch_one("SELECT * FROM clinic_tasks WHERE id = %s", (task_id,))), 201


@bp.patch("/tasks/<task_id>")
def update_task(task_id):
    existing = fetch_one("SELECT * FROM clinic_tasks WHERE id = %s", (task_id,))
    if not existing:
        return jsonify({"error": "task_not_found"}), 404
    try:
        data = _payload()
        status = data.get("status")
        if status not in {"open", "done"}:
            raise ValueError("invalid_status")
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"할 일 입력값을 확인하세요. ({error})"}), 400
    with transaction():
        execute(
            "UPDATE clinic_tasks SET status=%s, completed_at=%s WHERE id=%s",
            (status, datetime.utcnow() if status == "done" else None, task_id),
        )
        audit("task.update", "clinic_task", task_id, {"status": status})
    return jsonify(fetch_one("SELECT * FROM clinic_tasks WHERE id = %s", (task_id,)))


@bp.get("/encounters/<encounter_id>/prescription")
def get_prescription(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify({"prescription": fetch_one("SELECT * FROM prescriptions WHERE encounter_id = %s", (encounter_id,))})


@bp.put("/encounters/<encounter_id>/prescription")
@require_roles("veterinarian")
def save_prescription(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _closed_response(encounter)
    if closed:
        return closed
    if encounter.get("workflow_stage") not in {"education", "checkout"}:
        return jsonify(
            {
                "error": "workflow_stage_required",
                "message": "자유형 처방 초안은 보호자 설명 또는 수납 단계에서만 저장할 수 있습니다.",
            }
        ), 409
    structured = fetch_one(
        """
        SELECT r.id
        FROM prescriptions p
        JOIN prescription_revisions r ON r.prescription_id=p.id
        WHERE p.encounter_id=%s
        LIMIT 1
        """,
        (encounter_id,),
    )
    if structured:
        return jsonify(
            {
                "error": "structured_prescription_managed",
                "message": "구조화 처방은 구조화 처방 API에서만 수정할 수 있습니다.",
            }
        ), 409
    try:
        data = _payload()
        status = data.get("status") or "draft"
        if status not in {"draft", "issued"}:
            raise ValueError("invalid_status")
        if status == "issued":
            return jsonify(
                {
                    "error": "structured_prescription_required",
                    "message": "처방 발행은 구조화 처방 API를 사용하세요.",
                }
            ), 409
        cleaned = {
            "issued_at": parse_iso_datetime(data.get("issued_at")),
            "medication_text": _text(data.get("medication_text")),
            "instructions": _text(data.get("instructions")),
            "status": status,
        }
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"처방 입력값을 확인하세요. ({error})"}), 400
    return jsonify({"prescription": _upsert_one("prescriptions", encounter_id, cleaned, "prescription.update", stale=False)})


BILLING_AMOUNTS = ("consultation_amount", "diagnostic_amount", "medication_amount", "other_amount", "discount_amount")


@bp.get("/encounters/<encounter_id>/billing")
def get_billing(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    row = fetch_one("SELECT * FROM billing_records WHERE encounter_id = %s", (encounter_id,))
    return jsonify({"billing": _present(row, (*BILLING_AMOUNTS, "total_amount"))})


@bp.put("/encounters/<encounter_id>/billing")
def save_billing(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    try:
        data = _payload()
        cleaned = {field: _decimal(data.get(field) or 0, Decimal("0"), Decimal("999999999")) for field in BILLING_AMOUNTS}
        cleaned["total_amount"] = max(
            Decimal("0"),
            cleaned["consultation_amount"] + cleaned["diagnostic_amount"] + cleaned["medication_amount"] + cleaned["other_amount"] - cleaned["discount_amount"],
        )
        status = data.get("payment_status") or "unpaid"
        method = data.get("payment_method") or None
        if status not in {"unpaid", "paid", "refunded"} or method not in {None, "cash", "card", "transfer", "other"}:
            raise ValueError("invalid_choice")
        if status == "paid" and method is None:
            raise ValueError("payment_method_required")
        cleaned.update({
            "payment_status": status,
            "payment_method": method,
            "paid_at": parse_iso_datetime(data.get("paid_at")) if status == "paid" else None,
            "notes": _text(data.get("notes"), 10000),
        })
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": f"수납 입력값을 확인하세요. ({error})"}), 400
    row = _upsert_one("billing_records", encounter_id, cleaned, "billing.update", stale=False)
    return jsonify({"billing": _present(row, (*BILLING_AMOUNTS, "total_amount"))})


VIDEO_SIGNATURES = {
    "video/mp4": (".mp4", lambda data: len(data) >= 12 and data[4:8] == b"ftyp"),
    "video/webm": (".webm", lambda data: data.startswith(b"\x1a\x45\xdf\xa3")),
}


def _video_type(data):
    for mime_type, (extension, detector) in VIDEO_SIGNATURES.items():
        if detector(data):
            return mime_type, extension
    raise ValueError("unsupported_video")


def _present_video(row):
    result = dict(row)
    result.pop("storage_key", None)
    result["file_url"] = f"/api/echo-videos/{row['id']}/file"
    return result


@bp.get("/encounters/<encounter_id>/echo-videos")
def list_echo_videos(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    rows = fetch_all("SELECT * FROM echo_video_assets WHERE encounter_id = %s ORDER BY created_at DESC", (encounter_id,))
    return jsonify({"items": [_present_video(row) for row in rows]})


@bp.post("/encounters/<encounter_id>/echo-videos")
def upload_echo_video(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _closed_response(encounter)
    if closed:
        return closed
    count = fetch_one("SELECT COUNT(*) AS count FROM echo_video_assets WHERE encounter_id = %s", (encounter_id,))["count"]
    if count >= 20:
        return jsonify({"error": "echo_video_limit_reached", "message": "진료당 최대 20개까지 등록할 수 있습니다."}), 409
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "file_required"}), 400
    data = uploaded.stream.read(current_app.config["MAX_ECHO_VIDEO_BYTES"] + 1)
    if len(data) > current_app.config["MAX_ECHO_VIDEO_BYTES"]:
        return jsonify({"error": "echo_video_too_large"}), 413
    try:
        mime_type, extension = _video_type(data)
        recorded_at = parse_iso_datetime(request.form.get("recorded_at"))
        note = _text(request.form.get("note"), 500)
    except ValueError:
        return jsonify({"error": "invalid_echo_video", "message": "MP4 또는 WebM 영상만 지원합니다."}), 400
    asset_id = new_id()
    relative = Path(encounter_id) / f"{asset_id}{extension}"
    root = current_app.config["ECHO_VIDEO_ROOT"]
    destination = (root / relative).resolve()
    if root not in destination.parents:
        return jsonify({"error": "invalid_storage_path"}), 400
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        with transaction():
            execute(
                "INSERT INTO echo_video_assets (id, encounter_id, storage_key, original_name, mime_type, size_bytes, sha256, recorded_at, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (asset_id, encounter_id, relative.as_posix(), Path(uploaded.filename).name[:255], mime_type, len(data), hashlib.sha256(data).hexdigest(), recorded_at, note),
            )
            audit("echo_video.upload", "echo_video", asset_id, {"size_bytes": len(data)})
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return jsonify(_present_video(fetch_one("SELECT * FROM echo_video_assets WHERE id = %s", (asset_id,)))), 201


@bp.get("/echo-videos/<asset_id>/file")
def get_echo_video_file(asset_id):
    row = fetch_one("SELECT * FROM echo_video_assets WHERE id = %s", (asset_id,))
    if not row:
        return jsonify({"error": "echo_video_not_found"}), 404
    root = current_app.config["ECHO_VIDEO_ROOT"]
    path = (root / row["storage_key"]).resolve()
    if root not in path.parents or not path.is_file():
        return jsonify({"error": "echo_video_file_missing"}), 404
    with transaction():
        audit("echo_video.view", "echo_video", asset_id)
    return send_file(path, mimetype=row["mime_type"], download_name=row["original_name"], conditional=True)
