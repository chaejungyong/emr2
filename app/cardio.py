from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session

from .auth import require_roles
from .clinical import mark_soap_stages_stale, parse_iso_datetime
from .clinical_records import present_revision, record_clinical_revision
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .workflow_state import invalidate_diagnostic_review


bp = Blueprint("cardio", __name__, url_prefix="/api")

BOOLEAN_FIELDS = {
    "cough",
    "syncope",
    "exercise_intolerance",
    "nocturnal_tachypnea",
    "sam",
    "lvoto",
    "sec_present",
    "la_thrombus",
    "pericardial_effusion",
}
DECIMAL_FIELDS = {
    "body_weight_kg": (Decimal("0.01"), Decimal("500")),
    "vhs": (Decimal("0"), Decimal("30")),
    "vlas": (Decimal("0"), Decimal("10")),
    "la_ao": (Decimal("0"), Decimal("10")),
    "lvidd_cm": (Decimal("0"), Decimal("30")),
    "lviddn": (Decimal("0"), Decimal("10")),
    "lvids_cm": (Decimal("0"), Decimal("30")),
    "fs_percent": (Decimal("0"), Decimal("100")),
    "e_velocity_ms": (Decimal("0"), Decimal("10")),
    "a_velocity_ms": (Decimal("0"), Decimal("10")),
    "tr_vmax_ms": (Decimal("0"), Decimal("10")),
    "pr_vmax_ms": (Decimal("0"), Decimal("10")),
}
INTEGER_FIELDS = {
    "heart_rate_bpm": (1, 500),
    "respiratory_rate_rpm": (1, 300),
    "systolic_bp_mmhg": (20, 350),
    "home_rr_rpm": (1, 200),
    "follow_up_months": (1, 120),
}
CHOICE_FIELDS = {
    "murmur_grade": {"I/VI", "II/VI", "III/VI", "IV/VI", "V/VI", "VI/VI"},
    "mr_severity": {"none", "mild", "moderate", "severe"},
    "acvim_stage": {"A", "B1", "B2", "C", "D"},
    "ph_risk": {"low", "intermediate", "high"},
    "chf_status": {"none", "suspected", "present"},
}
TEXT_FIELDS = {
    "rhythm": 80,
    "findings": 60000,
    "assessment": 60000,
}
EDITABLE_FIELDS = (
    *sorted(BOOLEAN_FIELDS),
    *DECIMAL_FIELDS,
    *INTEGER_FIELDS,
    *CHOICE_FIELDS,
    *TEXT_FIELDS,
)
TREND_FIELDS = (
    "body_weight_kg",
    "heart_rate_bpm",
    "systolic_bp_mmhg",
    "vhs",
    "vlas",
    "la_ao",
    "lviddn",
    "e_velocity_ms",
    "tr_vmax_ms",
    "home_rr_rpm",
)


def _number(value, minimum, maximum, integer=False):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("invalid_number") from error
    if not parsed.is_finite():
        raise ValueError("invalid_number")
    if parsed < minimum or parsed > maximum:
        raise ValueError("number_out_of_range")
    if integer:
        if parsed != parsed.to_integral_value():
            raise ValueError("integer_required")
        return int(parsed)
    return parsed


def clean_cardiac_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("json_body_required")
    cleaned = {}
    for key in EDITABLE_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key in BOOLEAN_FIELDS:
            if type(value) is not bool:
                raise ValueError("invalid_boolean")
        elif key in DECIMAL_FIELDS:
            value = _number(value, *DECIMAL_FIELDS[key])
        elif key in INTEGER_FIELDS:
            value = _number(value, *INTEGER_FIELDS[key], integer=True)
        elif key in CHOICE_FIELDS:
            value = str(value).strip() if value not in (None, "") else None
            if value is not None and value not in CHOICE_FIELDS[key]:
                raise ValueError("invalid_choice")
        elif key in TEXT_FIELDS:
            value = str(value).strip() if value not in (None, "") else None
            if value is not None and len(value) > TEXT_FIELDS[key]:
                raise ValueError("text_too_long")
        cleaned[key] = value
    if not cleaned:
        raise ValueError("no_supported_fields")
    return cleaned


