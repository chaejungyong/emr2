import hashlib
import re
import unicodedata
from datetime import datetime
from flask import current_app
from pypdf import PdfReader

from .ai import (
    collection_name_for_full,
    get_embedding_client,
    get_vector_store,
)
from .db import execute, fetch_all, fetch_one, new_id, transaction


class IndexingError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text):
    value = unicodedata.normalize("NFC", text or "")
    value = value.replace("\x00", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def split_long_text(text, target=3200, overlap=400):
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return []
    chunks, current = [], ""
    for paragraph in paragraphs:
        pieces = (
            [paragraph[index : index + target] for index in range(0, len(paragraph), target)]
            if len(paragraph) > target
            else [paragraph]
        )
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if current and len(candidate) > target:
                chunks.append(current)
                prefix = current[-overlap:] if overlap else ""
                current = f"{prefix}\n\n{piece}".strip()
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def extract_pdf(path):
    try:
        if path.stat().st_size > current_app.config["MAX_PDF_BYTES"]:
            raise IndexingError("허용된 PDF 파일 크기를 초과했습니다.")
        reader = PdfReader(str(path), strict=False)
        if len(reader.pages) > current_app.config["MAX_PDF_PAGES"]:
            raise IndexingError("허용된 PDF 페이지 수를 초과했습니다.")
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as error:
                raise IndexingError("암호화된 PDF는 처리할 수 없습니다.") from error
        chunks = []
        for page_number, page in enumerate(reader.pages, 1):
            try:
                text = normalize_text(page.extract_text() or "")
            except Exception as error:
                raise IndexingError(f"{page_number}쪽 텍스트 추출 실패: {error}") from error
            if not text:
                continue
            heading = next(
                (line.strip() for line in text.splitlines() if 3 <= len(line.strip()) <= 120),
                None,
            )
            for page_chunk in split_long_text(text):
                chunks.append(
                    {
                        "page_start": page_number,
                        "page_end": page_number,
                        "heading": heading,
                        "content": page_chunk,
                    }
                )
        if sum(len(chunk["content"]) for chunk in chunks) < 200:
            raise IndexingError(
                "추출 가능한 텍스트가 없습니다. 스캔 PDF라면 OCR 처리 후 다시 시도하세요."
            )
        return len(reader.pages), chunks
    except IndexingError:
        raise
    except Exception as error:
        raise IndexingError(f"PDF를 열 수 없습니다: {error}") from error


def scan_pdfs():
    root = current_app.config["KNOWLEDGE_ROOT"].resolve()
    files = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            continue
        files.append(path)
    return sorted(files)


def source_key(path):
    return path.relative_to(current_app.config["KNOWLEDGE_ROOT"]).as_posix()


def embed_chunks(chunks):
    client = get_embedding_client()
    batch_size = current_app.config["EMBEDDING_BATCH_SIZE"]
    vectors = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors.extend(client.embed([chunk["content"] for chunk in batch]))
    return vectors


def get_or_create_document(key, display_name):
    document = fetch_one("SELECT * FROM kb_documents WHERE source_key = %s", (key,))
    if document:
        return document
    document_id = new_id()
    with transaction():
        execute(
            """
            INSERT INTO kb_documents (id, source_key, display_name)
            VALUES (%s, %s, %s)
            """,
            (document_id, key, display_name),
        )
    return fetch_one("SELECT * FROM kb_documents WHERE id = %s", (document_id,))


def active_version(document_id):
    return fetch_one(
        """
        SELECT * FROM kb_document_versions
        WHERE document_id = %s AND is_active = TRUE
        ORDER BY created_at DESC LIMIT 1
        """,
        (document_id,),
    )


def create_version(document, digest, file_size, page_count, chunks):
    version_id = new_id()
    chunk_rows = []
    with transaction():
        execute(
            """
            INSERT INTO kb_document_versions
                (id, document_id, sha256, file_size, page_count, embedding_model,
                 chunker_version, status, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', FALSE)
            """,
            (
                version_id,
                document["id"],
                digest,
                file_size,
                page_count,
                current_app.config["EMBEDDING_MODEL"],
                current_app.config["CHUNKER_VERSION"],
            ),
        )
        for index, chunk in enumerate(chunks):
            chunk_id = new_id()
            content_digest = hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest()
            execute(
                """
                INSERT INTO kb_chunks
                    (id, document_version_id, chunk_index, page_start, page_end,
                     heading, content, content_sha256, token_estimate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    chunk_id,
                    version_id,
                    index,
                    chunk["page_start"],
                    chunk["page_end"],
                    chunk["heading"],
                    chunk["content"],
                    content_digest,
                    max(1, len(chunk["content"]) // 4),
                ),
            )
            chunk_rows.append({"id": chunk_id, **chunk})
    return version_id, chunk_rows


def mark_version_failed(version_id, message):
    with transaction():
        execute(
            """
            UPDATE kb_document_versions
            SET status = 'failed', error_message = %s
            WHERE id = %s
            """,
            (str(message)[:2000], version_id),
        )


def points_for(document, version_id, chunks, vectors):
    return [
        {
            "id": chunk["id"],
            "vector": vector,
            "payload": {
                "chunk_id": chunk["id"],
                "document_id": document["id"],
                "document_version_id": version_id,
                "source_key": document["source_key"],
                "display_name": document["display_name"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "heading": chunk["heading"],
                "text": chunk["content"],
                "embedding_model": current_app.config["EMBEDDING_MODEL"],
                "chunker_version": current_app.config["CHUNKER_VERSION"],
            },
        }
        for chunk, vector in zip(chunks, vectors)
    ]


def set_item(job_id, key, status, chunk_count=0, error=None):
    with transaction():
        execute(
            """
            UPDATE index_job_items
            SET status = %s, chunk_count = %s, error_message = %s,
                started_at = COALESCE(started_at, %s),
                finished_at = CASE WHEN %s IN ('completed', 'skipped', 'failed')
                                   THEN %s ELSE finished_at END
            WHERE job_id = %s AND source_key = %s
            """,
            (
                status,
                chunk_count,
                str(error)[:2000] if error else None,
                datetime.utcnow(),
                status,
                datetime.utcnow(),
                job_id,
                key,
            ),
        )


def increment_job(job_id, column):
    allowed = {"processed_files", "skipped_files", "failed_files"}
    if column not in allowed:
        raise ValueError("invalid counter")
    with transaction():
        execute(
            f"UPDATE index_jobs SET {column} = {column} + 1 WHERE id = %s",
            (job_id,),
        )


def prepare_job_items(job_id, files):
    with transaction():
        execute(
            "UPDATE index_jobs SET total_files = %s WHERE id = %s",
            (len(files), job_id),
        )
        for path in files:
            execute(
                """
                INSERT IGNORE INTO index_job_items (id, job_id, source_key)
                VALUES (%s, %s, %s)
                """,
                (new_id(), job_id, source_key(path)),
            )


def index_incremental(job_id, files):
    store = get_vector_store()
    existing_collection = store.active_collection()
    active_configs = fetch_all(
        """
        SELECT DISTINCT embedding_model, chunker_version
        FROM kb_document_versions
        WHERE is_active = TRUE
        """
    )
    incompatible = any(
        row["embedding_model"] != current_app.config["EMBEDDING_MODEL"]
        or row["chunker_version"] != current_app.config["CHUNKER_VERSION"]
        for row in active_configs
    )
    if incompatible:
        raise IndexingError(
            "임베딩 모델 또는 청킹 버전이 변경되었습니다. 전체 재색인을 실행하세요."
        )
    if active_configs and not existing_collection:
        raise IndexingError(
            "활성 Qdrant alias가 없습니다. 기존 DB 기록을 보존하고 전체 재색인을 실행하세요."
        )
    seen = set()
    for path in files:
        key = source_key(path)
        seen.add(key)
        set_item(job_id, key, "processing")
        version_id = None
        try:
            digest = sha256_file(path)
            document = get_or_create_document(key, path.name)
            current = active_version(document["id"])
            if (
                current
                and existing_collection
                and current["sha256"] == digest
                and current["embedding_model"] == current_app.config["EMBEDDING_MODEL"]
                and current["chunker_version"] == current_app.config["CHUNKER_VERSION"]
            ):
                set_item(job_id, key, "skipped")
                increment_job(job_id, "skipped_files")
                continue
            page_count, extracted = extract_pdf(path)
            vectors = embed_chunks(extracted)
            version_id, chunks = create_version(
                document, digest, path.stat().st_size, page_count, extracted
            )
            collection = store.ensure_active_collection(
                len(vectors[0]), current_app.config["EMBEDDING_MODEL"]
            )
            store.upsert(
                collection, points_for(document, version_id, chunks, vectors)
            )
            with transaction():
                execute(
                    "UPDATE kb_document_versions SET is_active = FALSE WHERE document_id = %s",
                    (document["id"],),
                )
                execute(
                    """
                    UPDATE kb_document_versions
                    SET status = 'ready', is_active = TRUE, error_message = NULL
                    WHERE id = %s
                    """,
                    (version_id,),
                )
                execute(
                    "UPDATE kb_documents SET is_active = TRUE WHERE id = %s",
                    (document["id"],),
                )
            store.delete_document_versions(collection, document["id"], version_id)
            set_item(job_id, key, "completed", len(chunks))
            increment_job(job_id, "processed_files")
        except Exception as error:
            if version_id:
                mark_version_failed(version_id, error)
            set_item(job_id, key, "failed", error=error)
            increment_job(job_id, "failed_files")

    removed = fetch_all(
        "SELECT id, source_key FROM kb_documents WHERE is_active = TRUE"
    )
    collection = store.active_collection()
    for document in removed:
        if document["source_key"] in seen:
            continue
        if collection:
            store.delete_document_versions(collection, document["id"])
        with transaction():
            execute(
                "UPDATE kb_documents SET is_active = FALSE WHERE id = %s",
                (document["id"],),
            )


def index_full(job_id, files):
    if not files:
        raise IndexingError("knowledge/inbox 폴더에 PDF가 없습니다.")
    store = get_vector_store()
    collection = None
    prepared = []
    failures = []
    for path in files:
        key = source_key(path)
        set_item(job_id, key, "processing")
        version_id = None
        try:
            digest = sha256_file(path)
            document = get_or_create_document(key, path.name)
            page_count, extracted = extract_pdf(path)
            vectors = embed_chunks(extracted)
            version_id, chunks = create_version(
                document, digest, path.stat().st_size, page_count, extracted
            )
            if collection is None:
                collection = collection_name_for_full(
                    current_app.config["EMBEDDING_MODEL"]
                )
                store.create_collection(collection, len(vectors[0]))
                with transaction():
                    execute(
                        "UPDATE index_jobs SET new_collection = %s WHERE id = %s",
                        (collection, job_id),
                    )
            elif store.collection_dimension(collection) != len(vectors[0]):
                raise IndexingError("문서 사이의 임베딩 차원이 다릅니다.")
            store.upsert(collection, points_for(document, version_id, chunks, vectors))
            prepared.append((document, version_id))
            set_item(job_id, key, "completed", len(chunks))
            increment_job(job_id, "processed_files")
        except Exception as error:
            if version_id:
                mark_version_failed(version_id, error)
            failures.append((key, str(error)))
            set_item(job_id, key, "failed", error=error)
            increment_job(job_id, "failed_files")
    if failures:
        for _document, version_id in prepared:
            mark_version_failed(
                version_id,
                "전체 재색인의 다른 문서가 실패하여 컬렉션을 활성화하지 않았습니다.",
            )
        raise IndexingError(
            f"{len(failures)}개 PDF 처리 실패. 기존 검색 인덱스를 유지합니다."
        )
    if not collection or not prepared:
        raise IndexingError("색인할 수 있는 PDF가 없습니다.")

    store.switch_alias(collection)
    with transaction():
        execute("UPDATE kb_documents SET is_active = FALSE")
        execute("UPDATE kb_document_versions SET is_active = FALSE")
        for document, version_id in prepared:
            execute(
                "UPDATE kb_documents SET is_active = TRUE WHERE id = %s",
                (document["id"],),
            )
            execute(
                """
                UPDATE kb_document_versions
                SET status = 'ready', is_active = TRUE, error_message = NULL
                WHERE id = %s
                """,
                (version_id,),
            )


def run_job(job_id):
    job = fetch_one("SELECT * FROM index_jobs WHERE id = %s", (job_id,))
    if not job:
        raise IndexingError("색인 작업을 찾을 수 없습니다.")
    files = scan_pdfs()
    prepare_job_items(job_id, files)
    try:
        if not files:
            raise IndexingError(
                "knowledge/inbox 폴더에 PDF가 없습니다. 기존 검색 인덱스를 유지합니다."
            )
        if job["job_type"] == "full":
            index_full(job_id, files)
        else:
            index_incremental(job_id, files)
        refreshed = fetch_one(
            "SELECT failed_files FROM index_jobs WHERE id = %s", (job_id,)
        )
        final_status = "completed" if refreshed["failed_files"] == 0 else "failed"
        message = None if final_status == "completed" else "일부 문서 색인에 실패했습니다."
    except Exception as error:
        final_status, message = "failed", str(error)[:2000]
    with transaction():
        execute(
            """
            UPDATE index_jobs
            SET status = %s, error_message = %s, finished_at = %s
            WHERE id = %s
            """,
            (final_status, message, datetime.utcnow(), job_id),
        )


def claim_next_job():
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM index_jobs
                WHERE status = 'queued'
                ORDER BY created_at
                LIMIT 1 FOR UPDATE
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'running', started_at = %s
                WHERE id = %s
                """,
                (datetime.utcnow(), row["id"]),
            )
            return row["id"]
