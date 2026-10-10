from decimal import Decimal
from contextlib import nullcontext
from datetime import date, datetime

from flask import Flask

from app import commercial
from app.commercial import _dose, present_workflow, workflow_blockers


def encounter(**overrides):
    value = {
        "id": "encounter-1",
        "workflow_stage": "intake",
        "owner_name": "김보호",
        "owner_phone": "010-1234-5678",
        "current_medications": "없음",
        "allergies": "없음",
        "chief_complaint": "기침",
        "history_text": "3일 전 시작",
        "physical_exam": "심잡음 III/VI",
        "closed_at": None,
    }
    value.update(overrides)
    return value


def test_intake_requires_explicit_medication_and_allergy_review():
    blockers = workflow_blockers(
        encounter(current_medications=None, allergies=None), "examination"
    )
    assert {item["code"] for item in blockers} == {
        "current_medications_required",
        "allergies_required",
    }


def test_examination_requires_physical_exam_and_four_vitals(monkeypatch):
    monkeypatch.setattr(
        commercial,
        "fetch_one",
        lambda *_args, **_kwargs: {
            "body_weight_kg": Decimal("4.2"),
            "heart_rate_bpm": 130,
            "respiratory_rate_rpm": None,
            "systolic_bp_mmhg": None,
        },
    )
    blockers = workflow_blockers(encounter(physical_exam=""), "diagnostics")
    assert {item["code"] for item in blockers} == {
        "physical_exam_required",
        "respiratory_rate_rpm_required",
        "systolic_bp_mmhg_required",
    }


def test_documentation_requires_a_resolved_diagnostic_plan(monkeypatch):
    monkeypatch.setattr(
        commercial,
        "diagnostics_status",
        lambda _encounter_id: (
            [{"exam_type": "echo", "status": "completed", "reason": None}],
            [{"exam_type": "echo", "status": "completed", "reason": None}],
            [],
        ),
    )
    blockers = workflow_blockers(encounter(), "documentation")
    assert blockers[0]["code"] == "diagnostic_echo_unresolved"


def test_full_flow_gate_conditions_clear_in_order(monkeypatch):
    monkeypatch.setattr(
        commercial,
        "fetch_one",
        lambda sql, _params=(): (
            {"body_weight_kg": 4.2, "heart_rate_bpm": 130, "respiratory_rate_rpm": 28, "systolic_bp_mmhg": 125}
            if "FROM cardiac_exams" in sql
            else {"status": "completed"}
            if "FROM soap_documents" in sql
            else None
        ),
    )
    monkeypatch.setattr(
        commercial,
        "diagnostics_status",
        lambda _encounter_id: ([{"exam_type": "echo", "status": "reviewed", "reason": None}], [], []),
    )
    monkeypatch.setattr(
        commercial,
        "_active_prescription",
        lambda _encounter_id: {
            "status": "issued",
            "review_required": False,
            "latest_revision": {"status": "issued"},
        },
    )
    monkeypatch.setattr(
        commercial,
        "_latest_education",
        lambda _encounter_id: {"status": "delivered"},
    )
    monkeypatch.setattr(
        commercial,
        "_invoice",
        lambda _encounter_id: {"status": "paid", "paid_amount": 100, "total_amount": 100},
    )

    value = encounter()
    for target in ("examination", "diagnostics", "documentation", "education", "checkout", "closed"):
        assert workflow_blockers(value, target) == []


def test_workflow_response_exposes_next_stage_and_blockers():
    value = present_workflow(encounter(workflow_stage="intake"))
    assert value["next_stage"] == "examination"
    assert value["blockers"] == []


def test_close_requires_invoice_reissue_after_clinical_workflow_changed(monkeypatch):
    monkeypatch.setattr(
        commercial,
        "_invoice",
        lambda _encounter_id: {
            "status": "paid",
            "paid_amount": 100,
            "total_amount": 100,
            "issued_at": datetime(2026, 10, 10, 9, 0),
        },
    )
    blockers = workflow_blockers(
        encounter(workflow_updated_at=datetime(2026, 10, 10, 10, 0)), "closed"
    )

    assert blockers[0]["code"] == "invoice_outdated"


def test_staff_can_advance_intake_and_checkout_but_not_clinical_stages():
    veterinarian_only = commercial.VETERINARIAN_ONLY_TRANSITION_TARGETS

    assert "examination" not in veterinarian_only
    assert "checkout" not in veterinarian_only
    assert "closed" not in veterinarian_only
    assert {"diagnostics", "documentation", "education"} == veterinarian_only


def test_prescription_dose_preserves_four_decimal_precision():
    assert _dose("0.0625") == Decimal("0.0625")


