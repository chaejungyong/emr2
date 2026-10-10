from .db import execute, fetch_all, fetch_one


WORKFLOW_STAGES = (
    "intake",
    "examination",
    "diagnostics",
    "documentation",
    "education",
    "checkout",
    "closed",
)


DIAGNOSTIC_RESULT_QUERIES = {
    "echo": """
        SELECT id FROM cardiac_exams WHERE encounter_id=%s AND (
            la_ao IS NOT NULL OR lvidd_cm IS NOT NULL OR lviddn IS NOT NULL
            OR lvids_cm IS NOT NULL OR fs_percent IS NOT NULL
            OR e_velocity_ms IS NOT NULL OR a_velocity_ms IS NOT NULL
            OR tr_vmax_ms IS NOT NULL OR pr_vmax_ms IS NOT NULL
            OR mr_severity IS NOT NULL OR sam=TRUE OR lvoto=TRUE
            OR sec_present=TRUE OR la_thrombus=TRUE
            OR pericardial_effusion=TRUE OR findings IS NOT NULL
            OR assessment IS NOT NULL OR acvim_stage IS NOT NULL
            OR ph_risk IS NOT NULL OR chf_status IS NOT NULL
        )
    """,
    "ecg": """
        SELECT id FROM ecg_exams WHERE encounter_id=%s AND (
            heart_rate_bpm IS NOT NULL OR rhythm IS NOT NULL
            OR pr_ms IS NOT NULL OR qrs_ms IS NOT NULL OR qt_ms IS NOT NULL
            OR interpretation IS NOT NULL
        )
    """,
    "xray": """
        SELECT id FROM xray_assets
        WHERE encounter_id=%s AND reading_text IS NOT NULL AND TRIM(reading_text) <> ''
        LIMIT 1
    """,
    "bp": """
        SELECT id FROM cardiac_exams
        WHERE encounter_id=%s AND systolic_bp_mmhg IS NOT NULL
    """,
    "lab": """
        SELECT id FROM lab_results WHERE encounter_id=%s AND (
            nt_probnp_pmol_l IS NOT NULL OR troponin_i_ng_ml IS NOT NULL
            OR bun_mg_dl IS NOT NULL OR creatinine_mg_dl IS NOT NULL
            OR sodium_mmol_l IS NOT NULL OR potassium_mmol_l IS NOT NULL
            OR notes IS NOT NULL
        )
    """,
}


def diagnostic_result_exists(encounter_id, exam_type):
    query = DIAGNOSTIC_RESULT_QUERIES.get(exam_type)
    if not query:
        raise ValueError("invalid_diagnostic_exam_type")
    return bool(fetch_one(query, (encounter_id,)))


def invalidate_diagnostic_review(encounter_id, exam_type):
    """Require veterinarian re-review whenever a diagnostic result changes."""

    status = "completed" if diagnostic_result_exists(encounter_id, exam_type) else "planned"
    execute(
        """
        UPDATE diagnostic_requirements
        SET status=%s, reason=NULL, reviewed_by=NULL, reviewed_at=NULL
        WHERE encounter_id=%s AND exam_type=%s
        """,
        (status, encounter_id, exam_type),
    )
    return status


def diagnostics_status(encounter_id):
    rows = fetch_all(
        "SELECT exam_type, status, reason FROM diagnostic_requirements WHERE encounter_id = %s ORDER BY exam_type",
        (encounter_id,),
    )
    unresolved = [
        row for row in rows if row["status"] not in {"reviewed", "not_required"}
    ]
    invalid_skips = [
        row
        for row in rows
        if row["status"] == "not_required" and not str(row.get("reason") or "").strip()
    ]
    return rows, unresolved, invalid_skips


def diagnostics_ready(encounter_id):
    rows, unresolved, invalid_skips = diagnostics_status(encounter_id)
    return bool(rows) and not unresolved and not invalid_skips


def encounter_is_closed(encounter_id):
    encounter = fetch_one(
        "SELECT workflow_stage FROM encounters WHERE id = %s", (encounter_id,)
    )
    return bool(encounter and encounter["workflow_stage"] == "closed")


def invalidate_workflow(encounter_id, earliest_stage):
    """Move an open encounter back and invalidate generated handouts.

    The caller owns the surrounding transaction. Closed encounters remain immutable
    until explicitly reopened through the workflow API.
    """

    if earliest_stage not in WORKFLOW_STAGES:
        raise ValueError("invalid_workflow_stage")
    encounter = fetch_one(
        "SELECT workflow_stage FROM encounters WHERE id = %s", (encounter_id,)
    )
    if not encounter or encounter["workflow_stage"] == "closed":
        return
    current_index = WORKFLOW_STAGES.index(encounter["workflow_stage"])
    target_index = WORKFLOW_STAGES.index(earliest_stage)
    if current_index > target_index:
        execute(
            "UPDATE encounters SET workflow_stage=%s, workflow_updated_at=CURRENT_TIMESTAMP(6) WHERE id=%s",
            (earliest_stage, encounter_id),
        )
    if target_index <= WORKFLOW_STAGES.index("documentation"):
        execute(
            "UPDATE client_education_documents SET status='superseded' WHERE encounter_id=%s AND status IN ('draft','delivered')",
            (encounter_id,),
        )
        execute(
            "UPDATE prescriptions SET review_required=TRUE WHERE encounter_id=%s AND status IN ('draft','issued')",
            (encounter_id,),
        )
