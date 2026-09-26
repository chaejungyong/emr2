import hashlib
import io
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from flask import Blueprint, current_app, jsonify, request, send_file

from .db import audit, execute, fetch_all, fetch_one, new_id, transaction


bp = Blueprint("clinical", __name__, url_prefix="/api")

ALLOWED_SEX = {"male", "female", "unknown"}
ALLOWED_SPECIES = {"Canine", "Feline"}
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"\x89PNG\r\n\x1a\n": ("image/png", ".png"),
}


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "json_body_required"}), 400)
    return payload, None


def clean_text(value, max_length=None, required=False):
    if value is None:
        if required:
            raise ValueError("required")
        return None
    text = str(value).strip()
    if required and not text:
        raise ValueError("required")
    if max_length and len(text) > max_length:
        raise ValueError("too_long")
    return text or None


def clean_owner_phone(value, required=False):
    phone = clean_text(value, 13, required)
    if phone is not None and not re.fullmatch(r"010-[0-9]{4}-[0-9]{4}", phone):
        raise ValueError("invalid_owner_phone")
    return phone


def parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("invalid_date") from error


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as error:
        raise ValueError("invalid_datetime") from error


def validation_error(error):
    return jsonify({"error": "validation_error", "message": str(error)}), 400


def format_chart_number(sequence):
    return f"C-{int(sequence):06d}"


def allocate_chart_number():
    row = fetch_one(
        """
        SELECT next_value
        FROM patient_chart_number_sequence
        WHERE id = 1
        FOR UPDATE
        """
    )
    if not row:
        raise RuntimeError("patient chart number sequence is not initialized")
    sequence = int(row["next_value"])
    execute(
        """
        UPDATE patient_chart_number_sequence
        SET next_value = next_value + 1
        WHERE id = 1
        """
    )
    return format_chart_number(sequence)


@bp.get("/patients/next-chart-number")
def preview_next_chart_number():
    row = fetch_one(
        "SELECT next_value FROM patient_chart_number_sequence WHERE id = 1"
    )
    if not row:
        return jsonify({"error": "chart_number_sequence_unavailable"}), 503
    return jsonify({"chart_number": format_chart_number(row["next_value"])})


@bp.get("/patients")
def list_patients():
    query = request.args.get("q", "").strip()
    params = []
    where = ""
    if query:
        where = "WHERE p.name LIKE %s OR p.chart_number LIKE %s"
        needle = f"%{query}%"
        params.extend([needle, needle])
    rows = fetch_all(
        f"""
        SELECT p.*,
               (SELECT COUNT(*) FROM encounters e WHERE e.patient_id = p.id) AS encounter_count,
               (SELECT MAX(e.visit_at) FROM encounters e WHERE e.patient_id = p.id) AS last_visit_at
        FROM patients p
        {where}
        ORDER BY p.updated_at DESC
        LIMIT 200
        """,
        params,
    )
    return jsonify({"items": rows})


