import json
import time
from datetime import datetime

from flask import Blueprint, current_app, jsonify, session

from .ai import AIServiceError, get_llm
from .auth import require_roles
from .db import audit, execute, fetch_one, new_id, transaction
from .soap import (
    build_context,
    build_evidence_queries,
    ensure_document,
    retrieve_evidence,
    section_rows,
)


bp = Blueprint("agent", __name__, url_prefix="/api")
AGENT_PROMPT_VERSION = "cardiac-readonly-review-v1"
REVIEW_LIST_KEYS = (
    "priority_checks",
    "questions",
    "observations",
    "evidence_considerations",
)
SENSITIVE_CONTEXT_KEYS = {
    "id",
    "patient_id",
    "encounter_id",
    "chart_number",
    "name",
    "owner_name",
    "owner_phone",
    "storage_key",
    "original_name",
}


def sanitize_agent_context(value):
    """Remove direct record identifiers before persistence or external AI use."""

    if isinstance(value, dict):
        return {
            key: sanitize_agent_context(item)
            for key, item in value.items()
            if key not in SENSITIVE_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [sanitize_agent_context(item) for item in value]
    return value


def _present_evidence(item):
    return {
        key: item.get(key)
        for key in (
            "chunk_id",
            "document_name",
            "source_type",
            "source_heading",
            "source_edition",
            "source_volume",
            "page_start",
            "page_end",
            "score",
            "excerpt",
        )
    }


def build_review_signals(context):
    patient = context.get("patient") or {}
    encounter = context.get("encounter") or {}
    medication = context.get("medication_safety") or {}
    exam = context.get("cardiac_exam") or {}
    plan = context.get("diagnostic_plan") or []
    priority_checks = []
    questions = []
    observations = []

    if not str(encounter.get("physical_exam") or "").strip():
        priority_checks.append("신체검사/기초 소견이 입력되지 않았습니다.")
    if not str(medication.get("current_medications") or "").strip():
        priority_checks.append("현재 약물 복용 여부를 확인해야 합니다.")
    if not str(medication.get("allergies") or "").strip():
        priority_checks.append("알레르기 유무를 확인해야 합니다.")
    if not exam:
        priority_checks.append("구조화된 심장검사 또는 활력징후가 없습니다.")
    else:
        for key, label in (
            ("body_weight_kg", "체중"),
            ("heart_rate_bpm", "심박수"),
            ("respiratory_rate_rpm", "호흡수"),
            ("systolic_bp_mmhg", "수축기 혈압"),
        ):
            if exam.get(key) is None:
                priority_checks.append(f"{label} 구조화 값이 없습니다.")
        if exam.get("acvim_stage") and (
            exam.get("la_ao") is None or exam.get("lviddn") is None
        ):
            priority_checks.append(
                "ACVIM 병기가 기록됐지만 LA/Ao 또는 LVIDDN이 없어 근거 확인이 필요합니다."
            )

    unresolved = [
        item.get("exam_type", "검사")
        for item in plan
        if item.get("status") in {"planned", "completed"}
    ]
    if unresolved:
        priority_checks.append(
            "검토가 끝나지 않은 검사: " + ", ".join(str(item).upper() for item in unresolved)
        )
    if not plan:
        questions.append("이번 진료에 필요한 검사계획을 선택했습니까?")
    if exam and exam.get("la_ao") is None and exam.get("lviddn") is None:
        questions.append("병기 평가를 위해 심초음파 측정값을 추가할 예정입니까?")

    prior = (context.get("prior_cardiac_trends") or [None])[0]
    if exam and prior:
        labels = {
            "body_weight_kg": "체중",
            "heart_rate_bpm": "심박수",
            "systolic_bp_mmhg": "수축기 혈압",
            "vhs": "VHS",
            "vlas": "VLAS",
            "home_rr_rpm": "안정 시 호흡수",
            "la_ao": "LA/Ao",
            "lviddn": "LVIDDN",
            "e_velocity_ms": "E velocity",
            "tr_vmax_ms": "TR Vmax",
        }
        for key, label in labels.items():
            current_value = exam.get(key)
            prior_value = prior.get(key)
            if current_value is None or prior_value is None:
                continue
            delta = float(current_value) - float(prior_value)
            observations.append(
                f"{label}: 이전 {prior_value} → 현재 {current_value} (변화 {delta:+.3f})"
            )
    if patient.get("weight_kg") is not None and exam.get("body_weight_kg") is not None:
        if float(patient["weight_kg"]) != float(exam["body_weight_kg"]):
            observations.append(
                f"환자 기본 체중 {patient['weight_kg']} kg과 이번 검사 체중 {exam['body_weight_kg']} kg이 다릅니다."
            )

    return {
        "priority_checks": priority_checks,
        "questions": questions,
        "observations": observations,
    }


def _decode_json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def present_run(row):
    if not row:
        return None
    output = _decode_json(row.get("output_json"), {})
    return {
        "id": row["id"],
        "status": row["status"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "result": output,
        "evidence": _decode_json(row.get("evidence_json"), []),
        "latency_ms": row.get("latency_ms"),
        "input_tokens": row.get("input_tokens"),
        "output_tokens": row.get("output_tokens"),
        "created_at": row.get("created_at"),
        "completed_at": row.get("completed_at"),
    }


@bp.get("/encounters/<encounter_id>/agent-review")
@require_roles("veterinarian")
def get_agent_review(encounter_id):
    if not fetch_one("SELECT id FROM encounters WHERE id=%s", (encounter_id,)):
        return jsonify({"error": "encounter_not_found"}), 404
    row = fetch_one(
        "SELECT * FROM agent_review_runs WHERE encounter_id=%s AND status='completed' ORDER BY created_at DESC LIMIT 1",
        (encounter_id,),
    )
    return jsonify({"review": present_run(row)})


@bp.post("/encounters/<encounter_id>/agent-review")
@require_roles("veterinarian")
def generate_agent_review(encounter_id):
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    sections = section_rows(document["id"])
    context, base_query = build_context(encounter_id, "A", sections)
    context = sanitize_agent_context(context)
    queries = list(
        dict.fromkeys(
            build_evidence_queries("A", context, base_query)
            + build_evidence_queries("P", context, base_query)
        )
    )
    evidence = retrieve_evidence(queries)
    signals = build_review_signals(context)
    review_context = {
        "clinical_context": context,
        "rule_signals": signals,
        "medical_evidence": evidence,
        "constraints": {
            "read_only": True,
            "may_confirm_diagnosis": False,
            "may_issue_prescription": False,
            "may_modify_record": False,
        },
    }
    llm = get_llm()
    model = getattr(llm, "model", current_app.config["LLM_MODEL"])
    run_id = new_id()
    started = time.monotonic()
    with transaction():
        execute(
            """
            INSERT INTO agent_review_runs
                (id, encounter_id, status, provider, model, prompt_version,
                 input_snapshot, evidence_json, created_by)
            VALUES (%s,%s,'running',%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                encounter_id,
                llm.provider,
                model,
                AGENT_PROMPT_VERSION,
                json.dumps(review_context, ensure_ascii=False, default=str),
                json.dumps([_present_evidence(item) for item in evidence], ensure_ascii=False, default=str),
                session.get("admin_id"),
            ),
        )
    try:
        result, usage = llm.generate_clinical_review(review_context)
        for key in REVIEW_LIST_KEYS:
            combined = list(dict.fromkeys([*(signals.get(key) or []), *(result.get(key) or [])]))
            result[key] = combined[:12]
    except AIServiceError as error:
        with transaction():
            execute(
                """
                UPDATE agent_review_runs
                SET status='failed', error_message=%s, latency_ms=%s, completed_at=%s
                WHERE id=%s
                """,
                (str(error)[:2000], int((time.monotonic() - started) * 1000), datetime.utcnow(), run_id),
            )
        return jsonify({"error": "agent_review_failed", "message": str(error)}), 502

    with transaction():
        execute(
            """
            UPDATE agent_review_runs
            SET status='completed', output_json=%s, latency_ms=%s,
                input_tokens=%s, output_tokens=%s, completed_at=%s
            WHERE id=%s
            """,
            (
                json.dumps(result, ensure_ascii=False),
                int((time.monotonic() - started) * 1000),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                datetime.utcnow(),
                run_id,
            ),
        )
        audit("agent.review", "encounter", encounter_id, {"run_id": run_id, "read_only": True})
    row = fetch_one("SELECT * FROM agent_review_runs WHERE id=%s", (run_id,))
    return jsonify({"review": present_run(row)})
