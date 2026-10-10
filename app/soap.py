import json
import re
import time
from datetime import datetime

import httpx
from flask import Blueprint, current_app, jsonify, request, session

from .ai import (
    AIServiceError,
    PROMPT_VERSION,
    get_embedding_client,
    get_llm,
    get_vector_store,
)
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction
from .workflow_state import encounter_is_closed, invalidate_workflow


bp = Blueprint("soap", __name__, url_prefix="/api")
STAGES = ("S", "O", "A", "P")
STAGE_RETRIEVAL_TERMS = {
    "S": "cardiac clinical signs history cough syncope exercise intolerance resting respiratory rate",
    "O": "cardiovascular physical examination echocardiography electrocardiography thoracic radiography measurements",
    "A": "cardiac differential diagnosis disease classification staging prognosis MMVD cardiomyopathy pulmonary hypertension",
    "P": "cardiac treatment plan medication safety monitoring follow-up adverse effects cardiorenal electrolytes",
}


def normalize_stage(stage):
    value = stage.upper()
    return value if value in STAGES else None


def validate_diagnosis_name(stage, value):
    if stage != "A":
        return None
    if not isinstance(value, str):
        raise ValueError("diagnosis_name_required")
    diagnosis_name = value.strip()
    if not diagnosis_name:
        raise ValueError("diagnosis_name_required")
    if len(diagnosis_name) > 255:
        raise ValueError("diagnosis_name_too_long")
    return diagnosis_name


def ensure_document(encounter_id):
    encounter = fetch_one("SELECT id FROM encounters WHERE id = %s", (encounter_id,))
    if not encounter:
        return None
    document = fetch_one(
        "SELECT * FROM soap_documents WHERE encounter_id = %s", (encounter_id,)
    )
    if document:
        return document
    document_id = new_id()
    with transaction():
        execute(
            "INSERT INTO soap_documents (id, encounter_id) VALUES (%s, %s)",
            (document_id, encounter_id),
        )
        for stage in STAGES:
            execute(
                """
                INSERT INTO soap_sections (id, soap_document_id, stage)
                VALUES (%s, %s, %s)
                """,
                (new_id(), document_id, stage),
            )
    return fetch_one("SELECT * FROM soap_documents WHERE id = %s", (document_id,))


def section_rows(document_id):
    rows = fetch_all(
        "SELECT * FROM soap_sections WHERE soap_document_id = %s",
        (document_id,),
    )
    return {row["stage"]: row for row in rows}


def previous_stages(stage):
    return STAGES[: STAGES.index(stage)]


def stage_is_unlocked(stage, sections):
    return all(sections[item]["status"] == "confirmed" for item in previous_stages(stage))


