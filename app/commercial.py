import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session

from .auth import require_roles
from .clinical import clean_text, mark_soap_stages_stale, parse_iso_datetime
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .workflow_state import (
    WORKFLOW_STAGES,
    diagnostic_result_exists,
    diagnostics_status,
)


bp = Blueprint("commercial", __name__, url_prefix="/api")

EXAM_TYPES = {"echo", "ecg", "xray", "bp", "lab"}
DIAGNOSTIC_STATUSES = {"planned", "completed", "reviewed", "not_required"}
PAYMENT_METHODS = {"cash", "card", "transfer", "other"}
VETERINARIAN_ONLY_TRANSITION_TARGETS = {
    "diagnostics",
    "documentation",
    "education",
}


def _json():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("json_body_required")
    return value


def _money(value, allow_zero=True):
    try:
        parsed = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("invalid_amount") from error
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise ValueError("invalid_amount")
    return parsed.quantize(Decimal("0.01"))


def _dose(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("invalid_dose") from error
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("99999999"):
        raise ValueError("invalid_dose")
    return parsed.quantize(Decimal("0.0001"))


def _encounter(encounter_id):
    return fetch_one(
        """
        SELECT e.*, p.owner_name, p.owner_phone, p.weight_kg,
               p.current_medications, p.allergies, p.preventive_care,
               p.name AS patient_name, p.chart_number, p.species, p.breed
        FROM encounters e JOIN patients p ON p.id=e.patient_id
        WHERE e.id=%s
        """,
        (encounter_id,),
    )


def _active_prescription(encounter_id):
    row = fetch_one(
        "SELECT * FROM prescriptions WHERE encounter_id=%s", (encounter_id,)
    )
    if row:
        row["items"] = fetch_all(
            "SELECT * FROM prescription_items WHERE prescription_id=%s ORDER BY position_no",
            (row["id"],),
        )
        for item in row["items"]:
            if item.get("dose_value") is not None:
                item["dose_value"] = float(item["dose_value"])
        latest_revision = fetch_one(
            """
            SELECT id, revision_no, status, snapshot_sha256, created_at
            FROM prescription_revisions
            WHERE prescription_id=%s
            ORDER BY revision_no DESC
            LIMIT 1
            """,
            (row["id"],),
        )
        row["latest_revision"] = latest_revision
    return row


def _prescription_is_resolved(prescription):
    if not prescription or prescription.get("review_required"):
        return False
    if prescription.get("status") not in {"issued", "not_required"}:
        return False
    latest_revision = prescription.get("latest_revision") or {}
    return latest_revision.get("status") == prescription.get("status")


def _invoice(encounter_id):
    row = fetch_one(
        "SELECT * FROM invoices WHERE encounter_id=%s ORDER BY version_no DESC LIMIT 1",
        (encounter_id,),
    )
    if not row:
        return None
    row["items"] = fetch_all(
        "SELECT * FROM invoice_items WHERE invoice_id=%s ORDER BY position_no",
        (row["id"],),
    )
    row["payments"] = fetch_all(
        "SELECT * FROM payments WHERE invoice_id=%s ORDER BY paid_at, created_at",
        (row["id"],),
    )
    for key in ("subtotal_amount", "discount_amount", "total_amount", "paid_amount"):
        row[key] = float(row[key])
    for item in row["items"]:
        for key in ("quantity", "unit_amount", "line_amount"):
            item[key] = float(item[key])
    for payment in row["payments"]:
        payment["amount"] = float(payment["amount"])
    return row


def _latest_education(encounter_id):
    return fetch_one(
        "SELECT * FROM client_education_documents WHERE encounter_id=%s ORDER BY version_no DESC LIMIT 1",
        (encounter_id,),
    )


def _encounter_closed_response(encounter):
    if encounter.get("workflow_stage") != "closed":
        return None
    return jsonify(
        {
            "error": "encounter_closed",
            "message": "종료된 진료입니다. 수의사가 진료를 다시 연 뒤 수정하세요.",
        }
    ), 409


def _workflow_stage_response(encounter, allowed_stages):
    if encounter.get("workflow_stage") in allowed_stages:
        return None
    return jsonify(
        {
            "error": "workflow_stage_required",
            "message": "현재 진료 단계에서는 이 작업을 수행할 수 없습니다.",
        }
    ), 409


def _diagnostic_result_exists(encounter_id, exam_type):
    return diagnostic_result_exists(encounter_id, exam_type)


def workflow_blockers(encounter, target_stage):
    blockers = []
    if target_stage == "examination":
        required = {
            "owner_name": encounter.get("owner_name"),
            "owner_phone": encounter.get("owner_phone"),
            "current_medications": encounter.get("current_medications"),
            "allergies": encounter.get("allergies"),
            "chief_complaint": encounter.get("chief_complaint"),
            "history_text": encounter.get("history_text"),
        }
        labels = {
            "owner_name": "보호자 이름",
            "owner_phone": "보호자 연락처",
            "current_medications": "현재 약물(없음 포함)",
            "allergies": "알레르기(없음 포함)",
            "chief_complaint": "주호소",
            "history_text": "History",
        }
        blockers.extend(
            {"code": f"{key}_required", "message": f"{labels[key]} 확인이 필요합니다."}
            for key, value in required.items()
            if not str(value or "").strip()
        )
    elif target_stage == "diagnostics":
        if not str(encounter.get("physical_exam") or "").strip():
            blockers.append({"code": "physical_exam_required", "message": "신체검사가 필요합니다."})
        vitals = fetch_one(
            "SELECT body_weight_kg, heart_rate_bpm, respiratory_rate_rpm, systolic_bp_mmhg FROM cardiac_exams WHERE encounter_id=%s",
            (encounter["id"],),
        ) or {}
        for key, label in (
            ("body_weight_kg", "체중"),
            ("heart_rate_bpm", "심박수"),
            ("respiratory_rate_rpm", "호흡수"),
            ("systolic_bp_mmhg", "수축기 혈압"),
        ):
            if vitals.get(key) is None:
                blockers.append({"code": f"{key}_required", "message": f"{label} 기록이 필요합니다."})
    elif target_stage == "documentation":
        rows, unresolved, invalid_skips = diagnostics_status(encounter["id"])
        if not rows:
            blockers.append({"code": "diagnostic_plan_required", "message": "검사 계획을 한 개 이상 선택하세요."})
        blockers.extend(
            {"code": f"diagnostic_{row['exam_type']}_unresolved", "message": f"{row['exam_type'].upper()} 검토가 필요합니다."}
            for row in unresolved
        )
        blockers.extend(
            {"code": f"diagnostic_{row['exam_type']}_reason_required", "message": f"{row['exam_type'].upper()} 미실시 사유가 필요합니다."}
            for row in invalid_skips
        )
    elif target_stage == "education":
        document = fetch_one(
            "SELECT status FROM soap_documents WHERE encounter_id=%s", (encounter["id"],)
        )
        if not document or document["status"] != "completed":
            blockers.append({"code": "soap_incomplete", "message": "확정되고 최신 상태인 S/O/A/P가 필요합니다."})
    elif target_stage == "checkout":
        prescription = _active_prescription(encounter["id"])
        if not _prescription_is_resolved(prescription):
            blockers.append({"code": "prescription_unresolved", "message": "처방 발행 또는 처방 불필요 확인이 필요합니다."})
        education = _latest_education(encounter["id"])
        if not education or education["status"] != "delivered":
            blockers.append({"code": "education_not_delivered", "message": "보호자 설명서 교부 확인이 필요합니다."})
    elif target_stage == "closed":
        invoice = _invoice(encounter["id"])
        if not invoice or invoice["status"] in {"draft", "void"}:
            blockers.append({"code": "invoice_not_issued", "message": "청구서 발행이 필요합니다."})
        elif (
            invoice.get("issued_at")
            and encounter.get("workflow_updated_at")
            and invoice["issued_at"] < encounter["workflow_updated_at"]
        ):
            blockers.append(
                {
                    "code": "invoice_outdated",
                    "message": "임상 기록 변경 후 청구서를 다시 확인·발행해야 합니다.",
                }
            )
        elif invoice["paid_amount"] < invoice["total_amount"] and not str(invoice.get("deferred_reason") or "").strip():
            blockers.append({"code": "payment_incomplete", "message": "결제 완료 또는 미수 사유가 필요합니다."})
    return blockers


def present_workflow(encounter):
    current = encounter["workflow_stage"]
    index = WORKFLOW_STAGES.index(current)
    next_stage = WORKFLOW_STAGES[index + 1] if index + 1 < len(WORKFLOW_STAGES) else None
    return {
        "encounter_id": encounter["id"],
        "stage": current,
        "stages": list(WORKFLOW_STAGES),
        "next_stage": next_stage,
        "blockers": workflow_blockers(encounter, next_stage) if next_stage else [],
        "closed_at": encounter.get("closed_at"),
    }


@bp.post("/appointments/<appointment_id>/check-in")
def check_in_appointment(appointment_id):
    data = request.get_json(silent=True) or {}
    history = clean_text(data.get("history_text"))
    now = datetime.now()
    with transaction():
        # A row lock makes rapid duplicate check-in requests truly idempotent.
        appointment = fetch_one(
            "SELECT * FROM appointments WHERE id=%s FOR UPDATE", (appointment_id,)
        )
        if not appointment:
            return jsonify({"error": "appointment_not_found"}), 404
        if appointment.get("encounter_id"):
            encounter_id = appointment["encounter_id"]
            created = False
        else:
            if appointment["status"] in {"cancelled", "no_show"}:
                return jsonify({"error": "appointment_not_checkable"}), 409
            encounter_id = new_id()
            complaint = clean_text(data.get("chief_complaint")) or appointment["purpose"]
            execute(
                "INSERT INTO encounters (id, patient_id, visit_at, chief_complaint, history_text, workflow_stage) VALUES (%s,%s,%s,%s,%s,'intake')",
                (encounter_id, appointment["patient_id"], now, complaint, history),
            )
            execute(
                "UPDATE appointments SET encounter_id=%s, status='arrived', arrived_at=%s, checked_in_by=%s WHERE id=%s",
                (encounter_id, now, session.get("admin_id"), appointment_id),
            )
            audit("appointment.check_in", "appointment", appointment_id, {"encounter_id": encounter_id})
            created = True
    return jsonify(
        {
            "appointment": fetch_one("SELECT * FROM appointments WHERE id=%s", (appointment_id,)),
            "encounter": fetch_one("SELECT * FROM encounters WHERE id=%s", (encounter_id,)),
            "created": created,
        }
    ), 201 if created else 200


@bp.get("/encounters/<encounter_id>/workflow")
def get_workflow(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify(present_workflow(encounter))


@bp.post("/encounters/<encounter_id>/workflow/transition")
def transition_workflow(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    data = request.get_json(silent=True) or {}
    if data.get("action") == "reopen":
        if session.get("role") != "veterinarian":
            return jsonify({"error": "permission_denied"}), 403
        if encounter["workflow_stage"] != "closed":
            return jsonify(
                {
                    "error": "encounter_not_closed",
                    "message": "종료된 진료만 다시 열 수 있습니다.",
                }
            ), 409
        target = data.get("target_stage") or "documentation"
        if target not in WORKFLOW_STAGES[: WORKFLOW_STAGES.index("education")]:
            return jsonify({"error": "invalid_workflow_stage"}), 400
        with transaction():
            execute(
                "UPDATE encounters SET workflow_stage=%s, workflow_updated_at=CURRENT_TIMESTAMP(6), closed_at=NULL, closed_by=NULL WHERE id=%s",
                (target, encounter_id),
            )
            audit("workflow.reopen", "encounter", encounter_id, {"target": target})
        return jsonify(present_workflow(_encounter(encounter_id)))
    current_index = WORKFLOW_STAGES.index(encounter["workflow_stage"])
    if current_index + 1 >= len(WORKFLOW_STAGES):
        return jsonify({"error": "encounter_already_closed"}), 409
    target = WORKFLOW_STAGES[current_index + 1]
    if (
        target in VETERINARIAN_ONLY_TRANSITION_TARGETS
        and session.get("role") != "veterinarian"
    ):
        return jsonify({"error": "permission_denied", "message": "수의사 확인이 필요한 단계입니다."}), 403
    blockers = workflow_blockers(encounter, target)
    if blockers:
        return jsonify({"error": "workflow_blocked", "blockers": blockers}), 409
    now = datetime.now()
    with transaction():
        execute(
            "UPDATE encounters SET workflow_stage=%s, workflow_updated_at=%s, closed_at=%s, closed_by=%s WHERE id=%s",
            (target, now, now if target == "closed" else None, session.get("admin_id") if target == "closed" else None, encounter_id),
        )
        if target == "closed":
            execute(
                "UPDATE appointments SET status='completed' WHERE encounter_id=%s",
                (encounter_id,),
            )
        audit("workflow.transition", "encounter", encounter_id, {"from": encounter["workflow_stage"], "to": target})
    return jsonify(present_workflow(_encounter(encounter_id)))


@bp.get("/encounters/<encounter_id>/diagnostic-plan")
def get_diagnostic_plan(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    rows, _, _ = diagnostics_status(encounter_id)
    return jsonify({"items": rows})


@bp.put("/encounters/<encounter_id>/diagnostic-plan")
def save_diagnostic_plan(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _encounter_closed_response(encounter)
    if closed:
        return closed
    existing_rows, _, _ = diagnostics_status(encounter_id)
    if session.get("role") != "veterinarian" and any(
        row["status"] in {"reviewed", "not_required"} for row in existing_rows
    ):
        return jsonify(
            {
                "error": "permission_denied",
                "message": "수의사가 검토한 검사 계획은 수의사만 변경할 수 있습니다.",
            }
        ), 403
    try:
        items = _json().get("items")
        if not isinstance(items, list):
            raise ValueError("items_required")
        cleaned = []
        seen = set()
        for item in items:
            exam_type = item.get("exam_type")
            status = item.get("status") or "planned"
            reason = clean_text(item.get("reason"), 500)
            if exam_type not in EXAM_TYPES or exam_type in seen or status not in DIAGNOSTIC_STATUSES:
                raise ValueError("invalid_diagnostic_item")
            if status in {"reviewed", "not_required"} and session.get("role") != "veterinarian":
                return jsonify({"error": "permission_denied"}), 403
            if status == "not_required" and not reason:
                raise ValueError("reason_required")
            if status == "reviewed" and not _diagnostic_result_exists(encounter_id, exam_type):
                return jsonify(
                    {
                        "error": "diagnostic_result_required",
                        "message": f"{exam_type.upper()} 결과를 먼저 기록하세요.",
                    }
                ), 409
            cleaned.append((exam_type, status, reason))
            seen.add(exam_type)
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    existing_values = sorted(
        (row["exam_type"], row["status"], row.get("reason"))
        for row in existing_rows
    )
    changed = existing_values != sorted(cleaned)
    with transaction():
        execute("DELETE FROM diagnostic_requirements WHERE encounter_id=%s", (encounter_id,))
        for exam_type, status, reason in cleaned:
            execute(
                "INSERT INTO diagnostic_requirements (id,encounter_id,exam_type,status,reason,reviewed_by,reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (new_id(), encounter_id, exam_type, status, reason, session.get("admin_id") if status == "reviewed" else None, datetime.now() if status == "reviewed" else None),
            )
        if changed:
            from .workflow_state import invalidate_workflow

            mark_soap_stages_stale(encounter_id, ("A", "P"))
            invalidate_workflow(encounter_id, "diagnostics")
        audit("diagnostics.plan", "encounter", encounter_id, {"items": [item[0] for item in cleaned]})
    rows, _, _ = diagnostics_status(encounter_id)
    return jsonify({"items": rows})


@bp.get("/encounters/<encounter_id>/education")
def get_education(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify({"document": _latest_education(encounter_id)})


@bp.post("/encounters/<encounter_id>/education")
@require_roles("veterinarian")
def create_education(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _encounter_closed_response(encounter)
    if closed:
        return closed
    wrong_stage = _workflow_stage_response(encounter, {"education"})
    if wrong_stage:
        return wrong_stage
    sections = fetch_all(
        """
        SELECT ss.stage, ss.current_text, ss.diagnosis_name, ss.status, r.id AS revision_id
        FROM soap_documents sd JOIN soap_sections ss ON ss.soap_document_id=sd.id
        JOIN soap_section_revisions r ON r.section_id=ss.id
        WHERE sd.encounter_id=%s AND ss.stage IN ('A','P')
          AND r.revision_no=(SELECT MAX(r2.revision_no) FROM soap_section_revisions r2 WHERE r2.section_id=ss.id)
        """,
        (encounter_id,),
    )
    by_stage = {row["stage"]: row for row in sections}
    if any(stage not in by_stage or by_stage[stage]["status"] != "confirmed" for stage in ("A", "P")):
        return jsonify({"error": "soap_not_confirmed"}), 409
    prescription = _active_prescription(encounter_id)
    if not _prescription_is_resolved(prescription):
        return jsonify({"error": "prescription_unresolved", "message": "처방을 먼저 확정하세요."}), 409
    exam = fetch_one(
        "SELECT follow_up_months, home_rr_rpm FROM cardiac_exams WHERE encounter_id=%s",
        (encounter_id,),
    ) or {}
    snapshot = {
        "patient": {"name": encounter["patient_name"], "species": encounter["species"], "breed": encounter["breed"]},
        "diagnosis": by_stage["A"].get("diagnosis_name"),
        "assessment": by_stage["A"]["current_text"],
        "plan": by_stage["P"]["current_text"],
        "prescription": {"status": prescription["status"], "items": prescription.get("items", []), "instructions": prescription.get("instructions")},
        "monitoring": {"home_rr_rpm": exam.get("home_rr_rpm"), "follow_up_months": exam.get("follow_up_months")},
        "warning_signs": "호흡곤란, 실신, 청색증, 안정 시 호흡수의 뚜렷한 증가는 즉시 병원에 문의하세요.",
    }
    content = json.dumps(snapshot, ensure_ascii=False, default=str)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    latest = _latest_education(encounter_id)
    version = (latest["version_no"] if latest else 0) + 1
    document_id = new_id()
    with transaction():
        execute(
            "UPDATE client_education_documents SET status='superseded' WHERE encounter_id=%s AND status IN ('draft','delivered')",
            (encounter_id,),
        )
        execute(
            "INSERT INTO client_education_documents (id,encounter_id,version_no,source_a_revision_id,source_p_revision_id,content_snapshot,content_sha256,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (document_id, encounter_id, version, by_stage["A"]["revision_id"], by_stage["P"]["revision_id"], content, digest, session.get("admin_id")),
        )
        audit("education.create", "education", document_id, {"version": version})
    return jsonify({"document": _latest_education(encounter_id)}), 201


@bp.post("/encounters/<encounter_id>/education/deliver")
def deliver_education(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _encounter_closed_response(encounter)
    if closed:
        return closed
    wrong_stage = _workflow_stage_response(encounter, {"education"})
    if wrong_stage:
        return wrong_stage
    document = _latest_education(encounter_id)
    if not document or document["status"] != "draft":
        return jsonify({"error": "education_draft_not_found"}), 409
    method = (request.get_json(silent=True) or {}).get("method")
    if method not in {"print", "pdf"}:
        return jsonify({"error": "invalid_delivery_method"}), 400
    with transaction():
        execute(
            "UPDATE client_education_documents SET status='delivered', delivery_method=%s, delivered_by=%s, delivered_at=%s WHERE id=%s",
            (method, session.get("admin_id"), datetime.now(), document["id"]),
        )
        audit("education.deliver", "education", document["id"], {"method": method})
    return jsonify({"document": _latest_education(encounter_id)})


@bp.get("/encounters/<encounter_id>/structured-prescription")
def get_structured_prescription(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify({"prescription": _active_prescription(encounter_id)})


@bp.put("/encounters/<encounter_id>/structured-prescription")
@require_roles("veterinarian")
def save_structured_prescription(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    closed = _encounter_closed_response(encounter)
    if closed:
        return closed
    wrong_stage = _workflow_stage_response(encounter, {"education", "checkout"})
    if wrong_stage:
        return wrong_stage
    try:
        data = _json()
        status = data.get("status") or "draft"
        if status not in {"draft", "issued", "not_required", "cancelled"}:
            raise ValueError("invalid_status")
        reason = clean_text(data.get("not_required_reason"), 500)
        raw_items = data.get("items") or []
        if status == "not_required" and not reason:
            raise ValueError("reason_required")
        if status == "issued":
            if encounter.get("weight_kg") is None or encounter.get("current_medications") is None or encounter.get("allergies") is None:
                raise ValueError("medication_safety_review_required")
            if not raw_items:
                raise ValueError("medication_required")
        items = []
        for position, item in enumerate(raw_items, 1):
            name = clean_text(item.get("medication_name"), 255, True)
            dose = _dose(item.get("dose_value"))
            unit = clean_text(item.get("dose_unit"), 40, True)
            route = clean_text(item.get("route"), 80, True)
            frequency = clean_text(item.get("frequency"), 120, True)
            duration = int(item.get("duration_days"))
            if duration < 1 or duration > 3650:
                raise ValueError("invalid_duration")
            items.append((position, name, dose, unit, route, frequency, duration, clean_text(item.get("instructions"), 1000)))
        instructions = clean_text(data.get("instructions"))
    except (ValueError, TypeError) as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    existing = fetch_one("SELECT * FROM prescriptions WHERE encounter_id=%s", (encounter_id,))
    prescription_id = existing["id"] if existing else new_id()
    medication_text = "\n".join(
        f"{name} {dose} {unit}, {route}, {frequency}, {duration}일"
        for _, name, dose, unit, route, frequency, duration, _ in items
    ) or None
    revision_snapshot = json.dumps(
        {
            "status": status,
            "items": [
                {
                    "position_no": position,
                    "medication_name": name,
                    "dose_value": str(dose),
                    "dose_unit": unit,
                    "route": route,
                    "frequency": frequency,
                    "duration_days": duration,
                    "instructions": item_instructions,
                }
                for position, name, dose, unit, route, frequency, duration, item_instructions in items
            ],
            "instructions": instructions,
            "not_required_reason": reason,
        },
        ensure_ascii=False,
    )
    revision_digest = hashlib.sha256(revision_snapshot.encode("utf-8")).hexdigest()
    revision_no = fetch_one(
        "SELECT COALESCE(MAX(revision_no),0)+1 AS next_revision FROM prescription_revisions WHERE prescription_id=%s",
        (prescription_id,),
    )["next_revision"]
    now = datetime.now()
    with transaction():
        if existing:
            execute(
                "UPDATE prescriptions SET medication_text=%s,instructions=%s,status=%s,issued_at=%s,issued_by=%s,review_required=FALSE,not_required_reason=%s,cancelled_at=%s WHERE id=%s",
                (medication_text, instructions, status, now if status == "issued" else None, session.get("admin_id") if status in {"issued", "not_required"} else None, reason, now if status == "cancelled" else None, prescription_id),
            )
            execute("DELETE FROM prescription_items WHERE prescription_id=%s", (prescription_id,))
        else:
            execute(
                "INSERT INTO prescriptions (id,encounter_id,issued_at,medication_text,instructions,status,issued_by,not_required_reason,cancelled_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (prescription_id, encounter_id, now if status == "issued" else None, medication_text, instructions, status, session.get("admin_id") if status in {"issued", "not_required"} else None, reason, now if status == "cancelled" else None),
            )
        for item in items:
            execute(
                "INSERT INTO prescription_items (id,prescription_id,position_no,medication_name,dose_value,dose_unit,route,frequency,duration_days,instructions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (new_id(), prescription_id, *item),
            )
        execute(
            "INSERT INTO prescription_revisions (id,prescription_id,revision_no,status,snapshot_json,snapshot_sha256,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (new_id(), prescription_id, revision_no, status, revision_snapshot, revision_digest, session.get("admin_id")),
        )
        execute(
            "UPDATE client_education_documents SET status='superseded' WHERE encounter_id=%s AND status IN ('draft','delivered')",
            (encounter_id,),
        )
        execute(
            "UPDATE encounters SET workflow_stage='education', workflow_updated_at=CURRENT_TIMESTAMP(6) WHERE id=%s AND workflow_stage='checkout'",
            (encounter_id,),
        )
        audit("prescription.structured_update", "prescription", prescription_id, {"status": status, "item_count": len(items)})
    return jsonify({"prescription": _active_prescription(encounter_id)})


@bp.get("/encounters/<encounter_id>/invoice")
def get_invoice(encounter_id):
    if not _encounter(encounter_id):
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify({"invoice": _invoice(encounter_id)})


@bp.put("/encounters/<encounter_id>/invoice")
def save_invoice(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    existing = fetch_one(
        "SELECT * FROM invoices WHERE encounter_id=%s ORDER BY version_no DESC LIMIT 1",
        (encounter_id,),
    )
    if existing and existing["status"] not in {"draft", "void"}:
        return jsonify({"error": "issued_invoice_immutable"}), 409
    try:
        data = _json()
        raw_items = data.get("items") or []
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("invoice_items_required")
        items = []
        subtotal = Decimal("0")
        for position, item in enumerate(raw_items, 1):
            description = clean_text(item.get("description"), 255, True)
            quantity = _money(item.get("quantity") or 1, allow_zero=False)
            unit_amount = _money(item.get("unit_amount"))
            line_amount = (quantity * unit_amount).quantize(Decimal("0.01"))
            subtotal += line_amount
            items.append((position, clean_text(item.get("item_code"), 80), description, quantity, unit_amount, line_amount))
        discount = _money(data.get("discount_amount") or 0)
        total = max(Decimal("0"), subtotal - discount)
        deferred_reason = clean_text(data.get("deferred_reason"), 500)
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    replaces_invoice_id = existing["id"] if existing and existing["status"] == "void" else None
    draft = existing if existing and existing["status"] == "draft" else None
    invoice_id = draft["id"] if draft else new_id()
    invoice_number = draft["invoice_number"] if draft else f"INV-{datetime.now():%Y%m%d}-{invoice_id[:8].upper()}"
    version_no = draft["version_no"] if draft else (
        fetch_one(
            "SELECT COALESCE(MAX(version_no),0)+1 AS next_version FROM invoices WHERE encounter_id=%s",
            (encounter_id,),
        )["next_version"]
    )
    with transaction():
        if draft:
            execute(
                "UPDATE invoices SET subtotal_amount=%s,discount_amount=%s,total_amount=%s,deferred_reason=%s WHERE id=%s",
                (subtotal, discount, total, deferred_reason, invoice_id),
            )
            execute("DELETE FROM invoice_items WHERE invoice_id=%s", (invoice_id,))
        else:
            execute(
                "INSERT INTO invoices (id,encounter_id,version_no,replaces_invoice_id,invoice_number,subtotal_amount,discount_amount,total_amount,deferred_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (invoice_id, encounter_id, version_no, replaces_invoice_id, invoice_number, subtotal, discount, total, deferred_reason),
            )
        for item in items:
            execute(
                "INSERT INTO invoice_items (id,invoice_id,position_no,item_code,description,quantity,unit_amount,line_amount) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (new_id(), invoice_id, *item),
            )
        audit("invoice.update", "invoice", invoice_id, {"total": str(total)})
    return jsonify({"invoice": _invoice(encounter_id)})


@bp.post("/encounters/<encounter_id>/invoice/issue")
def issue_invoice(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    invoice = _invoice(encounter_id)
    if not invoice:
        return jsonify({"error": "invoice_not_found"}), 404
    if invoice["status"] != "draft":
        return jsonify({"error": "invoice_already_issued"}), 409
    with transaction():
        execute(
            "UPDATE invoices SET status=%s,issued_at=%s,issued_by=%s WHERE id=%s",
            ("paid" if invoice["total_amount"] == 0 else "issued", datetime.now(), session.get("admin_id"), invoice["id"]),
        )
        audit("invoice.issue", "invoice", invoice["id"])
    return jsonify({"invoice": _invoice(encounter_id)})


@bp.patch("/encounters/<encounter_id>/invoice/deferred-reason")
def update_deferred_reason(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    invoice = _invoice(encounter_id)
    if not invoice or invoice["status"] in {"draft", "void"}:
        return jsonify({"error": "issued_invoice_required"}), 409
    try:
        reason = clean_text(_json().get("reason"), 500, True)
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    with transaction():
        execute("UPDATE invoices SET deferred_reason=%s WHERE id=%s", (reason, invoice["id"]))
        audit("invoice.deferred_reason", "invoice", invoice["id"], {"reason": reason})
    return jsonify({"invoice": _invoice(encounter_id)})


@bp.post("/encounters/<encounter_id>/invoice/void")
def void_invoice(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    invoice = _invoice(encounter_id)
    if not invoice or invoice["status"] in {"draft", "void"}:
        return jsonify({"error": "issued_invoice_required"}), 409
    try:
        reason = clean_text(_json().get("reason"), 500, True)
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    with transaction():
        locked = fetch_one(
            "SELECT status,paid_amount FROM invoices WHERE id=%s FOR UPDATE", (invoice["id"],)
        )
        if not locked or locked["status"] in {"draft", "void"}:
            return jsonify({"error": "issued_invoice_required"}), 409
        if Decimal(str(locked["paid_amount"])) > 0:
            return jsonify({"error": "paid_invoice_cannot_be_voided", "message": "결제분을 먼저 전액 환불하세요."}), 409
        execute(
            "UPDATE invoices SET status='void',voided_at=%s,void_reason=%s WHERE id=%s",
            (datetime.now(), reason, invoice["id"]),
        )
        audit("invoice.void", "invoice", invoice["id"], {"reason": reason})
    return jsonify({"invoice": _invoice(encounter_id)})


def _refresh_invoice_payment(invoice_id):
    totals = fetch_one(
        "SELECT COALESCE(SUM(CASE WHEN payment_type='payment' THEN amount ELSE -amount END),0) AS paid FROM payments WHERE invoice_id=%s",
        (invoice_id,),
    )
    invoice = fetch_one("SELECT total_amount,status FROM invoices WHERE id=%s", (invoice_id,))
    paid = max(Decimal("0"), totals["paid"])
    if paid >= invoice["total_amount"]:
        status = "paid"
    elif paid > 0:
        status = "partially_paid"
    else:
        status = "issued"
    execute("UPDATE invoices SET paid_amount=%s,status=%s WHERE id=%s", (paid, status, invoice_id))


@bp.post("/encounters/<encounter_id>/invoice/payments")
def record_payment(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    invoice = _invoice(encounter_id)
    if not invoice or invoice["status"] in {"draft", "void"}:
        return jsonify({"error": "issued_invoice_required"}), 409
    try:
        data = _json()
        method = data.get("payment_method")
        if method not in PAYMENT_METHODS:
            raise ValueError("invalid_payment_method")
        amount = _money(data.get("amount"), allow_zero=False)
        paid_at = parse_iso_datetime(data.get("paid_at")) or datetime.now()
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    payment_id = new_id()
    receipt_number = f"RCP-{paid_at:%Y%m%d}-{payment_id[:8].upper()}"
    snapshot = json.dumps(
        {
            "receipt_number": receipt_number,
            "invoice_number": invoice["invoice_number"],
            "patient": {"name": encounter["patient_name"], "chart_number": encounter["chart_number"]},
            "items": invoice["items"],
            "invoice_total": invoice["total_amount"],
            "amount": str(amount),
            "method": method,
            "paid_at": paid_at.isoformat(),
        },
        ensure_ascii=False,
        default=str,
    )
    with transaction():
        # Lock the invoice while recalculating the balance so duplicate clicks
        # cannot both spend the same outstanding amount.
        locked_invoice = fetch_one(
            "SELECT total_amount,status FROM invoices WHERE id=%s FOR UPDATE",
            (invoice["id"],),
        )
        if not locked_invoice or locked_invoice["status"] in {"draft", "void"}:
            return jsonify({"error": "issued_invoice_required"}), 409
        if fetch_one("SELECT id FROM daily_closings WHERE business_date=%s", (paid_at.date(),)):
            return jsonify({"error": "business_day_closed", "message": "마감된 날짜에는 결제를 추가할 수 없습니다."}), 409
        paid = fetch_one(
            "SELECT COALESCE(SUM(CASE WHEN payment_type='payment' THEN amount ELSE -amount END),0) AS amount FROM payments WHERE invoice_id=%s",
            (invoice["id"],),
        )["amount"]
        balance = Decimal(str(locked_invoice["total_amount"])) - Decimal(str(paid))
        if amount > balance:
            return jsonify({"error": "validation_error", "message": "payment_exceeds_balance"}), 400
        execute(
            "INSERT INTO payments (id,invoice_id,payment_type,payment_method,amount,receipt_number,receipt_snapshot,paid_at,recorded_by) VALUES (%s,%s,'payment',%s,%s,%s,%s,%s,%s)",
            (payment_id, invoice["id"], method, amount, receipt_number, snapshot, paid_at, session.get("admin_id")),
        )
        _refresh_invoice_payment(invoice["id"])
        audit("payment.record", "payment", payment_id, {"invoice_id": invoice["id"], "amount": str(amount)})
    return jsonify({"invoice": _invoice(encounter_id), "receipt_number": receipt_number}), 201


@bp.get("/payments/<payment_id>/receipt")
def get_receipt(payment_id):
    row = fetch_one(
        "SELECT id,payment_type,receipt_number,receipt_snapshot,paid_at FROM payments WHERE id=%s",
        (payment_id,),
    )
    if not row:
        return jsonify({"error": "receipt_not_found"}), 404
    try:
        snapshot = json.loads(row["receipt_snapshot"])
    except (TypeError, json.JSONDecodeError):
        snapshot = {"receipt_number": row["receipt_number"], "paid_at": row["paid_at"]}
    return jsonify({"payment_type": row["payment_type"], "snapshot": snapshot})


@bp.post("/encounters/<encounter_id>/invoice/refunds")
def record_refund(encounter_id):
    encounter = _encounter(encounter_id)
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    wrong_stage = _workflow_stage_response(encounter, {"checkout", "closed"})
    if wrong_stage:
        return wrong_stage
    invoice = _invoice(encounter_id)
    if not invoice or invoice["paid_amount"] <= 0:
        return jsonify({"error": "refundable_payment_not_found"}), 409
    try:
        data = _json()
        method = data.get("payment_method")
        if method not in PAYMENT_METHODS:
            raise ValueError("invalid_payment_method")
        amount = _money(data.get("amount"), allow_zero=False)
        reason = clean_text(data.get("reason"), 500, True)
        paid_at = parse_iso_datetime(data.get("paid_at")) or datetime.now()
    except ValueError as error:
        return jsonify({"error": "validation_error", "message": str(error)}), 400
    payment_id = new_id()
    receipt_number = f"REF-{paid_at:%Y%m%d}-{payment_id[:8].upper()}"
    snapshot = json.dumps(
        {
            "receipt_number": receipt_number,
            "invoice_number": invoice["invoice_number"],
            "refund": str(amount),
            "method": method,
            "reason": reason,
            "paid_at": paid_at.isoformat(),
        },
        ensure_ascii=False,
    )
    with transaction():
        locked_invoice = fetch_one(
            "SELECT status FROM invoices WHERE id=%s FOR UPDATE", (invoice["id"],)
        )
        if not locked_invoice or locked_invoice["status"] in {"draft", "void"}:
            return jsonify({"error": "refundable_payment_not_found"}), 409
        if fetch_one("SELECT id FROM daily_closings WHERE business_date=%s", (paid_at.date(),)):
            return jsonify({"error": "business_day_closed", "message": "마감된 날짜에는 환불을 추가할 수 없습니다."}), 409
        paid = fetch_one(
            "SELECT COALESCE(SUM(CASE WHEN payment_type='payment' THEN amount ELSE -amount END),0) AS amount FROM payments WHERE invoice_id=%s",
            (invoice["id"],),
        )["amount"]
        if amount > Decimal(str(paid)):
            return jsonify({"error": "validation_error", "message": "refund_exceeds_paid"}), 400
        execute(
            "INSERT INTO payments (id,invoice_id,payment_type,payment_method,amount,receipt_number,receipt_snapshot,reason,paid_at,recorded_by) VALUES (%s,%s,'refund',%s,%s,%s,%s,%s,%s,%s)",
            (payment_id, invoice["id"], method, amount, receipt_number, snapshot, reason, paid_at, session.get("admin_id")),
        )
        _refresh_invoice_payment(invoice["id"])
        audit("payment.refund", "payment", payment_id, {"invoice_id": invoice["id"], "amount": str(amount)})
    return jsonify({"invoice": _invoice(encounter_id), "receipt_number": receipt_number}), 201


def daily_summary(selected):
    start = datetime.combine(selected, time.min)
    end = start + timedelta(days=1)
    rows = fetch_all(
        """
        SELECT payment_method,
               COALESCE(SUM(CASE WHEN payment_type='payment' THEN amount ELSE 0 END),0) AS payments,
               COALESCE(SUM(CASE WHEN payment_type='refund' THEN amount ELSE 0 END),0) AS refunds
        FROM payments WHERE paid_at >= %s AND paid_at < %s GROUP BY payment_method
        """,
        (start, end),
    )
    methods = {method: {"payments": 0.0, "refunds": 0.0, "net": 0.0} for method in PAYMENT_METHODS}
    for row in rows:
        payments = float(row["payments"])
        refunds = float(row["refunds"])
        methods[row["payment_method"]] = {"payments": payments, "refunds": refunds, "net": payments - refunds}
    outstanding = fetch_one(
        "SELECT COALESCE(SUM(total_amount-paid_amount),0) AS amount, COUNT(*) AS count FROM invoices WHERE status IN ('issued','partially_paid')"
    )
    billed = fetch_one(
        "SELECT COALESCE(SUM(total_amount),0) AS amount FROM invoices WHERE issued_at >= %s AND issued_at < %s AND status <> 'void'",
        (start, end),
    )["amount"]
    unbilled = fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM encounters e
        LEFT JOIN invoices i ON i.id=(
            SELECT i2.id FROM invoices i2 WHERE i2.encounter_id=e.id
            ORDER BY i2.version_no DESC LIMIT 1
        )
        WHERE e.visit_at >= %s AND e.visit_at < %s
          AND (i.id IS NULL OR i.status IN ('draft','void'))
        """,
        (start, end),
    )["count"]
    net = sum(item["net"] for item in methods.values())
    return {
        "date": selected.isoformat(),
        "methods": methods,
        "payments": sum(item["payments"] for item in methods.values()),
        "refunds": sum(item["refunds"] for item in methods.values()),
        "net": net,
        "billed_amount": float(billed),
        "billing_payment_difference": float(billed) - net,
        "unbilled_count": unbilled,
        "outstanding_amount": float(outstanding["amount"]),
        "outstanding_count": outstanding["count"],
    }


@bp.get("/billing/daily-close")
def get_daily_close():
    try:
        selected = date.fromisoformat(request.args.get("date")) if request.args.get("date") else date.today()
    except ValueError:
        return jsonify({"error": "invalid_date"}), 400
    return jsonify({"summary": daily_summary(selected), "closing": fetch_one("SELECT * FROM daily_closings WHERE business_date=%s", (selected,))})


@bp.post("/billing/daily-close")
def close_day():
    try:
        selected = date.fromisoformat(_json().get("date"))
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_date"}), 400
    if selected > date.today():
        return jsonify({"error": "future_day_cannot_close"}), 400
    if fetch_one("SELECT id FROM daily_closings WHERE business_date=%s", (selected,)):
        return jsonify({"error": "day_already_closed"}), 409
    summary = daily_summary(selected)
    if summary["unbilled_count"]:
        return jsonify(
            {
                "error": "daily_close_blocked",
                "message": f"미청구 진료 {summary['unbilled_count']}건을 먼저 처리하세요.",
                "summary": summary,
            }
        ), 409
    closing_id = new_id()
    with transaction():
        execute(
            "INSERT INTO daily_closings (id,business_date,totals_snapshot,closed_by) VALUES (%s,%s,%s,%s)",
            (closing_id, selected, json.dumps(summary, ensure_ascii=False), session.get("admin_id")),
        )
        audit("billing.daily_close", "daily_closing", closing_id, {"date": selected.isoformat()})
    return jsonify({"summary": summary, "closing": fetch_one("SELECT * FROM daily_closings WHERE id=%s", (closing_id,))}), 201
