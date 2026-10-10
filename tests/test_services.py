import io
import json
from contextlib import nullcontext

import pytest
from flask import Flask
from PIL import Image

from app.cardio import clean_cardiac_payload, exam_comparison
from app.ai import (
    AIServiceError,
    EmbeddingClient,
    MockLLM,
    OpenAICompatibleLLM,
    QdrantStore,
    api_url,
    parse_json,
)
from app.auth import install_guards
from app.clinical import affected_soap_stages, detect_image, xray_context_signature
from app.indexing import (
    IndexingError,
    create_version,
    detect_page_heading,
    embed_chunks,
    embedding_text,
    extract_pdf,
    normalize_text,
    source_profile,
    split_long_text,
    update_item_progress,
)
from app.soap import (
    build_context,
    build_evidence_queries,
    rank_evidence_results,
    stage_is_unlocked,
    validate_diagnosis_name,
)


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


def test_qdrant_upsert_reports_batch_progress(monkeypatch):
    store = QdrantStore(
        "http://qdrant:6333",
        "kb_active",
        upsert_batch_size=2,
    )
    requests = []
    progress = []
    monkeypatch.setattr(
        store,
        "_request",
        lambda method, path, **kwargs: requests.append((method, path, kwargs)),
    )

    store.upsert(
        "collection",
        [{"id": str(index)} for index in range(5)],
        lambda current, total: progress.append((current, total)),
    )

    assert len(requests) == 3
    assert progress == [(2, 5), (4, 5), (5, 5)]


def test_pdf_extraction_reports_progress_for_pages_without_text(
    monkeypatch, tmp_path
):
    class EmptyPage:
        @staticmethod
        def extract_text():
            return ""

    class Reader:
        pages = [EmptyPage(), EmptyPage(), EmptyPage()]
        is_encrypted = False

    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    monkeypatch.setattr("app.indexing.PdfReader", lambda *_args, **_kwargs: Reader())
    progress = []
    app = Flask(__name__)
    app.config["MAX_PDF_BYTES"] = 1024
    app.config["MAX_PDF_PAGES"] = 10

    with app.app_context(), pytest.raises(IndexingError, match="추출 가능한 텍스트"):
        extract_pdf(
            pdf_path,
            lambda current, total: progress.append((current, total)),
        )

    assert progress == [(0, 3), (3, 3)]


def test_embedding_reports_batch_progress(monkeypatch):
    class EmbeddingStub:
        @staticmethod
        def embed(texts):
            return [[float(len(text))] for text in texts]

    monkeypatch.setattr("app.indexing.get_embedding_client", EmbeddingStub)
    progress = []
    app = Flask(__name__)
    app.config["EMBEDDING_BATCH_SIZE"] = 2

    with app.app_context():
        vectors = embed_chunks(
            [{"content": f"chunk {index}"} for index in range(5)],
            lambda current, total: progress.append((current, total)),
        )

    assert len(vectors) == 5
    assert progress == [(2, 5), (4, 5), (5, 5)]


def test_ettinger_source_profile_and_embedding_keep_book_context():
    profile = source_profile("2.Textbook of Veterinary Internal Medicine Ettinger 8th.pdf")
    assert profile == {
        "source_type": "internal_medicine_textbook",
        "canonical_title": "Ettinger Textbook of Veterinary Internal Medicine",
        "edition": "8th",
        "volume_label": "Volume 2",
    }
    text = embedding_text(
        {
            **profile,
            "heading": "Chapter 42 Cardiac Disease",
            "content": "Mitral regurgitation assessment.",
        }
    )
    assert "Ettinger Textbook" in text
    assert "Chapter 42 Cardiac Disease" in text
    assert text.endswith("Mitral regurgitation assessment.")


def test_page_heading_prefers_chapter_and_carries_previous_heading():
    assert detect_page_heading("12\nCHAPTER 42 Cardiac Disease\nBody text") == (
        "CHAPTER 42 Cardiac Disease"
    )
    assert detect_page_heading("123\nbody text only", "CHAPTER 42 Cardiac Disease") == (
        "CHAPTER 42 Cardiac Disease"
    )


