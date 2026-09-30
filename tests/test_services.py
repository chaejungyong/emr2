import io
import json
from contextlib import nullcontext

import pytest
from flask import Flask
from PIL import Image

from app.ai import MockLLM, OpenAICompatibleLLM, QdrantStore, api_url, parse_json
from app.auth import install_guards
from app.clinical import affected_soap_stages, detect_image, xray_context_signature
from app.indexing import normalize_text, split_long_text
from app.soap import build_context, stage_is_unlocked


def test_api_url_accepts_root_or_v1_base():
    assert api_url("https://example.test", "/v1/chat/completions") == (
        "https://example.test/v1/chat/completions"
    )
    assert api_url("https://example.test/v1", "/v1/chat/completions") == (
        "https://example.test/v1/chat/completions"
    )


def test_json_parser_accepts_fenced_response():
    result = parse_json('```json\n{"candidates":[{"text":"S"}]}\n```')
    assert result["candidates"][0]["text"] == "S"


def test_mock_llm_returns_exact_candidate_count():
    client = MockLLM()
    candidates, usage = client.generate_candidates(
        "S", {"encounter": {"chief_complaint": "기침"}}, 1
    )
    assert len(candidates) == 1
    assert all("기침" in candidate for candidate in candidates)
    assert usage["prompt_tokens"] == 0


def test_qdrant_active_collection_uses_alias_list(monkeypatch):
    store = QdrantStore("http://qdrant:6333", "kb_active")
    called = {}

    def fake_request(method, path, **_kwargs):
        called.update({"method": method, "path": path})
        return {
            "result": {
                "aliases": [
                    {"alias_name": "other", "collection_name": "old"},
                    {"alias_name": "kb_active", "collection_name": "current"},
                ]
            }
        }

    monkeypatch.setattr(store, "_request", fake_request)
    assert store.active_collection() == "current"
    assert called == {"method": "GET", "path": "/aliases"}


def test_openai_adapter_validates_candidate_count(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"candidates": [{"text": "첫째"}, {"text": "둘째"}]}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

    monkeypatch.setattr("app.ai.httpx.post", lambda *args, **kwargs: Response())
    client = OpenAICompatibleLLM("https://example.test", "key", "model", 5)
    candidates, usage = client.generate_candidates("A", {}, 2)
    assert candidates == ["첫째", "둘째"]
    assert usage["completion_tokens"] == 20