def build_context(encounter_id, stage, sections):
    encounter = fetch_one(
        """
        SELECT e.id, e.patient_id, e.visit_at, e.chief_complaint, e.history_text,
               e.physical_exam, p.chart_number, p.name, p.species, p.breed,
               p.sex, p.neutered, p.birth_date, p.weight_kg,
               p.current_medications, p.allergies, p.preventive_care,
               d.name AS disease_name, d.category AS disease_category,
               d.description AS disease_description
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN diseases d ON d.id = e.disease_id
        WHERE e.id = %s
        """,
        (encounter_id,),
    )
    xrays = []
    if stage != "S":
        xrays = fetch_all(
            """
            SELECT taken_at, body_region, reading_text
            FROM xray_assets
            WHERE encounter_id = %s AND reading_text IS NOT NULL AND reading_text <> ''
            ORDER BY created_at
            """,
            (encounter_id,),
        )
    cardiac_exam = None
    ecg_exam = None
    lab_result = None
    prior_cardiac_trends = []
    monitoring_logs = fetch_all(
        """
        SELECT measured_at, source, body_weight_kg, home_rr_rpm,
               systolic_bp_mmhg, heart_rate_bpm, cough, dyspnea, syncope,
               appetite, medication_adherence, adverse_effects, notes
        FROM cardiac_monitoring_logs
        WHERE patient_id=%s
        ORDER BY measured_at DESC
        LIMIT 20
        """,
        (encounter["patient_id"],),
    )
    diagnostic_plan = []
    if stage != "S":
        cardiac_rows = fetch_all(
            """
            SELECT body_weight_kg, heart_rate_bpm, respiratory_rate_rpm,
                   systolic_bp_mmhg, murmur_grade, rhythm, vhs, vlas,
                   home_rr_rpm, la_ao, lvidd_cm, lviddn, lvids_cm,
                   fs_percent, e_velocity_ms, a_velocity_ms, e_a_ratio,
                   tr_vmax_ms, pr_vmax_ms, mr_severity, sam, lvoto,
                   sec_present, la_thrombus, pericardial_effusion,
                   findings, assessment, acvim_stage, ph_risk, chf_status
            FROM cardiac_exams
            WHERE encounter_id = %s
            """,
            (encounter_id,),
        )
        cardiac_exam = cardiac_rows[0] if cardiac_rows else None
        ecg_rows = fetch_all(
            """
            SELECT recorded_at, heart_rate_bpm, rhythm, pr_ms, qrs_ms,
                   qt_ms, interpretation
            FROM ecg_exams WHERE encounter_id = %s
            """,
            (encounter_id,),
        )
        ecg_exam = ecg_rows[0] if ecg_rows else None
        lab_rows = fetch_all(
            """
            SELECT collected_at, nt_probnp_pmol_l, troponin_i_ng_ml,
                   bun_mg_dl, creatinine_mg_dl, sodium_mmol_l,
                   potassium_mmol_l, notes
            FROM lab_results WHERE encounter_id = %s
            """,
            (encounter_id,),
        )
        lab_result = lab_rows[0] if lab_rows else None
        diagnostic_plan = fetch_all(
            """
            SELECT exam_type, status, reason
            FROM diagnostic_requirements WHERE encounter_id = %s
            ORDER BY exam_type
            """,
            (encounter_id,),
        )
    if stage in {"A", "P"}:
        prior_cardiac_trends = fetch_all(
            """
            SELECT e.visit_at, ce.body_weight_kg, ce.heart_rate_bpm,
                   ce.systolic_bp_mmhg, ce.vhs, ce.vlas, ce.home_rr_rpm,
                   ce.la_ao, ce.lviddn, ce.e_velocity_ms, ce.tr_vmax_ms,
                   ce.acvim_stage
            FROM cardiac_exams ce
            JOIN encounters e ON e.id = ce.encounter_id
            WHERE e.patient_id = %s AND e.id <> %s AND e.visit_at < %s
            ORDER BY e.visit_at DESC
            LIMIT 5
            """,
            (encounter["patient_id"], encounter_id, encounter["visit_at"]),
        )
    prior_rows = fetch_all(
        """
        SELECT e.id AS encounter_id, e.visit_at, d.name AS disease_name,
               ss.stage, ss.current_text, ss.diagnosis_name
        FROM encounters e
        JOIN soap_documents sd ON sd.encounter_id = e.id
        JOIN soap_sections ss ON ss.soap_document_id = sd.id
                              AND ss.status = 'confirmed'
        LEFT JOIN diseases d ON d.id = e.disease_id
        WHERE e.patient_id = %s AND e.id <> %s AND e.visit_at < %s
        ORDER BY e.visit_at DESC, FIELD(ss.stage, 'S', 'O', 'A', 'P')
        """,
        (encounter["patient_id"], encounter_id, encounter["visit_at"]),
    )
    prior_notes = []
    for row in prior_rows:
        note = next(
            (item for item in prior_notes if item["encounter_id"] == row["encounter_id"]),
            None,
        )
        if not note:
            if len(prior_notes) >= 5:
                continue
            note = {
                "encounter_id": row["encounter_id"],
                "visit_at": row["visit_at"],
                "disease_name": row["disease_name"],
                "sections": {},
            }
            prior_notes.append(note)
        note["sections"][row["stage"]] = row["current_text"]
        if row["stage"] == "A" and row.get("diagnosis_name"):
            note["diagnosis_name"] = row["diagnosis_name"]
    confirmed = {
        item: sections[item]["current_text"]
        for item in previous_stages(stage)
        if sections[item]["current_text"]
    }
    confirmed_diagnosis_name = None
    if stage == "P" and sections["A"]["status"] == "confirmed":
        confirmed_diagnosis_name = sections["A"].get("diagnosis_name")
    context = {
        "patient": {
            key: encounter.get(key)
            for key in (
                "species",
                "breed",
                "sex",
                "neutered",
                "birth_date",
                "weight_kg",
            )
        },
        "medication_safety": {
            "current_medications": encounter.get("current_medications"),
            "allergies": encounter.get("allergies"),
            "preventive_care": encounter.get("preventive_care"),
        },
        "encounter": {
            key: encounter[key]
            for key in (
                "visit_at",
                "chief_complaint",
                "history_text",
                "physical_exam",
                "disease_name",
                "disease_category",
                "disease_description",
            )
        },
        "xray_readings": xrays,
        "cardiac_exam": cardiac_exam,
        "ecg_exam": ecg_exam,
        "lab_result": lab_result,
        "diagnostic_plan": diagnostic_plan,
        "prior_cardiac_trends": prior_cardiac_trends,
        "recent_cardiac_monitoring": monitoring_logs,
        "confirmed_soap": confirmed,
        "confirmed_diagnosis_name": confirmed_diagnosis_name,
        "prior_confirmed_soap": prior_notes,
        "medical_evidence": [],
    }
    query_parts = [
        str(encounter.get("disease_name") or ""),
        str(encounter.get("chief_complaint") or ""),
        str(encounter.get("history_text") or ""),
        str(encounter.get("current_medications") or ""),
        str(encounter.get("allergies") or ""),
        str(encounter.get("preventive_care") or ""),
        json.dumps(cardiac_exam or {}, ensure_ascii=False, default=str),
        json.dumps(ecg_exam or {}, ensure_ascii=False, default=str),
        json.dumps(lab_result or {}, ensure_ascii=False, default=str),
        json.dumps(monitoring_logs, ensure_ascii=False, default=str),
        " ".join(str(value or "") for value in confirmed.values()),
        str(confirmed_diagnosis_name or ""),
        f"SOAP {stage}",
    ]
    return context, " ".join(part for part in query_parts if part).strip()