def test_evidence_queries_route_ettinger_and_critical_care_by_stage():
    context = {
        "patient": {"species": "Canine"},
        "encounter": {
            "disease_name": "MMVD",
            "chief_complaint": "호흡곤란",
            "history_text": "야간 호흡수 증가",
        },
        "cardiac_exam": {"chf_status": "present"},
        "confirmed_soap": {"O": "폐부종 의심"},
    }
    queries = build_evidence_queries("A", context, "호흡곤란 CHF")
    assert any("Ettinger Textbook" in query for query in queries)
    assert any("Critical Care Medicine" in query for query in queries)
    assert all("Canine" not in query or "canine dog" in query for query in queries)


def test_evidence_ranking_merges_queries_and_limits_one_document():
    def result(chunk_id, document_id, score, source_type="medical_reference"):
        return {
            "id": chunk_id,
            "score": score,
            "payload": {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "source_type": source_type,
            },
        }

    ranked = rank_evidence_results(
        [
            [
                result("e1", "ettinger", 0.80, "internal_medicine_textbook"),
                result("e2", "ettinger", 0.79, "internal_medicine_textbook"),
                result("c1", "critical", 0.78, "critical_care_textbook"),
            ],
            [
                result("e1", "ettinger", 0.81, "internal_medicine_textbook"),
                result("g1", "guideline", 0.77),
            ],
        ],
        limit=3,
        max_per_document=1,
    )
    selected_ids = [item["item"]["payload"]["chunk_id"] for item in ranked]
    assert selected_ids[0] == "e1"
    assert set(selected_ids) == {"e1", "c1", "g1"}


def test_chunk_storage_reports_batched_progress(monkeypatch):
    generated_ids = iter(f"id-{index}" for index in range(300))
    monkeypatch.setattr("app.indexing.new_id", lambda: next(generated_ids))
    monkeypatch.setattr("app.indexing.execute", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.indexing.transaction", nullcontext)
    chunks = [
        {
            "page_start": 1,
            "page_end": 1,
            "heading": None,
            "content": f"chunk {index}",
        }
        for index in range(205)
    ]
    progress = []
    app = Flask(__name__)
    app.config.update(EMBEDDING_MODEL="model", CHUNKER_VERSION="chunker")

    with app.app_context():
        version_id, rows = create_version(
            {"id": "document-1"},
            "digest",
            123,
            1,
            chunks,
            lambda current, total: progress.append((current, total)),
        )

    assert version_id == "id-0"
    assert len(rows) == 205
    assert progress == [(0, 205), (100, 205), (200, 205), (205, 205)]


def test_item_progress_is_committed_on_a_short_connection(monkeypatch):
    executed = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def execute(sql, params):
            executed.update(sql=sql, params=params)

    class Connection:
        committed = False
        rolled_back = False
        closed = False

        @staticmethod
        def cursor():
            return Cursor()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr("app.indexing.connect", lambda: connection)

    update_item_progress("job-1", "inbox/book.pdf", "extracting", 20, 100)

    assert executed["params"] == (
        "extracting",
        20,
        100,
        "job-1",
        "inbox/book.pdf",
    )
    assert connection.committed
    assert not connection.rolled_back
    assert connection.closed


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


def test_embedding_client_explains_invalid_api_key_without_retry(monkeypatch):
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        return __import__("httpx").Response(
            401,
            request=__import__("httpx").Request("POST", "https://example.test"),
        )

    monkeypatch.setattr("app.ai.httpx.post", fake_post)
    client = EmbeddingClient("https://example.test", "bad-key", "model")
    with pytest.raises(AIServiceError, match="EMBEDDING_API_KEY"):
        client.embed(["text"])
    assert len(calls) == 1


def test_embedding_client_retries_rate_limit(monkeypatch):
    import httpx

    responses = [
        httpx.Response(
            429,
            headers={"retry-after": "0"},
            request=httpx.Request("POST", "https://example.test"),
        ),
        httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]},
            request=httpx.Request("POST", "https://example.test"),
        ),
    ]
    delays = []
    monkeypatch.setattr("app.ai.httpx.post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("app.ai.time.sleep", delays.append)
    client = EmbeddingClient("https://example.test", "key", "model")
    assert client.embed(["text"]) == [[0.25, 0.75]]
    assert delays == [0.0]


def test_embedding_client_retries_server_error(monkeypatch):
    import httpx

    responses = [
        httpx.Response(
            503,
            request=httpx.Request("POST", "https://example.test"),
        ),
        httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]},
            request=httpx.Request("POST", "https://example.test"),
        ),
    ]
    delays = []
    monkeypatch.setattr("app.ai.httpx.post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("app.ai.time.sleep", delays.append)
    client = EmbeddingClient(
        "https://example.test",
        "key",
        "model",
        max_attempts=2,
        backoff_seconds=0.5,
    )
    assert client.embed(["text"]) == [[0.25, 0.75]]
    assert delays == [0.5]


def test_embedding_client_retries_connection_error(monkeypatch):
    import httpx

    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise httpx.ConnectError(
                "temporary connection failure",
                request=httpx.Request("POST", "https://example.test"),
            )
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]},
            request=httpx.Request("POST", "https://example.test"),
        )

    delays = []
    monkeypatch.setattr("app.ai.httpx.post", fake_post)
    monkeypatch.setattr("app.ai.time.sleep", delays.append)
    client = EmbeddingClient(
        "https://example.test",
        "key",
        "model",
        max_attempts=2,
        backoff_seconds=0.5,
    )
    assert client.embed(["text"]) == [[0.25, 0.75]]
    assert len(calls) == 2
    assert delays == [0.5]


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