@bp.post("/patients")
def create_patient():
    payload, error = body()
    if error:
        return error
    try:
        patient_id = new_id()
        patient_values = (
            clean_text(payload.get("name"), 120, True),
            clean_text(payload.get("owner_name"), 120, True),
            clean_owner_phone(payload.get("owner_phone"), True),
            clean_text(payload.get("species"), 80, True),
            clean_text(payload.get("breed"), 120),
            payload.get("sex", "unknown"),
            payload.get("neutered"),
            parse_iso_date(payload.get("birth_date")),
            payload.get("weight_kg"),
            clean_text(payload.get("notes")),
        )
        if patient_values[3] not in ALLOWED_SPECIES:
            raise ValueError("invalid_species")
        if patient_values[5] not in ALLOWED_SEX:
            raise ValueError("invalid_sex")
        if patient_values[8] is not None and not 0 < float(patient_values[8]) <= 500:
            raise ValueError("invalid_weight")
    except (ValueError, TypeError):
        return validation_error("환자 입력값을 확인하세요.")
    with transaction():
        chart_number = allocate_chart_number()
        execute(
            """
            INSERT INTO patients
                (id, chart_number, name, owner_name, owner_phone, species, breed,
                 sex, neutered, birth_date, weight_kg, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (patient_id, chart_number, *patient_values),
        )
        audit("patient.create", "patient", patient_id)
    return jsonify(fetch_one("SELECT * FROM patients WHERE id = %s", (patient_id,))), 201


@bp.get("/patients/<patient_id>")
def get_patient(patient_id):
    patient = fetch_one("SELECT * FROM patients WHERE id = %s", (patient_id,))
    if not patient:
        return jsonify({"error": "patient_not_found"}), 404
    encounters = fetch_all(
        """
        SELECT e.*, d.name AS disease_name
        FROM encounters e
        LEFT JOIN diseases d ON d.id = e.disease_id
        WHERE e.patient_id = %s
        ORDER BY e.visit_at DESC
        """,
        (patient_id,),
    )
    patient["encounters"] = encounters
    return jsonify(patient)


@bp.patch("/patients/<patient_id>")
def update_patient(patient_id):
    if not fetch_one("SELECT id FROM patients WHERE id = %s", (patient_id,)):
        return jsonify({"error": "patient_not_found"}), 404
    payload, error = body()
    if error:
        return error
    if "chart_number" in payload:
        return jsonify(
            {
                "error": "chart_number_immutable",
                "message": "차트번호는 변경할 수 없습니다.",
            }
        ), 400
    allowed = {
        "name",
        "owner_name",
        "owner_phone",
        "species",
        "breed",
        "sex",
        "neutered",
        "birth_date",
        "weight_kg",
        "notes",
    }
    updates, params = [], []
    try:
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key in {"name", "owner_name"}:
                value = clean_text(value, 120, True)
            elif key == "owner_phone":
                value = clean_owner_phone(value, True)
            elif key == "species":
                value = clean_text(value, 80, True)
                if value not in ALLOWED_SPECIES:
                    raise ValueError("invalid_species")
            elif key in {"breed"}:
                value = clean_text(value, 120)
            elif key == "notes":
                value = clean_text(value)
            elif key == "sex" and value not in ALLOWED_SEX:
                raise ValueError("invalid_sex")
            elif key == "birth_date":
                value = parse_iso_date(value)
            elif key == "weight_kg" and value is not None and not 0 < float(value) <= 500:
                raise ValueError("invalid_weight")
            updates.append(f"{key} = %s")
            params.append(value)
    except (ValueError, TypeError):
        return validation_error("환자 입력값을 확인하세요.")
    if not updates:
        return jsonify({"error": "no_supported_fields"}), 400
    params.append(patient_id)
    with transaction():
        execute(f"UPDATE patients SET {', '.join(updates)} WHERE id = %s", params)
        audit("patient.update", "patient", patient_id, {"fields": list(payload)})
    return jsonify(fetch_one("SELECT * FROM patients WHERE id = %s", (patient_id,)))


@bp.get("/diseases")
def list_diseases():
    rows = fetch_all(
        """
        SELECT * FROM diseases
        WHERE is_active = TRUE OR %s = 'all'
        ORDER BY category, name
        """,
        (request.args.get("status", "active"),),
    )
    return jsonify({"items": rows})


@bp.post("/diseases")
def create_disease():
    payload, error = body()
    if error:
        return error
    try:
        disease_id = new_id()
        name = clean_text(payload.get("name"), 180, True)
        category = clean_text(payload.get("category"), 120) or "심장질환"
        description = clean_text(payload.get("description"))
    except ValueError:
        return validation_error("질병명을 입력하세요.")
    with transaction():
        execute(
            "INSERT INTO diseases (id, name, category, description) VALUES (%s, %s, %s, %s)",
            (disease_id, name, category, description),
        )
        audit("disease.create", "disease", disease_id)
    return jsonify(fetch_one("SELECT * FROM diseases WHERE id = %s", (disease_id,))), 201


@bp.patch("/diseases/<disease_id>")
def update_disease(disease_id):
    if not fetch_one("SELECT id FROM diseases WHERE id = %s", (disease_id,)):
        return jsonify({"error": "disease_not_found"}), 404
    payload, error = body()
    if error:
        return error
    updates, params = [], []
    for key in ("name", "category", "description", "is_active"):
        if key not in payload:
            continue
        value = payload[key]
        try:
            if key == "name":
                value = clean_text(value, 180, True)
            elif key == "category":
                value = clean_text(value, 120, True)
            elif key == "description":
                value = clean_text(value)
        except ValueError:
            return validation_error("질병 입력값을 확인하세요.")
        updates.append(f"{key} = %s")
        params.append(value)
    if not updates:
        return jsonify({"error": "no_supported_fields"}), 400
    params.append(disease_id)
    with transaction():
        execute(f"UPDATE diseases SET {', '.join(updates)} WHERE id = %s", params)
        audit("disease.update", "disease", disease_id, {"fields": list(payload)})
    return jsonify(fetch_one("SELECT * FROM diseases WHERE id = %s", (disease_id,)))


@bp.get("/encounters")
def list_encounters():
    patient_id = request.args.get("patient_id")
    where, params = "", []
    if patient_id:
        where, params = "WHERE e.patient_id = %s", [patient_id]
    rows = fetch_all(
        f"""
        SELECT e.*, p.name AS patient_name, p.chart_number, d.name AS disease_name
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN diseases d ON d.id = e.disease_id
        {where}
        ORDER BY e.visit_at DESC
        LIMIT 300
        """,
        params,
    )
    return jsonify({"items": rows})


@bp.post("/encounters")
def create_encounter():
    payload, error = body()
    if error:
        return error
    patient_id = payload.get("patient_id")
    if not fetch_one("SELECT id FROM patients WHERE id = %s", (patient_id,)):
        return jsonify({"error": "patient_not_found"}), 404
    disease_id = payload.get("disease_id") or None
    if disease_id and not fetch_one("SELECT id FROM diseases WHERE id = %s", (disease_id,)):
        return jsonify({"error": "disease_not_found"}), 404
    try:
        encounter_id = new_id()
        visit_at = parse_iso_datetime(payload.get("visit_at")) or datetime.utcnow()
        values = (
            encounter_id,
            patient_id,
            disease_id,
            visit_at,
            clean_text(payload.get("chief_complaint")),
            clean_text(payload.get("history_text")),
            clean_text(payload.get("physical_exam")),
        )
    except ValueError:
        return validation_error("진료 입력값을 확인하세요.")
    with transaction():
        execute(
            """
            INSERT INTO encounters
                (id, patient_id, disease_id, visit_at, chief_complaint,
                 history_text, physical_exam)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            values,
        )
        audit("encounter.create", "encounter", encounter_id)
    return get_encounter(encounter_id)[0], 201