def stage_prerequisite_error(encounter_id, stage):
    if stage == "S":
        return None
    encounter = fetch_one(
        "SELECT physical_exam FROM encounters WHERE id=%s", (encounter_id,)
    )
    if stage == "O" and not str((encounter or {}).get("physical_exam") or "").strip():
        return {
            "error": "physical_exam_required",
            "message": "O 작성 전에 신체검사/기초 소견을 입력하세요.",
        }
    if stage in {"A", "P"}:
        from .workflow_state import diagnostics_ready

        if not diagnostics_ready(encounter_id):
            return {
                "error": "diagnostics_not_reviewed",
                "message": "선택한 검사를 모두 검토하거나 미실시 사유를 기록하세요.",
            }
    return None


def build_evidence_queries(stage, context, base_query):
    patient = context.get("patient") or {}
    encounter = context.get("encounter") or {}
    species = {"Canine": "canine dog", "Feline": "feline cat"}.get(
        patient.get("species"), str(patient.get("species") or "")
    )
    disease = str(encounter.get("disease_name") or "cardiac disease")
    complaint = str(encounter.get("chief_complaint") or "")
    history = str(encounter.get("history_text") or "")
    diagnosis = str(context.get("confirmed_diagnosis_name") or "")
    stage_terms = STAGE_RETRIEVAL_TERMS[stage]
    objective = " ".join(
        json.dumps(context.get(key) or {}, ensure_ascii=False, default=str)
        for key in ("cardiac_exam", "ecg_exam", "lab_result")
        if context.get(key)
    )
    confirmed = " ".join(
        str(value or "") for value in (context.get("confirmed_soap") or {}).values()
    )
    queries = [
        f"{species} {disease} {stage_terms} {complaint} {history}",
        f"Ettinger Textbook of Veterinary Internal Medicine {species} {disease} {stage_terms} {diagnosis}",
        f"{species} {disease} {objective} {confirmed} SOAP {stage}",
    ]
    if stage in {"A", "P"} and re.search(
        r"dyspnea|respiratory distress|pulmonary edema|chf|호흡곤란|폐부종|실신",
        f"{base_query} {objective}",
        re.I,
    ):
        queries.append(
            f"Small Animal Critical Care Medicine {species} {disease} emergency stabilization monitoring"
        )
    unique = []
    for query in queries:
        normalized = re.sub(r"\s+", " ", query).strip()
        if normalized and normalized not in unique:
            unique.append(normalized[:12000])
    return unique