def test_cardiac_payload_validates_structured_values():
    cleaned = clean_cardiac_payload(
        {
            "cough": True,
            "la_ao": "1.78",
            "heart_rate_bpm": "126",
            "acvim_stage": "B2",
        }
    )

    assert cleaned["cough"] is True
    assert float(cleaned["la_ao"]) == 1.78
    assert cleaned["heart_rate_bpm"] == 126
    assert cleaned["acvim_stage"] == "B2"

    with pytest.raises(ValueError, match="number_out_of_range"):
        clean_cardiac_payload({"heart_rate_bpm": 900})


def test_cardiac_comparison_returns_previous_deltas(monkeypatch):
    from app import cardio

    encounter = {
        "id": "encounter-current",
        "patient_id": "patient-1",
        "visit_at": "2026-10-06T10:00:00",
    }
    current = {
        "encounter_id": "encounter-current",
        "la_ao": 1.78,
        "vhs": 11.5,
        "cough": 1,
    }
    previous = {
        "encounter_id": "encounter-old",
        "visit_at": "2026-03-11T10:00:00",
        "la_ao": 1.63,
        "vhs": 11.0,
        "cough": 0,
    }

    def fake_fetch_one(sql, _params=()):
        if "FROM encounters WHERE" in sql:
            return encounter
        if "WHERE encounter_id" in sql:
            return current
        if "e.visit_at <" in sql:
            return previous
        return None

    monkeypatch.setattr(cardio, "fetch_one", fake_fetch_one)

    comparison = exam_comparison("encounter-current")

    assert comparison["exam"]["cough"] is True
    assert comparison["previous"]["cough"] is False
    assert comparison["deltas"]["la_ao"] == 0.15
    assert comparison["deltas"]["vhs"] == 0.5