@bp.get("/encounters/<encounter_id>")
def get_encounter(encounter_id):
    encounter = fetch_one(
        """
        SELECT e.*, p.name AS patient_name, p.chart_number, p.species, p.breed,
               p.sex, p.neutered, p.birth_date, p.weight_kg,
               d.name AS disease_name, d.category AS disease_category
        FROM encounters e
        JOIN patients p ON p.id = e.patient_id
        LEFT JOIN diseases d ON d.id = e.disease_id
        WHERE e.id = %s
        """,
        (encounter_id,),
    )
    if not encounter:
        return jsonify({"error": "encounter_not_found"}), 404
    encounter["xrays"] = [
        present_xray(row)
        for row in fetch_all(
            "SELECT * FROM xray_assets WHERE encounter_id = %s ORDER BY created_at DESC",
            (encounter_id,),
        )
    ]
    return jsonify(encounter), 200


@bp.patch("/encounters/<encounter_id>")
def update_encounter(encounter_id):
    if not fetch_one("SELECT id FROM encounters WHERE id = %s", (encounter_id,)):
        return jsonify({"error": "encounter_not_found"}), 404
    payload, error = body()
    if error:
        return error
    allowed = {
        "disease_id",
        "visit_at",
        "chief_complaint",
        "history_text",
        "physical_exam",
    }
    updates, params, updated_fields = [], [], []
    try:
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key == "disease_id" and value:
                if not fetch_one("SELECT id FROM diseases WHERE id = %s", (value,)):
                    return jsonify({"error": "disease_not_found"}), 404
            elif key == "visit_at":
                value = parse_iso_datetime(value)
            elif key in {"chief_complaint", "history_text", "physical_exam"}:
                value = clean_text(value)
            updates.append(f"{key} = %s")
            params.append(value or None)
            updated_fields.append(key)
    except ValueError:
        return validation_error("진료 입력값을 확인하세요.")
    if not updates:
        return jsonify({"error": "no_supported_fields"}), 400
    params.append(encounter_id)
    with transaction():
        execute(f"UPDATE encounters SET {', '.join(updates)} WHERE id = %s", params)
        context_fields = {
            "disease_id",
            "chief_complaint",
            "history_text",
            "physical_exam",
        }
        if context_fields.intersection(updated_fields):
            execute(
                """
                UPDATE soap_sections ss
                JOIN soap_documents sd ON sd.id = ss.soap_document_id
                SET ss.status = CASE
                        WHEN ss.current_text IS NULL THEN 'pending'
                        ELSE 'stale'
                    END
                WHERE sd.encounter_id = %s
                """,
                (encounter_id,),
            )
            execute(
                """
                UPDATE soap_documents
                SET status = 'draft'
                WHERE encounter_id = %s
                """,
                (encounter_id,),
            )
            execute(
                "UPDATE encounters SET status = 'draft' WHERE id = %s",
                (encounter_id,),
            )
        audit("encounter.update", "encounter", encounter_id, {"fields": updated_fields})
    return get_encounter(encounter_id)


def detect_image(data):
    detected = None
    for signature, info in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            detected = info
            break
    if not detected:
        raise ValueError("unsupported_image")
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 50_000_000:
            raise ValueError("image_dimensions_too_large")
        image.verify()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("invalid_image") from error
    return detected