def rank_evidence_results(result_sets, limit, max_per_document):
    merged = {}
    for results in result_sets:
        seen_in_query = set()
        for item in results:
            payload = item.get("payload") or {}
            chunk_id = str(payload.get("chunk_id") or item.get("id") or "")
            if not chunk_id:
                continue
            score = float(item.get("score") or 0)
            current = merged.get(chunk_id)
            if not current or score > current["vector_score"]:
                merged[chunk_id] = {
                    "item": item,
                    "vector_score": score,
                    "query_hits": (current or {}).get("query_hits", 0),
                }
            if chunk_id not in seen_in_query:
                merged[chunk_id]["query_hits"] += 1
                seen_in_query.add(chunk_id)
    ranked = []
    for value in merged.values():
        payload = value["item"].get("payload") or {}
        source_bonus = (
            0.015 if payload.get("source_type") == "internal_medicine_textbook" else 0
        )
        value["rank_score"] = (
            value["vector_score"]
            + min(0.05, 0.015 * max(0, value["query_hits"] - 1))
            + source_bonus
        )
        ranked.append(value)
    ranked.sort(
        key=lambda value: (
            value["rank_score"],
            value["vector_score"],
            str(value["item"].get("id") or ""),
        ),
        reverse=True,
    )
    selected, deferred, counts = [], [], {}
    for value in ranked:
        payload = value["item"].get("payload") or {}
        document_id = str(
            payload.get("document_id")
            or payload.get("source_key")
            or payload.get("display_name")
            or "unknown"
        )
        if counts.get(document_id, 0) >= max_per_document:
            deferred.append(value)
            continue
        selected.append(value)
        counts[document_id] = counts.get(document_id, 0) + 1
        if len(selected) == limit:
            return selected
    for value in deferred:
        selected.append(value)
        if len(selected) == limit:
            break
    return selected


def retrieve_evidence(queries):
    if isinstance(queries, str):
        queries = [queries]
    queries = [query for query in queries if query]
    if not queries:
        return []
    store = get_vector_store()
    try:
        if not store.active_collection():
            return []
        vectors = get_embedding_client().embed(queries)
        result_sets = [
            store.search(vector, current_app.config["RAG_CANDIDATE_POOL"])
            for vector in vectors
        ]
        results = rank_evidence_results(
            result_sets,
            current_app.config["RAG_TOP_K"],
            current_app.config["RAG_MAX_PER_DOCUMENT"],
        )
    except (AIServiceError, httpx.HTTPError, KeyError, ValueError) as error:
        current_app.logger.warning("Knowledge retrieval unavailable: %s", type(error).__name__)
        return []
    evidence = []
    for ranked in results:
        item = ranked["item"]
        payload = item.get("payload") or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        evidence.append(
            {
                "chunk_id": str(payload.get("chunk_id") or item.get("id")),
                "document_name": payload.get("canonical_title") or payload.get("display_name") or payload.get("source_key") or "문서",
                "source_type": payload.get("source_type"),
                "source_heading": payload.get("heading"),
                "source_edition": payload.get("edition"),
                "source_volume": payload.get("volume_label"),
                "page_start": payload.get("page_start"),
                "page_end": payload.get("page_end"),
                "score": ranked["rank_score"],
                "excerpt": text[:2000],
            }
        )
    return evidence