def present_exam(row):
    if not row:
        return None
    result = dict(row)
    for key in (*DECIMAL_FIELDS, "e_a_ratio"):
        if result.get(key) is not None:
            result[key] = float(result[key])
    for key in BOOLEAN_FIELDS:
        result[key] = bool(result.get(key))
    return result


def previous_exam(encounter):
    return fetch_one(
        """
        SELECT ce.*, e.visit_at
        FROM cardiac_exams ce
        JOIN encounters e ON e.id = ce.encounter_id
        WHERE e.patient_id = %s AND e.visit_at < %s
        ORDER BY e.visit_at DESC
        LIMIT 1
        """,
        (encounter["patient_id"], encounter["visit_at"]),
    )


def exam_comparison(encounter_id):
    encounter = fetch_one(
        "SELECT id, patient_id, visit_at, workflow_stage FROM encounters WHERE id = %s",
        (encounter_id,),
    )
    if not encounter:
        return None
    current = fetch_one(
        "SELECT * FROM cardiac_exams WHERE encounter_id = %s", (encounter_id,)
    )
    previous = previous_exam(encounter)
    current_presented = present_exam(current)
    previous_presented = present_exam(previous)
    deltas = {}
    for key in TREND_FIELDS:
        current_value = (current_presented or {}).get(key)
        previous_value = (previous_presented or {}).get(key)
        deltas[key] = (
            round(current_value - previous_value, 3)
            if current_value is not None and previous_value is not None
            else None
        )
    return {
        "encounter_id": encounter_id,
        "exam": current_presented,
        "previous": previous_presented,
        "deltas": deltas,
    }


@bp.get("/encounters/<encounter_id>/cardiac-exam")
def get_cardiac_exam(encounter_id):
    comparison = exam_comparison(encounter_id)
    if comparison is None:
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify(comparison)