def present_xray(row):
    return {
        "id": row["id"],
        "encounter_id": row["encounter_id"],
        "original_name": row["original_name"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "taken_at": row["taken_at"],
        "body_region": row["body_region"],
        "reading_text": row["reading_text"],
        "created_at": row["created_at"],
        "file_url": f"/api/xrays/{row['id']}/file",
    }


@bp.post("/encounters/<encounter_id>/xrays")
def upload_xray(encounter_id):
    if not fetch_one("SELECT id FROM encounters WHERE id = %s", (encounter_id,)):
        return jsonify({"error": "encounter_not_found"}), 404
    existing_count = fetch_one(
        "SELECT COUNT(*) AS count FROM xray_assets WHERE encounter_id = %s",
        (encounter_id,),
    )["count"]
    if existing_count >= 10:
        return jsonify({"error": "xray_limit_reached", "message": "진료당 최대 10개까지 등록할 수 있습니다."}), 409
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "file_required"}), 400
    data = uploaded.stream.read(current_app.config["MAX_XRAY_BYTES"] + 1)
    if len(data) > current_app.config["MAX_XRAY_BYTES"]:
        return jsonify({"error": "xray_too_large"}), 413
    try:
        mime_type, extension = detect_image(data)
    except ValueError:
        return jsonify({"error": "invalid_xray", "message": "JPEG 또는 PNG만 지원합니다."}), 400
    try:
        taken_at = parse_iso_datetime(request.form.get("taken_at"))
        body_region = clean_text(request.form.get("body_region"), 120)
        reading_text = clean_text(request.form.get("reading_text"))
    except ValueError:
        return validation_error("X-ray 입력값을 확인하세요.")

    xray_id = new_id()
    relative = Path(encounter_id) / f"{xray_id}{extension}"
    destination = (current_app.config["XRAY_ROOT"] / relative).resolve()
    if current_app.config["XRAY_ROOT"] not in destination.parents:
        return jsonify({"error": "invalid_storage_path"}), 400
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        with transaction():
            execute(
                """
                INSERT INTO xray_assets
                    (id, encounter_id, storage_key, original_name, mime_type,
                     size_bytes, sha256, taken_at, body_region, reading_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    xray_id,
                    encounter_id,
                    relative.as_posix(),
                    Path(uploaded.filename).name[:255],
                    mime_type,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    taken_at,
                    body_region,
                    reading_text,
                ),
            )
            audit("xray.upload", "xray", xray_id, {"size_bytes": len(data)})
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    row = fetch_one("SELECT * FROM xray_assets WHERE id = %s", (xray_id,))
    return jsonify(present_xray(row)), 201


@bp.get("/encounters/<encounter_id>/xrays")
def list_xrays(encounter_id):
    rows = fetch_all(
        "SELECT * FROM xray_assets WHERE encounter_id = %s ORDER BY created_at DESC",
        (encounter_id,),
    )
    return jsonify({"items": [present_xray(row) for row in rows]})


@bp.get("/xrays/<xray_id>/file")
def get_xray_file(xray_id):
    row = fetch_one("SELECT * FROM xray_assets WHERE id = %s", (xray_id,))
    if not row:
        return jsonify({"error": "xray_not_found"}), 404
    path = (current_app.config["XRAY_ROOT"] / row["storage_key"]).resolve()
    if current_app.config["XRAY_ROOT"] not in path.parents or not path.is_file():
        return jsonify({"error": "xray_file_missing"}), 404
    with transaction():
        audit("xray.view", "xray", xray_id)
    return send_file(
        path,
        mimetype=row["mime_type"],
        download_name=row["original_name"],
        conditional=True,
    )


@bp.patch("/xrays/<xray_id>")
def update_xray(xray_id):
    if not fetch_one("SELECT id FROM xray_assets WHERE id = %s", (xray_id,)):
        return jsonify({"error": "xray_not_found"}), 404
    payload, error = body()
    if error:
        return error
    updates, params = [], []
    try:
        for key in ("reading_text", "body_region", "taken_at"):
            if key not in payload:
                continue
            value = payload[key]
            if key == "taken_at":
                value = parse_iso_datetime(value)
            else:
                value = clean_text(value, 120 if key == "body_region" else None)
            updates.append(f"{key} = %s")
            params.append(value)
    except ValueError:
        return validation_error("X-ray 입력값을 확인하세요.")
    if not updates:
        return jsonify({"error": "no_supported_fields"}), 400
    params.append(xray_id)
    with transaction():
        execute(f"UPDATE xray_assets SET {', '.join(updates)} WHERE id = %s", params)
        audit("xray.update", "xray", xray_id, {"fields": list(payload)})
    return jsonify(present_xray(fetch_one("SELECT * FROM xray_assets WHERE id = %s", (xray_id,))))