def present_document(document):
    sections = section_rows(document["id"])
    result = []
    for stage in STAGES:
        section = sections[stage]
        latest_run = fetch_one(
            """
            SELECT id, stage, status, provider, model, prompt_version,
                   kb_collection, latency_ms, input_tokens, output_tokens,
                   created_at, completed_at
            FROM ai_generation_runs
            WHERE soap_document_id = %s AND stage = %s AND status = 'completed'
            ORDER BY created_at DESC LIMIT 1
            """,
            (document["id"], stage),
        )
        candidates = []
        if latest_run:
            candidates = fetch_all(
                """
                SELECT id, rank_no, content, is_selected, created_at
                FROM ai_candidates
                WHERE run_id = %s
                ORDER BY rank_no
                """,
                (latest_run["id"],),
            )
            for candidate in candidates:
                candidate["evidence"] = fetch_all(
                    """
                    SELECT chunk_id, document_name, source_type, source_heading,
                           source_edition, source_volume, page_start, page_end,
                           score, excerpt, excerpt_ko
                    FROM candidate_evidence
                    WHERE candidate_id = %s
                    ORDER BY score DESC
                    """,
                    (candidate["id"],),
                )
        result.append(
            {
                **section,
                "unlocked": stage_is_unlocked(stage, sections),
                "candidates": candidates,
                "latest_run": latest_run,
            }
        )
    return {
        "id": document["id"],
        "encounter_id": document["encounter_id"],
        "status": document["status"],
        "sections": result,
    }


@bp.get("/encounters/<encounter_id>/soap")
def get_soap(encounter_id):
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    return jsonify(present_document(document))