@bp.put("/encounters/<encounter_id>/cardiac-exam")
def save_cardiac_exam(encounter_id):
    if session.get("admin_id") and session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    encounter = fetch_one(
        "SELECT id, patient_id, visit_at, workflow_stage FROM encounters WHERE id = %s",
        (encounter_id,),
    )
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    if encounter["workflow_stage"] == "closed":
        return jsonify(
            {
                "error": "encounter_closed",
                "message": "종료된 진료입니다. 진료를 다시 연 뒤 검사 결과를 수정하세요.",
            }
        ), 409
    try:
        cleaned = clean_cardiac_payload(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(
            {"error": "validation_error", "message": f"심장검사 입력값을 확인하세요. ({error})"}
        ), 400
    existing = fetch_one(
        "SELECT * FROM cardiac_exams WHERE encounter_id = %s", (encounter_id,)
    )
    if "e_velocity_ms" in cleaned or "a_velocity_ms" in cleaned:
        e_velocity = cleaned.get(
            "e_velocity_ms", existing.get("e_velocity_ms") if existing else None
        )
        a_velocity = cleaned.get(
            "a_velocity_ms", existing.get("a_velocity_ms") if existing else None
        )
        cleaned["e_a_ratio"] = (
            (e_velocity / a_velocity).quantize(Decimal("0.001"))
            if e_velocity is not None and a_velocity is not None and a_velocity > 0
            else None
        )
    changed = {
        key: value
        for key, value in cleaned.items()
        if not existing or existing.get(key) != value
    }
    if changed:
        with transaction():
            if existing:
                result_id = existing["id"]
                assignments = ", ".join(f"{key} = %s" for key in changed)
                execute(
                    f"UPDATE cardiac_exams SET {assignments} WHERE encounter_id = %s",
                    [*changed.values(), encounter_id],
                )
            else:
                exam_id = new_id()
                result_id = exam_id
                columns = ["id", "encounter_id", *cleaned]
                placeholders = ", ".join(["%s"] * len(columns))
                execute(
                    f"INSERT INTO cardiac_exams ({', '.join(columns)}) VALUES ({placeholders})",
                    [exam_id, encounter_id, *cleaned.values()],
                )
            snapshot = dict(existing or {"id": result_id, "encounter_id": encounter_id})
            snapshot.update(cleaned)
            record_clinical_revision(
                encounter_id,
                "cardiac",
                result_id,
                snapshot,
                "updated" if existing else "created",
                changed,
            )
            mark_soap_stages_stale(encounter_id, ("O", "A", "P"))
            execute(
                "UPDATE encounters SET workflow_stage='examination', workflow_updated_at=CURRENT_TIMESTAMP(6) WHERE id=%s AND workflow_stage IN ('diagnostics','documentation','education','checkout')",
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
            echo_fields = {
                "la_ao", "lvidd_cm", "lviddn", "lvids_cm", "fs_percent",
                "e_velocity_ms", "a_velocity_ms", "tr_vmax_ms", "pr_vmax_ms",
                "mr_severity", "sam", "lvoto", "sec_present", "la_thrombus",
                "pericardial_effusion", "findings", "assessment", "acvim_stage",
                "ph_risk", "chf_status", "follow_up_months",
            }
            vital_fields = {"body_weight_kg", "heart_rate_bpm", "respiratory_rate_rpm", "systolic_bp_mmhg", "home_rr_rpm", "vhs", "vlas"}
            if set(changed) & echo_fields:
                invalidate_diagnostic_review(encounter_id, "echo")
            if set(changed) & vital_fields:
                invalidate_diagnostic_review(encounter_id, "bp")
            if "body_weight_kg" in changed:
                execute(
                    "UPDATE patients SET weight_kg=%s WHERE id=%s",
                    (changed["body_weight_kg"], encounter["patient_id"]),
                )
            audit("cardiac_exam.update", "encounter", encounter_id, {"fields": list(changed)})
    return jsonify(exam_comparison(encounter_id))


@bp.get("/patients/<patient_id>/cardio-trends")
def cardio_trends(patient_id):
    if not fetch_one("SELECT id FROM patients WHERE id = %s", (patient_id,)):
        return jsonify({"error": "patient_not_found"}), 404
    rows = fetch_all(
        """
        SELECT ce.*, e.visit_at
        FROM cardiac_exams ce
        JOIN encounters e ON e.id = ce.encounter_id
        WHERE e.patient_id = %s
        ORDER BY e.visit_at
        """,
        (patient_id,),
    )
    return jsonify({"items": [present_exam(row) for row in rows]})


@bp.get("/encounters/<encounter_id>/clinical-revisions")
def clinical_revisions(encounter_id):
    if not fetch_one("SELECT id FROM encounters WHERE id=%s", (encounter_id,)):
        return jsonify({"error": "encounter_not_found"}), 404
    result_type = request.args.get("result_type")
    params = [encounter_id]
    where = "WHERE r.encounter_id=%s"
    if result_type:
        if result_type not in {"cardiac", "ecg", "lab", "xray"}:
            return jsonify({"error": "invalid_result_type"}), 400
        where += " AND r.result_type=%s"
        params.append(result_type)
    rows = fetch_all(
        f"""
        SELECT r.*, a.username AS created_by_username
        FROM clinical_result_revisions r
        LEFT JOIN admins a ON a.id=r.created_by
        {where}
        ORDER BY r.created_at DESC, r.revision_no DESC
        """,
        params,
    )
    return jsonify({"items": [present_revision(row) for row in rows]})


MONITOR_DECIMALS = {
    "body_weight_kg": (Decimal("0.01"), Decimal("500")),
}
MONITOR_INTEGERS = {
    "home_rr_rpm": (1, 200),
    "systolic_bp_mmhg": (20, 350),
    "heart_rate_bpm": (1, 500),
}
MONITOR_BOOLEANS = ("cough", "dyspnea", "syncope")


def present_monitoring(row):
    result = dict(row)
    if result.get("body_weight_kg") is not None:
        result["body_weight_kg"] = float(result["body_weight_kg"])
    for key in MONITOR_BOOLEANS:
        result[key] = bool(result.get(key))
    return result


@bp.get("/patients/<patient_id>/monitoring")
def list_monitoring(patient_id):
    if not fetch_one("SELECT id FROM patients WHERE id=%s", (patient_id,)):
        return jsonify({"error": "patient_not_found"}), 404
    try:
        limit = min(max(int(request.args.get("limit", "200")), 1), 500)
    except ValueError:
        return jsonify({"error": "invalid_limit"}), 400
    rows = fetch_all(
        """
        SELECT m.*, a.username AS created_by_username
        FROM cardiac_monitoring_logs m
        LEFT JOIN admins a ON a.id=m.created_by
        WHERE m.patient_id=%s
        ORDER BY m.measured_at DESC, m.created_at DESC
        LIMIT %s
        """,
        (patient_id, limit),
    )
    return jsonify({"items": [present_monitoring(row) for row in rows]})


@bp.post("/patients/<patient_id>/monitoring")
@require_roles("veterinarian", "staff")
def create_monitoring(patient_id):
    if not fetch_one("SELECT id FROM patients WHERE id=%s", (patient_id,)):
        return jsonify({"error": "patient_not_found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "json_body_required"}), 400
    try:
        measured_at = parse_iso_datetime(payload.get("measured_at")) or datetime.now()
        source = payload.get("source") or "owner_report"
        if source not in {"clinic", "owner_report", "device"}:
            raise ValueError("invalid_source")
        appetite = payload.get("appetite") or "unknown"
        if appetite not in {"normal", "reduced", "none", "unknown"}:
            raise ValueError("invalid_appetite")
        adherence = payload.get("medication_adherence") or "unknown"
        if adherence not in {"all", "partial", "missed", "unknown"}:
            raise ValueError("invalid_medication_adherence")
        values = {}
        for key, bounds in MONITOR_DECIMALS.items():
            values[key] = _number(payload.get(key), *bounds)
        for key, bounds in MONITOR_INTEGERS.items():
            values[key] = _number(payload.get(key), *bounds, integer=True)
        for key in MONITOR_BOOLEANS:
            value = payload.get(key, False)
            if type(value) is not bool:
                raise ValueError("invalid_boolean")
            values[key] = value
        adverse_effects = str(payload.get("adverse_effects") or "").strip() or None
        notes = str(payload.get("notes") or "").strip() or None
        if adverse_effects and len(adverse_effects) > 2000:
            raise ValueError("adverse_effects_too_long")
        if notes and len(notes) > 4000:
            raise ValueError("notes_too_long")
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400

    encounter_id = payload.get("encounter_id") or None
    if encounter_id:
        encounter = fetch_one(
            "SELECT id FROM encounters WHERE id=%s AND patient_id=%s",
            (encounter_id, patient_id),
        )
        if not encounter:
            return jsonify({"error": "encounter_not_found"}), 404
    correction_of_id = payload.get("correction_of_id") or None
    if correction_of_id:
        corrected = fetch_one(
            "SELECT id FROM cardiac_monitoring_logs WHERE id=%s AND patient_id=%s",
            (correction_of_id, patient_id),
        )
        if not corrected:
            return jsonify({"error": "monitoring_log_not_found"}), 404
    meaningful = any(value is not None and value is not False for value in values.values())
    meaningful = meaningful or appetite != "unknown" or adherence != "unknown"
    meaningful = meaningful or bool(adverse_effects or notes)
    if not meaningful:
        return jsonify({"error": "monitoring_value_required"}), 400

    log_id = new_id()
    with transaction():
        execute(
            """
            INSERT INTO cardiac_monitoring_logs
                (id, patient_id, encounter_id, correction_of_id, measured_at,
                 source, body_weight_kg, home_rr_rpm, systolic_bp_mmhg,
                 heart_rate_bpm, cough, dyspnea, syncope, appetite,
                 medication_adherence, adverse_effects, notes, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                log_id,
                patient_id,
                encounter_id,
                correction_of_id,
                measured_at,
                source,
                values["body_weight_kg"],
                values["home_rr_rpm"],
                values["systolic_bp_mmhg"],
                values["heart_rate_bpm"],
                values["cough"],
                values["dyspnea"],
                values["syncope"],
                appetite,
                adherence,
                adverse_effects,
                notes,
                session.get("admin_id"),
            ),
        )
        audit(
            "cardiac_monitoring.create",
            "cardiac_monitoring_log",
            log_id,
            {"patient_id": patient_id, "correction_of_id": correction_of_id},
        )
    row = fetch_one(
        """
        SELECT m.*, a.username AS created_by_username
        FROM cardiac_monitoring_logs m
        LEFT JOIN admins a ON a.id=m.created_by
        WHERE m.id=%s
        """,
        (log_id,),
    )
    return jsonify(present_monitoring(row)), 201


@bp.get("/dashboard/today")
def today_dashboard():
    raw_date = request.args.get("date")
    try:
        selected = date.fromisoformat(raw_date) if raw_date else date.today()
    except ValueError:
        return jsonify({"error": "invalid_date"}), 400
    start = datetime.combine(selected, time.min)
    end = start + timedelta(days=1)
    items = fetch_all(
        """
        SELECT e.id, e.patient_id, e.visit_at, e.chief_complaint,
               e.status AS soap_status, e.workflow_stage,
               CASE WHEN e.workflow_stage='closed' THEN 'completed' ELSE 'draft' END AS status,
               p.name AS patient_name, p.chart_number,
               COALESCE(a.diagnosis_name, d.name, '미분류 심장 진료') AS diagnosis_name,
               (ce.la_ao IS NOT NULL OR ce.lviddn IS NOT NULL
                OR ce.findings IS NOT NULL) AS has_cardiac_exam
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN diseases d ON d.id = e.disease_id
        LEFT JOIN soap_documents sd ON sd.encounter_id = e.id
        LEFT JOIN soap_sections a ON a.soap_document_id = sd.id
             AND a.stage = 'A' AND a.status = 'confirmed'
        LEFT JOIN cardiac_exams ce ON ce.encounter_id = e.id
        WHERE e.visit_at >= %s AND e.visit_at < %s
        ORDER BY e.visit_at
        """,
        (start, end),
    )
    completed = sum(1 for item in items if item["status"] == "completed")
    appointments = fetch_all(
        """
        SELECT a.*, p.name AS patient_name, p.chart_number
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        WHERE a.starts_at >= %s AND a.starts_at < %s
          AND a.encounter_id IS NULL
        ORDER BY a.starts_at
        """,
        (start, end),
    )
    tasks = fetch_all(
        """
        SELECT t.*, p.name AS patient_name, p.chart_number
        FROM clinic_tasks t
        LEFT JOIN patients p ON p.id = t.patient_id
        WHERE t.due_at >= %s AND t.due_at < %s
        ORDER BY t.status, t.due_at
        """,
        (start, end),
    )
    billing = fetch_one(
        """
        SELECT COALESCE(SUM(CASE WHEN payment_type='payment' THEN amount ELSE -amount END),0) AS paid_total,
               COUNT(DISTINCT CASE WHEN payment_type='payment' THEN invoice_id END) AS paid_count
        FROM payments
        WHERE paid_at >= %s AND paid_at < %s
        """,
        (start, end),
    )
    unpaid = fetch_one(
        """
        SELECT COUNT(*) AS unpaid_count
        FROM encounters e
        LEFT JOIN invoices i ON i.id=(
            SELECT i2.id FROM invoices i2
            WHERE i2.encounter_id=e.id
            ORDER BY i2.version_no DESC LIMIT 1
        )
        WHERE e.visit_at >= %s AND e.visit_at < %s
          AND (i.id IS NULL OR i.status IN ('draft','issued','partially_paid','void'))
        """,
        (start, end),
    )
    billing["unpaid_count"] = unpaid["unpaid_count"]
    return jsonify(
        {
            "date": selected.isoformat(),
            "items": items,
            "appointments": appointments,
            "tasks": tasks,
            "billing": billing,
            "summary": {
                "total": len(items),
                "completed": completed,
                "draft": len(items) - completed,
                "echo_pending": sum(1 for item in items if not item["has_cardiac_exam"]),
            },
        }
    )