def test_cardiac_exam_save_marks_o_and_later_stale(monkeypatch):
    from app import cardio

    app = Flask(__name__)
    app.register_blueprint(cardio.bp)
    stale_calls = []
    diagnostic_review_calls = []

    def fake_fetch_one(sql, _params=()):
        if "SELECT id, patient_id, visit_at, workflow_stage FROM encounters" in sql:
            return {
                "id": "encounter-1",
                "patient_id": "patient-1",
                "visit_at": "2026-10-06T10:00:00",
                "workflow_stage": "diagnostics",
            }
        if "FROM cardiac_exams" in sql:
            return None
        return None

    monkeypatch.setattr(cardio, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(cardio, "execute", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cardio, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cardio, "transaction", nullcontext)
    monkeypatch.setattr(cardio, "new_id", lambda: "exam-1")
    monkeypatch.setattr(
        cardio,
        "mark_soap_stages_stale",
        lambda encounter_id, stages: stale_calls.append((encounter_id, stages)),
    )
    monkeypatch.setattr(
        cardio,
        "invalidate_diagnostic_review",
        lambda encounter_id, exam_type: diagnostic_review_calls.append(
            (encounter_id, exam_type)
        ),
    )
    monkeypatch.setattr(
        cardio,
        "exam_comparison",
        lambda encounter_id: {"encounter_id": encounter_id, "exam": {}, "previous": None, "deltas": {}},
    )

    response = app.test_client().put(
        "/api/encounters/encounter-1/cardiac-exam",
        json={"la_ao": 1.78, "lviddn": 1.62},
    )

    assert response.status_code == 200
    assert stale_calls == [("encounter-1", ("O", "A", "P"))]
    assert diagnostic_review_calls == [("encounter-1", "echo")]


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
        "current_medications": "피모벤단",
        "allergies": "없음",
        "preventive_care": "심장사상충 예방",
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
    sections["A"]["diagnosis_name"] = "승모판 폐쇄부전증"
    s_context, _ = build_context("encounter-1", "S", sections)
    o_context, _ = build_context("encounter-1", "O", sections)
    p_context, p_query = build_context("encounter-1", "P", sections)
    assert s_context["xray_readings"] == []
    assert "name" not in s_context["patient"]
    assert "chart_number" not in s_context["patient"]
    assert p_context["medication_safety"]["current_medications"] == "피모벤단"
    assert p_context["medication_safety"]["allergies"] == "없음"
    assert o_context["xray_readings"] == xray_rows
    assert p_context["confirmed_diagnosis_name"] == "승모판 폐쇄부전증"
    assert "승모판 폐쇄부전증" in p_query


def test_xray_reading_change_requires_veterinarian_rereview(monkeypatch):
    from app import clinical, workflow_state

    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(clinical.bp)
    review_calls = []
    stored = {
        "id": "xray-1",
        "encounter_id": "encounter-1",
        "original_name": "thorax.png",
        "mime_type": "image/png",
        "size_bytes": 128,
        "sha256": "a" * 64,
        "taken_at": None,
        "body_region": "흉부",
        "reading_text": "기존 판독",
        "created_at": "2026-10-10T10:00:00",
        "workflow_stage": "diagnostics",
    }

    def fake_fetch_one(sql, _params=()):
        if "FROM xray_assets x" in sql:
            return dict(stored)
        if "SELECT * FROM xray_assets" in sql:
            return dict(stored)
        return None

    def fake_execute(sql, params=()):
        if "UPDATE xray_assets SET" in sql:
            stored["reading_text"] = params[0]

    monkeypatch.setattr(clinical, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(clinical, "execute", fake_execute)
    monkeypatch.setattr(clinical, "transaction", nullcontext)
    monkeypatch.setattr(clinical, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        clinical,
        "mark_soap_stages_stale",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        workflow_state,
        "invalidate_diagnostic_review",
        lambda encounter_id, exam_type: review_calls.append(
            (encounter_id, exam_type)
        ),
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_id"] = "vet-1"
        session["role"] = "veterinarian"
    response = client.patch(
        "/api/xrays/xray-1",
        json={"reading_text": "수정된 판독"},
    )

    assert response.status_code == 200
    assert response.get_json()["reading_text"] == "수정된 판독"
    assert review_calls == [("encounter-1", "xray")]


def test_cardiac_exam_is_excluded_from_s_and_prior_trends_are_for_a_p(monkeypatch):
    encounter = {
        "id": "encounter-1",
        "patient_id": "patient-1",
        "visit_at": "2026-10-06T10:00:00",
        "chief_complaint": "기침",
        "history_text": "운동불내성",
        "physical_exam": "심잡음 III/VI",
        "chart_number": "C-000001",
        "name": "초코",
        "species": "Canine",
        "breed": "Maltese",
        "sex": "male",
        "neutered": True,
        "birth_date": None,
        "weight_kg": 4.2,
        "disease_name": "MMVD",
        "disease_category": "심장질환",
        "disease_description": None,
    }
    current = [{"la_ao": 1.78, "lviddn": 1.62, "acvim_stage": "B2"}]
    prior = [{"visit_at": "2026-03-11T10:00:00", "la_ao": 1.63, "lviddn": 1.55}]

    def fake_fetch_all(sql, *_args, **_kwargs):
        if "FROM cardiac_exams ce" in sql:
            return prior
        if "FROM cardiac_exams" in sql:
            return current
        return []

    monkeypatch.setattr("app.soap.fetch_one", lambda *_args, **_kwargs: encounter)
    monkeypatch.setattr("app.soap.fetch_all", fake_fetch_all)
    sections = {
        stage: {"status": "confirmed", "current_text": stage}
        for stage in ("S", "O", "A", "P")
    }
    sections["A"]["diagnosis_name"] = "MMVD B2"

    s_context, _ = build_context("encounter-1", "S", sections)
    o_context, _ = build_context("encounter-1", "O", sections)
    a_context, _ = build_context("encounter-1", "A", sections)

    assert s_context["cardiac_exam"] is None
    assert s_context["prior_cardiac_trends"] == []
    assert o_context["cardiac_exam"] == current[0]
    assert o_context["prior_cardiac_trends"] == []
    assert a_context["prior_cardiac_trends"] == prior


def test_ecg_save_marks_objective_and_later_soap_stale(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.register_blueprint(workflow.bp)
    stale_calls = []
    diagnostic_review_calls = []
    stored = {}

    def fake_fetch_one(sql, _params=()):
        if "FROM encounters" in sql:
            return {"id": "encounter-1", "patient_id": "patient-1"}
        if "FROM ecg_exams" in sql:
            return stored or None
        return None

    def fake_execute(sql, params=()):
        if "INSERT INTO ecg_exams" in sql:
            stored.update({
                "id": params[0], "encounter_id": params[1],
                "recorded_at": params[2], "heart_rate_bpm": params[3],
                "rhythm": params[4], "pr_ms": params[5], "qrs_ms": params[6],
                "qt_ms": params[7], "interpretation": params[8],
            })

    monkeypatch.setattr(workflow, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(workflow, "execute", fake_execute)
    monkeypatch.setattr(workflow, "new_id", lambda: "ecg-1")
    monkeypatch.setattr(workflow, "transaction", nullcontext)
    monkeypatch.setattr(workflow, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow,
        "mark_soap_stages_stale",
        lambda encounter_id, stages: stale_calls.append((encounter_id, stages)),
    )
    monkeypatch.setattr(
        workflow,
        "invalidate_diagnostic_review",
        lambda encounter_id, exam_type: diagnostic_review_calls.append(
            (encounter_id, exam_type)
        ),
    )

    response = app.test_client().put(
        "/api/encounters/encounter-1/ecg",
        json={"heart_rate_bpm": 132, "rhythm": "Sinus rhythm", "interpretation": "정상 동율동"},
    )

    assert response.status_code == 200
    assert response.get_json()["exam"]["heart_rate_bpm"] == 132
    assert stale_calls == [("encounter-1", ("O", "A", "P"))]
    assert diagnostic_review_calls == [("encounter-1", "ecg")]


def test_lab_rejects_negative_values(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.register_blueprint(workflow.bp)
    monkeypatch.setattr(
        workflow,
        "fetch_one",
        lambda sql, _params=(): {"id": "encounter-1", "patient_id": "patient-1"}
        if "FROM encounters" in sql else None,
    )

    response = app.test_client().put(
        "/api/encounters/encounter-1/lab",
        json={"creatinine_mg_dl": -1},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_billing_total_is_server_calculated(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.json = __import__("app.json_provider", fromlist=["ISOJSONProvider"]).ISOJSONProvider(app)
    app.register_blueprint(workflow.bp)
    stored = {}

    def fake_fetch_one(sql, _params=()):
        if "FROM encounters" in sql:
            return {"id": "encounter-1", "patient_id": "patient-1"}
        if "FROM billing_records" in sql:
            return stored or None
        return None

    def fake_execute(sql, params=()):
        if "INSERT INTO billing_records" in sql:
            fields = [
                "id", "encounter_id", "consultation_amount", "diagnostic_amount",
                "medication_amount", "other_amount", "discount_amount", "total_amount",
                "payment_status", "payment_method", "paid_at", "notes",
            ]
            stored.update(dict(zip(fields, params)))

    monkeypatch.setattr(workflow, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(workflow, "execute", fake_execute)
    monkeypatch.setattr(workflow, "new_id", lambda: "billing-1")
    monkeypatch.setattr(workflow, "transaction", nullcontext)
    monkeypatch.setattr(workflow, "audit", lambda *_args, **_kwargs: None)

    response = app.test_client().put(
        "/api/encounters/encounter-1/billing",
        json={
            "consultation_amount": 30000,
            "diagnostic_amount": 120000,
            "medication_amount": 20000,
            "discount_amount": 10000,
            "payment_status": "paid",
            "payment_method": "card",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["billing"]["total_amount"] == 160000.0


def test_echo_video_type_is_detected_from_bytes_not_filename():
    from app.workflow import _video_type

    assert _video_type(b"\x00\x00\x00\x18ftypisom" + b"0" * 20) == ("video/mp4", ".mp4")
    assert _video_type(b"\x1a\x45\xdf\xa3" + b"0" * 20) == ("video/webm", ".webm")
    with pytest.raises(ValueError, match="unsupported_video"):
        _video_type(b"not-a-video")


def test_appointment_create_persists_patient_time_and_purpose(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.register_blueprint(workflow.bp)
    stored = {}

    def fake_fetch_one(sql, _params=()):
        if "FROM patients" in sql:
            return {"id": "patient-1"}
        if "FROM appointments" in sql:
            return stored or None
        return None

    def fake_execute(sql, params=()):
        if "INSERT INTO appointments" in sql:
            fields = ["id", "patient_id", "starts_at", "duration_minutes", "purpose", "status", "notes"]
            stored.update(dict(zip(fields, params)))

    monkeypatch.setattr(workflow, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(workflow, "execute", fake_execute)
    monkeypatch.setattr(workflow, "new_id", lambda: "appointment-1")
    monkeypatch.setattr(workflow, "transaction", nullcontext)
    monkeypatch.setattr(workflow, "audit", lambda *_args, **_kwargs: None)

    response = app.test_client().post(
        "/api/appointments",
        json={
            "patient_id": "patient-1",
            "starts_at": "2026-10-07T09:30",
            "duration_minutes": 30,
            "purpose": "Echo 재검",
            "status": "scheduled",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["purpose"] == "Echo 재검"
    assert stored["patient_id"] == "patient-1"


def test_legacy_prescription_cannot_issue_or_bypass_structured_workflow(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(workflow.bp)
    monkeypatch.setattr(
        workflow,
        "fetch_one",
        lambda sql, _params=(): {
            "id": "encounter-1",
            "patient_id": "patient-1",
            "workflow_stage": "education",
        }
        if "FROM encounters" in sql
        else None,
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_id"] = "vet-1"
        session["role"] = "veterinarian"
    response = client.put(
        "/api/encounters/encounter-1/prescription",
        json={"status": "issued", "medication_text": "피모벤단"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "structured_prescription_required"


def test_legacy_prescription_write_is_veterinarian_only(monkeypatch):
    from app import workflow

    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(workflow.bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_id"] = "staff-1"
        session["role"] = "staff"

    response = client.put(
        "/api/encounters/encounter-1/prescription",
        json={"status": "draft", "medication_text": "임시 메모"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "permission_denied"


def test_diagnostic_result_change_clears_review_and_requires_rereview(monkeypatch):
    from app import workflow_state

    statements = []
    monkeypatch.setattr(
        workflow_state,
        "fetch_one",
        lambda *_args, **_kwargs: {"id": "result-1"},
    )
    monkeypatch.setattr(
        workflow_state,
        "execute",
        lambda sql, params=(): statements.append((sql, params)),
    )

    status = workflow_state.invalidate_diagnostic_review("encounter-1", "echo")

    assert status == "completed"
    assert statements[0][1] == ("completed", "encounter-1", "echo")
    assert "reviewed_by=NULL" in statements[0][0]
    assert "reviewed_at=NULL" in statements[0][0]


def test_removed_diagnostic_result_returns_requirement_to_planned(monkeypatch):
    from app import workflow_state

    statements = []
    monkeypatch.setattr(workflow_state, "fetch_one", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow_state,
        "execute",
        lambda sql, params=(): statements.append((sql, params)),
    )

    status = workflow_state.invalidate_diagnostic_review("encounter-1", "xray")

    assert status == "planned"
    assert statements[0][1] == ("planned", "encounter-1", "xray")


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


def test_workflow_invalidation_rolls_back_and_invalidates_outputs(monkeypatch):
    from app import workflow_state

    statements = []
    monkeypatch.setattr(
        workflow_state,
        "fetch_one",
        lambda *_args, **_kwargs: {"workflow_stage": "checkout"},
    )
    monkeypatch.setattr(
        workflow_state,
        "execute",
        lambda sql, params=(): statements.append((" ".join(sql.split()), params)),
    )

    workflow_state.invalidate_workflow("encounter-1", "documentation")

    assert any("SET workflow_stage=%s" in sql for sql, _params in statements)
    assert any("UPDATE client_education_documents" in sql for sql, _params in statements)
    assert any("UPDATE prescriptions SET review_required=TRUE" in sql for sql, _params in statements)


def test_assessment_diagnosis_name_is_required_and_normalized():
    assert validate_diagnosis_name("A", "  승모판 폐쇄부전증  ") == "승모판 폐쇄부전증"
    assert validate_diagnosis_name("S", None) is None
    with pytest.raises(ValueError, match="diagnosis_name_required"):
        validate_diagnosis_name("A", "  ")
    with pytest.raises(ValueError, match="diagnosis_name_too_long"):
        validate_diagnosis_name("A", "진" * 256)


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
        lambda *_args, **_kwargs: {"id": "patient-id", "is_archived": False},
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


def test_patient_name_and_owner_contact_can_be_updated(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    patient = {
        "id": "patient-id",
        "name": "이전 환자명",
        "owner_name": "이전 보호자",
        "owner_phone": "010-1111-2222",
    }

    def fake_execute(sql, params=()):
        if sql.startswith("UPDATE patients SET"):
            patient["name"] = params[0]
            patient["owner_name"] = params[1]
            patient["owner_phone"] = params[2]

    monkeypatch.setattr(clinical, "fetch_one", lambda *_args, **_kwargs: patient)
    monkeypatch.setattr(clinical, "execute", fake_execute)
    monkeypatch.setattr(clinical, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(clinical, "transaction", nullcontext)

    response = app.test_client().patch(
        "/api/patients/patient-id",
        json={
            "name": "새 환자명",
            "owner_name": "새 보호자",
            "owner_phone": "010-3333-4444",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["name"] == "새 환자명"
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


def test_patient_list_hides_archived_by_default_and_can_include_all(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    calls = []

    def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(clinical, "fetch_all", fake_fetch_all)
    client = app.test_client()

    assert client.get("/api/patients").status_code == 200
    assert "p.is_archived = FALSE" in calls[-1][0]

    assert client.get("/api/patients?status=all").status_code == 200
    assert "p.is_archived" not in calls[-1][0]

    response = client.get("/api/patients?status=invalid")
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_patient_status"


def test_patient_can_be_archived_and_restored_without_deletion(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    patient = {"id": "patient-id", "is_archived": False, "archived_at": None}
    audits = []

    def fake_execute(sql, params=()):
        if sql.startswith("UPDATE patients SET"):
            patient["is_archived"] = params[0]
            patient["archived_at"] = params[1]

    monkeypatch.setattr(clinical, "fetch_one", lambda *_args, **_kwargs: patient)
    monkeypatch.setattr(clinical, "execute", fake_execute)
    monkeypatch.setattr(clinical, "transaction", nullcontext)
    monkeypatch.setattr(clinical, "audit", lambda action, *_args, **_kwargs: audits.append(action))
    client = app.test_client()

    response = client.patch("/api/patients/patient-id", json={"is_archived": True})
    assert response.status_code == 200
    assert response.get_json()["is_archived"] is True
    assert patient["archived_at"] is not None
    assert audits[-1] == "patient.archive"

    response = client.patch("/api/patients/patient-id", json={"is_archived": False})
    assert response.status_code == 200
    assert patient["archived_at"] is None
    assert audits[-1] == "patient.restore"

    assert client.patch("/api/patients/patient-id", json={"is_archived": 1}).status_code == 400


def test_archived_patient_cannot_start_new_encounter(monkeypatch):
    from app import clinical

    app = Flask(__name__)
    app.register_blueprint(clinical.bp)
    monkeypatch.setattr(
        clinical,
        "fetch_one",
        lambda *_args, **_kwargs: {"id": "patient-id", "is_archived": True},
    )

    response = app.test_client().post(
        "/api/encounters",
        json={"patient_id": "patient-id", "chief_complaint": "기침"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "patient_archived"