@bp.post("/encounters/<encounter_id>/soap/<stage>/candidates")
def generate_candidates(encounter_id, stage):
    if session.get("admin_id") and session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    if encounter_is_closed(encounter_id):
        return jsonify(
            {
                "error": "encounter_closed",
                "message": "종료된 진료입니다. 진료를 다시 연 뒤 SOAP를 수정하세요.",
            }
        ), 409
    stage = normalize_stage(stage)
    if not stage:
        return jsonify({"error": "invalid_soap_stage"}), 400
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    sections = section_rows(document["id"])
    if not stage_is_unlocked(stage, sections):
        return jsonify({"error": "previous_stage_not_confirmed"}), 409
    prerequisite = stage_prerequisite_error(encounter_id, stage)
    if prerequisite:
        return jsonify(prerequisite), 409

    context, query = build_context(encounter_id, stage, sections)
    evidence_queries = build_evidence_queries(stage, context, query)
    evidence = retrieve_evidence(evidence_queries)
    context["medical_evidence"] = evidence
    context["evidence_search_strategy"] = {
        "queries": evidence_queries,
        "selection": "multi-query diversified reranking",
    }
    llm = get_llm()
    model = getattr(llm, "model", current_app.config["LLM_MODEL"])
    run_id = new_id()
    started = time.monotonic()
    with transaction():
        execute(
            """
            INSERT INTO ai_generation_runs
                (id, soap_document_id, stage, provider, model, prompt_version,
                 kb_collection, input_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                document["id"],
                stage,
                llm.provider,
                model,
                PROMPT_VERSION,
                current_app.config["QDRANT_ALIAS"],
                json.dumps(context, ensure_ascii=False, default=str),
            ),
        )
    try:
        candidates, generation_usage = llm.generate_candidates(
            stage, context, current_app.config["SOAP_CANDIDATE_COUNT"]
        )
        translations, translation_usage = llm.translate_evidence(evidence)
        for source in evidence:
            source["excerpt_ko"] = translations.get(source["chunk_id"])
        usage = {
            key: (generation_usage.get(key) or 0) + (translation_usage.get(key) or 0)
            for key in ("prompt_tokens", "completion_tokens")
        }
    except AIServiceError as error:
        with transaction():
            execute(
                """
                UPDATE ai_generation_runs
                SET status = 'failed', error_code = 'provider_error',
                    error_message = %s, latency_ms = %s, completed_at = %s
                WHERE id = %s
                """,
                (
                    str(error)[:2000],
                    int((time.monotonic() - started) * 1000),
                    datetime.utcnow(),
                    run_id,
                ),
            )
        return jsonify({"error": "ai_generation_failed", "message": str(error)}), 502

    with transaction():
        candidate_rows = []
        for rank, content in enumerate(candidates, 1):
            candidate_id = new_id()
            execute(
                """
                INSERT INTO ai_candidates (id, run_id, rank_no, content)
                VALUES (%s, %s, %s, %s)
                """,
                (candidate_id, run_id, rank, content),
            )
            for source in evidence:
                execute(
                    """
                    INSERT INTO candidate_evidence
                        (id, candidate_id, chunk_id, document_name, source_type,
                         source_heading, source_edition, source_volume,
                         page_start, page_end, score, excerpt, excerpt_ko)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_id(),
                        candidate_id,
                        source["chunk_id"],
                        source["document_name"],
                        source["source_type"],
                        source["source_heading"],
                        source["source_edition"],
                        source["source_volume"],
                        source["page_start"],
                        source["page_end"],
                        source["score"],
                        source["excerpt"],
                        source["excerpt_ko"],
                    ),
                )
            candidate_rows.append({"id": candidate_id, "rank_no": rank, "content": content})
        execute(
            """
            UPDATE ai_generation_runs
            SET status = 'completed', latency_ms = %s, input_tokens = %s,
                output_tokens = %s, completed_at = %s
            WHERE id = %s
            """,
            (
                int((time.monotonic() - started) * 1000),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                datetime.utcnow(),
                run_id,
            ),
        )
        execute(
            "UPDATE soap_sections SET status = 'generated' WHERE id = %s",
            (sections[stage]["id"],),
        )
        later = STAGES[STAGES.index(stage) + 1 :]
        if later:
            placeholders = ", ".join(["%s"] * len(later))
            execute(
                f"""
                UPDATE soap_sections
                SET status = CASE
                        WHEN current_text IS NULL THEN 'pending'
                        ELSE 'stale'
                    END
                WHERE soap_document_id = %s AND stage IN ({placeholders})
                """,
                [document["id"], *later],
            )
        execute(
            "UPDATE soap_documents SET status = 'draft' WHERE id = %s",
            (document["id"],),
        )
        execute(
            "UPDATE encounters SET status = 'draft' WHERE id = %s",
            (encounter_id,),
        )
        invalidate_workflow(encounter_id, "documentation")
        audit("soap.generate", "soap_document", document["id"], {"stage": stage})
    return jsonify({"run_id": run_id, "stage": stage, "candidates": candidate_rows, "evidence": evidence})


