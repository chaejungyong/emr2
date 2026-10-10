import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from flask import has_request_context, session

from .db import execute, fetch_one, new_id


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        raise TypeError("binary clinical data must not be embedded in a revision")
    raise TypeError(f"unsupported snapshot value: {type(value).__name__}")


def canonical_snapshot(snapshot):
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_clinical_revision(
    encounter_id,
    result_type,
    result_id,
    snapshot,
    change_kind,
    changed_fields=(),
):
    if change_kind not in {"created", "updated", "corrected", "migration"}:
        raise ValueError("invalid_change_kind")
    encoded, digest = canonical_snapshot(snapshot)
    previous = fetch_one(
        """
        SELECT revision_no
        FROM clinical_result_revisions
        WHERE result_type=%s AND result_id=%s
        ORDER BY revision_no DESC
        LIMIT 1 FOR UPDATE
        """,
        (result_type, result_id),
    )
    revision_no = (previous["revision_no"] if previous else 0) + 1
    revision_id = new_id()
    execute(
        """
        INSERT INTO clinical_result_revisions
            (id, encounter_id, result_type, result_id, revision_no, change_kind,
             snapshot_json, snapshot_sha256, changed_fields_json, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            revision_id,
            encounter_id,
            result_type,
            result_id,
            revision_no,
            change_kind,
            encoded,
            digest,
            json.dumps(sorted(set(changed_fields)), ensure_ascii=False),
            session.get("admin_id") if has_request_context() else None,
        ),
    )
    return {
        "id": revision_id,
        "revision_no": revision_no,
        "snapshot_sha256": digest,
    }


def present_revision(row):
    result = dict(row)
    try:
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
    except (TypeError, json.JSONDecodeError):
        result["snapshot"] = None
        result.pop("snapshot_json", None)
    try:
        result["changed_fields"] = json.loads(
            result.pop("changed_fields_json") or "[]"
        )
    except (TypeError, json.JSONDecodeError):
        result["changed_fields"] = []
        result.pop("changed_fields_json", None)
    return result