def test_openai_adapter_translates_evidence_to_korean(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "translations": [
                                        {"chunk_id": "chunk-1", "text_ko": "심장 비대"},
                                        {"chunk_id": "chunk-2", "text_ko": "폐부종"},
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }

    monkeypatch.setattr("app.ai.httpx.post", lambda *args, **kwargs: Response())
    client = OpenAICompatibleLLM("https://example.test", "key", "model", 5)
    translations, usage = client.translate_evidence(
        [
            {"chunk_id": "chunk-1", "excerpt": "cardiomegaly"},
            {"chunk_id": "chunk-2", "excerpt": "pulmonary edema"},
        ]
    )
    assert translations == {"chunk-1": "심장 비대", "chunk-2": "폐부종"}
    assert usage["completion_tokens"] == 8


def test_chunker_is_deterministic_and_preserves_text():
    source = ("첫 번째 문단입니다. " * 100) + "\n\n" + ("두 번째 문단입니다. " * 100)
    normalized = normalize_text(source)
    first = split_long_text(normalized, target=500, overlap=50)
    second = split_long_text(normalized, target=500, overlap=50)
    assert first == second
    assert len(first) > 2
    assert all(chunk.strip() for chunk in first)


def test_image_validator_accepts_png_and_rejects_fake_file():
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    mime, extension = detect_image(stream.getvalue())
    assert (mime, extension) == ("image/png", ".png")
    with pytest.raises(ValueError):
        detect_image(b"\x89PNG\r\n\x1a\nnot-an-image")


def test_encounter_fields_stale_only_dependent_soap_stages():
    assert affected_soap_stages({"physical_exam"}) == ("O", "A", "P")
    assert affected_soap_stages({"chief_complaint"}) == ("S", "O", "A", "P")
    assert affected_soap_stages({"history_text", "physical_exam"}) == (
        "S",
        "O",
        "A",
        "P",
    )
    assert affected_soap_stages({"visit_at"}) == ()


def test_xray_context_signature_ignores_images_without_readings():
    empty = {
        "taken_at": None,
        "body_region": "흉부",
        "reading_text": None,
    }
    interpreted = {
        "taken_at": None,
        "body_region": "흉부",
        "reading_text": "심장 음영 증가",
    }
    assert xray_context_signature(empty) is None
    assert xray_context_signature(interpreted) == (
        None,
        "흉부",
        "심장 음영 증가",
    )


def test_xray_readings_are_excluded_from_s_context(monkeypatch):
    encounter = {
        "id": "encounter-1",
        "patient_id": "patient-1",
        "visit_at": "2026-09-30T10:00:00",
        "chief_complaint": "기침",
        "history_text": "3일 전 시작",
        "physical_exam": "심잡음",
        "chart_number": "C-000001",
        "name": "초코",
        "species": "Canine",
        "breed": "Maltese",
        "sex": "male",
        "neutered": True,
        "birth_date": None,
        "weight_kg": 4.2,
        "disease_name": "심장질환",
        "disease_category": "심장질환",
        "disease_description": None,
    }
    xray_rows = [
        {"taken_at": None, "body_region": "흉부", "reading_text": "심장 음영 증가"}
    ]
    monkeypatch.setattr("app.soap.fetch_one", lambda *_args, **_kwargs: encounter)
    monkeypatch.setattr(
        "app.soap.fetch_all",
        lambda sql, *_args, **_kwargs: xray_rows if "FROM xray_assets" in sql else [],
    )
    sections = {
        stage: {"status": "confirmed", "current_text": stage}
        for stage in ("S", "O", "A", "P")
    }
    s_context, _ = build_context("encounter-1", "S", sections)
    o_context, _ = build_context("encounter-1", "O", sections)
    assert s_context["xray_readings"] == []
    assert o_context["xray_readings"] == xray_rows


def test_stage_order_requires_all_previous_sections_confirmed():
    sections = {
        "S": {"status": "confirmed"},
        "O": {"status": "confirmed"},
        "A": {"status": "pending"},
        "P": {"status": "pending"},
    }
    assert stage_is_unlocked("S", sections)
    assert stage_is_unlocked("A", sections)
    assert not stage_is_unlocked("P", sections)


def test_api_guard_requires_session_and_csrf():
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.get("/api/private")
    def private_get():
        return {"ok": True}

    @app.post("/api/private")
    def private_post():
        return {"ok": True}

    install_guards(app)
    client = app.test_client()
    assert client.get("/api/private").status_code == 401
    with client.session_transaction() as session:
        session["admin_id"] = "admin"
        session["csrf_token"] = "token"
    assert client.get("/api/private").status_code == 200
    assert client.post("/api/private").status_code == 403
    assert (
        client.post("/api/private", headers={"X-CSRF-Token": "token"}).status_code
        == 200
    )


def test_patient_create_endpoint_generates_sequential_chart_numbers(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    patients = []
    sequence = {"next_value": 1}

    def fake_execute(sql, params=()):
        if "UPDATE patient_chart_number_sequence" in sql:
            sequence["next_value"] += 1
        if len(params) == 12:
            patients.append(
                {
                    "id": params[0],
                    "chart_number": params[1],
                    "name": params[2],
                    "owner_name": params[3],
                    "owner_phone": params[4],
                    "species": params[5],
                }
            )

    def fake_fetch_one(sql, _params=()):
        if "FROM patient_chart_number_sequence" in sql:
            return dict(sequence)
        return patients[-1]

    patient_ids = iter(("patient-id-1", "patient-id-2"))
    monkeypatch.setattr(clinical, "new_id", lambda: next(patient_ids))
    monkeypatch.setattr(clinical, "execute", fake_execute)
    monkeypatch.setattr(clinical, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(clinical, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clinical, "transaction", nullcontext)

    first_response = app.test_client().post(
        "/api/patients",
        json={
            "chart_number": "MANUAL-NUMBER-IS-IGNORED",
            "name": "보리",
            "owner_name": "김보호",
            "owner_phone": "010-1234-5678",
            "species": "Canine",
            "sex": "female",
            "weight_kg": 7.2,
        },
    )
    second_response = app.test_client().post(
        "/api/patients",
        json={
            "name": "초코",
            "owner_name": "이보호",
            "owner_phone": "010-8765-4321",
            "species": "Feline",
        },
    )

    assert first_response.status_code == 201
    assert first_response.get_json()["name"] == "보리"
    assert second_response.status_code == 201
    assert [patient["chart_number"] for patient in patients] == [
        "C-000001",
        "C-000002",
    ]
    assert patients[0]["owner_phone"] == "010-1234-5678"


def test_patient_create_endpoint_rejects_invalid_owner_phone():
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)

    response = app.test_client().post(
        "/api/patients",
        json={
            "name": "토리",
            "owner_name": "박보호",
            "owner_phone": "010-123-45678",
            "species": "Canine",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_patient_create_endpoint_rejects_unsupported_species():
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)

    response = app.test_client().post(
        "/api/patients",
        json={
            "name": "토리",
            "owner_name": "박보호",
            "owner_phone": "010-1234-5678",
            "species": "Rabbit",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_encounter_create_requires_chief_complaint(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    monkeypatch.setattr(
        clinical,
        "fetch_one",
        lambda *_args, **_kwargs: {"id": "patient-id"},
    )

    response = app.test_client().post(
        "/api/encounters",
        json={"patient_id": "patient-id"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_next_patient_chart_number_endpoint_returns_preview(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    monkeypatch.setattr(
        clinical,
        "fetch_one",
        lambda *_args, **_kwargs: {"next_value": 42},
    )

    response = app.test_client().get("/api/patients/next-chart-number")

    assert response.status_code == 200
    assert response.get_json()["chart_number"] == "C-000042"


def test_patient_owner_contact_can_be_updated(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    patient = {
        "id": "patient-id",
        "owner_name": "이전 보호자",
        "owner_phone": "010-1111-2222",
    }

    def fake_execute(sql, params=()):
        if sql.startswith("UPDATE patients SET"):
            patient["owner_name"] = params[0]
            patient["owner_phone"] = params[1]

    monkeypatch.setattr(clinical, "fetch_one", lambda *_args, **_kwargs: patient)
    monkeypatch.setattr(clinical, "execute", fake_execute)
    monkeypatch.setattr(clinical, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clinical, "transaction", nullcontext)

    response = app.test_client().patch(
        "/api/patients/patient-id",
        json={
            "owner_name": "새 보호자",
            "owner_phone": "010-3333-4444",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["owner_name"] == "새 보호자"
    assert response.get_json()["owner_phone"] == "010-3333-4444"


def test_patient_chart_number_cannot_be_updated(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    monkeypatch.setattr(
        clinical,
        "fetch_one",
        lambda *_args, **_kwargs: {"id": "patient-id"},
    )

    response = app.test_client().patch(
        "/api/patients/patient-id",
        json={"chart_number": "C-999999"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "chart_number_immutable"
