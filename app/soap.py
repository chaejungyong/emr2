import json
import time
from datetime import datetime

import httpx
from flask import Blueprint, current_app, jsonify, request

from .ai import (
    AIServiceError,
    PROMPT_VERSION,
    get_embedding_client,
    get_llm,
    get_vector_store,
)
from .db import audit, execute, fetch_all, fetch_one, new_id, transaction


bp = Blueprint("soap", __name__, url_prefix="/api")
STAGES = ("S", "O", "A", "P")


def normalize_stage(stage):
    value = stage.upper()
    return value if value in STAGES else None


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
               d.name AS disease_name, d.category AS disease_category,
               d.description AS disease_description
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN diseases d ON d.id = e.disease_id
        WHERE e.id = %s
        """,
        (encounter_id,),
    )
    xrays = fetch_all(
        """
        SELECT taken_at, body_region, reading_text
        FROM xray_assets
        WHERE encounter_id = %s AND reading_text IS NOT NULL AND reading_text <> ''
        ORDER BY created_at
        """,
        (encounter_id,),
    )
    prior_rows = fetch_all(
        """
        SELECT e.id AS encounter_id, e.visit_at, d.name AS disease_name,
               ss.stage, ss.current_text
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
    confirmed = {
        item: sections[item]["current_text"]
        for item in previous_stages(stage)
        if sections[item]["current_text"]
    }
    context = {
        "patient": {
            key: encounter[key]
            for key in (
                "chart_number",
                "name",
                "species",
                "breed",
                "sex",
                "neutered",
                "birth_date",
                "weight_kg",
            )
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
        "confirmed_soap": confirmed,
        "prior_confirmed_soap": prior_notes,
        "medical_evidence": [],
    }
    query_parts = [
        str(encounter.get("disease_name") or ""),
        str(encounter.get("chief_complaint") or ""),
        str(encounter.get("history_text") or ""),
        " ".join(str(value or "") for value in confirmed.values()),
        f"SOAP {stage}",
    ]
    return context, " ".join(part for part in query_parts if part).strip()


def retrieve_evidence(query):
    if not query:
        return []
    store = get_vector_store()
    try:
        if not store.active_collection():
            return []
        vector = get_embedding_client().embed([query])[0]
        results = store.search(vector, current_app.config["RAG_TOP_K"])
    except (AIServiceError, httpx.HTTPError, KeyError, ValueError) as error:
        current_app.logger.warning("Knowledge retrieval unavailable: %s", type(error).__name__)
        return []
    evidence = []
    for item in results:
        payload = item.get("payload") or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            continue
        evidence.append(
            {
                "chunk_id": str(payload.get("chunk_id") or item.get("id")),
                "document_name": payload.get("display_name") or payload.get("source_key") or "문서",
                "page_start": payload.get("page_start"),
                "page_end": payload.get("page_end"),
                "score": item.get("score"),
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
                    SELECT chunk_id, document_name, page_start, page_end, score, excerpt
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
    stage = normalize_stage(stage)
    if not stage:
        return jsonify({"error": "invalid_soap_stage"}), 400
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    sections = section_rows(document["id"])
    if not stage_is_unlocked(stage, sections):
        return jsonify({"error": "previous_stage_not_confirmed"}), 409

    context, query = build_context(encounter_id, stage, sections)
    evidence = retrieve_evidence(query)
    context["medical_evidence"] = evidence
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
        candidates, usage = llm.generate_candidates(
            stage, context, current_app.config["SOAP_CANDIDATE_COUNT"]
        )
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
                        (id, candidate_id, chunk_id, document_name, page_start,
                         page_end, score, excerpt)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        new_id(),
                        candidate_id,
                        source["chunk_id"],
                        source["document_name"],
                        source["page_start"],
                        source["page_end"],
                        source["score"],
                        source["excerpt"],
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
        audit("soap.generate", "soap_document", document["id"], {"stage": stage})
    return jsonify({"run_id": run_id, "stage": stage, "candidates": candidate_rows, "evidence": evidence})


@bp.put("/encounters/<encounter_id>/soap/<stage>/confirm")
def confirm_section(encounter_id, stage):
    stage = normalize_stage(stage)
    if not stage:
        return jsonify({"error": "invalid_soap_stage"}), 400
    document = ensure_document(encounter_id)
    if not document:
        return jsonify({"error": "encounter_not_found"}), 404
    sections = section_rows(document["id"])
    if not stage_is_unlocked(stage, sections):
        return jsonify({"error": "previous_stage_not_confirmed"}), 409
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content", "")).strip()
    candidate_id = payload.get("candidate_id") or None
    if not content:
        return jsonify({"error": "content_required"}), 400
    if len(content) > 60000:
        return jsonify({"error": "content_too_long"}), 400
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
                (id, section_id, revision_no, source_candidate_id, content)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (new_id(), section["id"], revision, candidate_id, content),
        )
        execute(
            """
            UPDATE soap_sections
            SET status = 'confirmed', current_text = %s, confirmed_at = %s
            WHERE id = %s
            """,
            (content, datetime.utcnow(), section["id"]),
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