@bp.put("/encounters/<encounter_id>/soap/<stage>/confirm")
def confirm_section(encounter_id, stage):
    if session.get("admin_id") and session.get("role") != "veterinarian":
        return jsonify({"error": "permission_denied"}), 403
    if encounter_is_closed(encounter_id):
        return jsonify(
            {
                "error": "encounter_closed",
                "message": "종료된 진료입니다. 진료를 다시 연 뒤 SOAP를 수정하세요.",
            }
        ), 409
    stage = normalize_stage(stage)
    if not stage:
        return jsonify({"error": "invalid_soap_stage"}), 400
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    sections = section_rows(document["id"])
    if not stage_is_unlocked(stage, sections):
        return jsonify({"error": "previous_stage_not_confirmed"}), 409
    prerequisite = stage_prerequisite_error(encounter_id, stage)
    if prerequisite:
        return jsonify(prerequisite), 409
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content", "")).strip()
    candidate_id = payload.get("candidate_id") or None
    if not content:
        return jsonify({"error": "content_required"}), 400
    if len(content) > 60000:
        return jsonify({"error": "content_too_long"}), 400
    try:
        diagnosis_name = validate_diagnosis_name(stage, payload.get("diagnosis_name"))
    except ValueError as error:
        message = (
            "진단명은 255자 이내로 입력하세요."
            if str(error) == "diagnosis_name_too_long"
            else "진단명을 입력하세요."
        )
        return jsonify({"error": str(error), "message": message}), 400
    if candidate_id:
        candidate = fetch_one(
            """
            SELECT c.id
            FROM ai_candidates c
            JOIN ai_generation_runs r ON r.id = c.run_id
            WHERE c.id = %s AND r.soap_document_id = %s AND r.stage = %s
            """,
            (candidate_id, document["id"], stage),
        )
        if not candidate:
            return jsonify({"error": "candidate_not_found"}), 404

    section = sections[stage]
    revision = fetch_one(
        """
        SELECT COALESCE(MAX(revision_no), 0) + 1 AS next_revision
        FROM soap_section_revisions WHERE section_id = %s
        """,
        (section["id"],),
    )["next_revision"]
    later = STAGES[STAGES.index(stage) + 1 :]
    with transaction():
        if candidate_id:
            execute(
                """
                UPDATE ai_candidates c
                JOIN ai_generation_runs r ON r.id = c.run_id
                SET c.is_selected = (c.id = %s)
                WHERE r.soap_document_id = %s AND r.stage = %s
                """,
                (candidate_id, document["id"], stage),
            )
        execute(
            """
            INSERT INTO soap_section_revisions
                (id, section_id, revision_no, source_candidate_id, content,
                 diagnosis_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                new_id(),
                section["id"],
                revision,
                candidate_id,
                content,
                diagnosis_name,
            ),
        )
        execute(
            """
            UPDATE soap_sections
            SET status = 'confirmed', current_text = %s, diagnosis_name = %s,
                confirmed_at = %s
            WHERE id = %s
            """,
            (content, diagnosis_name, datetime.utcnow(), section["id"]),
        )
        if later:
            placeholders = ", ".join(["%s"] * len(later))
            execute(
                f"""
                UPDATE soap_sections
                SET status = CASE
                        WHEN current_text IS NULL THEN 'pending'
                        ELSE 'stale'
                    END
                WHERE soap_document_id = %s AND stage IN ({placeholders})
                """,
                [document["id"], *later],
            )
        invalidate_workflow(encounter_id, "documentation")
        new_sections = section_rows(document["id"])
        completed = all(new_sections[item]["status"] == "confirmed" for item in STAGES)
        execute(
            "UPDATE soap_documents SET status = %s WHERE id = %s",
            ("completed" if completed else "draft", document["id"]),
        )
        execute(
            "UPDATE encounters SET status = %s WHERE id = %s",
            ("completed" if completed else "draft", encounter_id),
        )
        audit(
            "soap.confirm",
            "soap_document",
            document["id"],
            {"stage": stage, "revision": revision},
        )
    document = fetch_one("SELECT * FROM soap_documents WHERE id = %s", (document["id"],))
    return jsonify(present_document(document))