def test_legacy_issued_prescription_does_not_unlock_checkout(monkeypatch):
    monkeypatch.setattr(
        commercial,
        "_active_prescription",
        lambda _encounter_id: {
            "status": "issued",
            "review_required": False,
            "latest_revision": None,
        },
    )
    monkeypatch.setattr(
        commercial,
        "_latest_education",
        lambda _encounter_id: {"status": "delivered"},
    )

    blockers = workflow_blockers(encounter(), "checkout")

    assert blockers[0]["code"] == "prescription_unresolved"


def test_prescription_write_waits_for_education_stage(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(commercial.bp)
    monkeypatch.setattr(commercial, "_encounter", lambda _encounter_id: encounter(workflow_stage="documentation"))

    client = app.test_client()
    with client.session_transaction() as session:
        session["role"] = "veterinarian"
    response = client.put(
        "/api/encounters/encounter-1/structured-prescription",
        json={"status": "not_required", "not_required_reason": "불필요"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "workflow_stage_required"


def test_appointment_checkin_is_idempotent_when_already_linked(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(commercial.bp)

    def fake_fetch_one(sql, _params=()):
        if "FROM appointments" in sql:
            return {
                "id": "appointment-1",
                "patient_id": "patient-1",
                "encounter_id": "encounter-1",
                "status": "arrived",
            }
        if "FROM encounters" in sql:
            return {"id": "encounter-1", "patient_id": "patient-1"}
        return None

    monkeypatch.setattr(commercial, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(commercial, "transaction", nullcontext)
    response = app.test_client().post("/api/appointments/appointment-1/check-in")
    assert response.status_code == 200
    assert response.get_json()["created"] is False
    assert response.get_json()["encounter"]["id"] == "encounter-1"


def test_reopen_rejects_an_encounter_that_is_not_closed(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(commercial.bp)
    monkeypatch.setattr(commercial, "_encounter", lambda _encounter_id: encounter())

    client = app.test_client()
    with client.session_transaction() as session:
        session["role"] = "veterinarian"
    response = client.post(
        "/api/encounters/encounter-1/workflow/transition",
        json={"action": "reopen", "target_stage": "checkout"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "encounter_not_closed"


def test_closed_encounter_rejects_diagnostic_plan_changes(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(commercial.bp)
    monkeypatch.setattr(
        commercial,
        "_encounter",
        lambda _encounter_id: encounter(workflow_stage="closed"),
    )

    response = app.test_client().put(
        "/api/encounters/encounter-1/diagnostic-plan",
        json={"items": []},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "encounter_closed"


def test_reviewed_diagnostic_requires_a_recorded_result(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(commercial.bp)
    monkeypatch.setattr(commercial, "_encounter", lambda _encounter_id: encounter(workflow_stage="diagnostics"))
    monkeypatch.setattr(commercial, "diagnostics_status", lambda _encounter_id: ([], [], []))
    monkeypatch.setattr(commercial, "_diagnostic_result_exists", lambda *_args: False)

    client = app.test_client()
    with client.session_transaction() as session:
        session["role"] = "veterinarian"
    response = client.put(
        "/api/encounters/encounter-1/diagnostic-plan",
        json={"items": [{"exam_type": "echo", "status": "reviewed"}]},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "diagnostic_result_required"


def test_staff_cannot_replace_a_veterinarian_reviewed_plan(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(commercial.bp)
    monkeypatch.setattr(commercial, "_encounter", lambda _encounter_id: encounter(workflow_stage="diagnostics"))
    monkeypatch.setattr(
        commercial,
        "diagnostics_status",
        lambda _encounter_id: ([{"exam_type": "echo", "status": "reviewed", "reason": None}], [], []),
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["role"] = "staff"
    response = client.put(
        "/api/encounters/encounter-1/diagnostic-plan",
        json={"items": []},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "permission_denied"


def test_daily_close_rejects_unbilled_encounters(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(commercial.bp)
    selected = date.today().isoformat()
    summary = {
        "date": selected,
        "methods": {},
        "payments": 0,
        "refunds": 0,
        "net": 0,
        "billed_amount": 0,
        "billing_payment_difference": 0,
        "unbilled_count": 1,
        "outstanding_amount": 0,
        "outstanding_count": 0,
    }
    monkeypatch.setattr(commercial, "daily_summary", lambda _selected: summary)
    monkeypatch.setattr(commercial, "fetch_one", lambda *_args, **_kwargs: None)

    response = app.test_client().post(
        "/api/billing/daily-close", json={"date": selected}
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "daily_close_blocked"
