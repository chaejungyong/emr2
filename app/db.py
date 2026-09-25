import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import pymysql
from flask import current_app, g, has_request_context, request, session


def connect(database=True):
    config = dict(current_app.config["DB_CONFIG"])
    if not database:
        config.pop("database", None)
    return pymysql.connect(
        **config,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@contextmanager
def transaction():
    connection = get_db()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def fetch_one(sql, params=()):
    with get_db().cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def fetch_all(sql, params=()):
    with get_db().cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def execute(sql, params=()):
    with get_db().cursor() as cursor:
        return cursor.execute(sql, params)


def new_id():
    return str(uuid.uuid4())


def audit(action, entity_type=None, entity_id=None, metadata=None):
    admin_id = session.get("admin_id") if has_request_context() else None
    ip_address = request.remote_addr if has_request_context() else None
    execute(
        """
        INSERT INTO audit_events
            (id, admin_id, action, entity_type, entity_id, metadata_json, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            new_id(),
            admin_id,
            action,
            entity_type,
            entity_id,
            json.dumps(metadata or {}, ensure_ascii=False),
            ip_address,
        ),
    )


def run_migrations(app):
    sql_dir = Path(app.config["BASE_DIR"]) / "sql"
    with app.app_context():
        connection = connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(64) PRIMARY KEY,
                        applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                connection.commit()
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {row["version"] for row in cursor.fetchall()}

                for path in sorted(sql_dir.glob("*.sql")):
                    version = path.stem
                    if version in applied:
                        continue
                    statements = [
                        statement.strip()
                        for statement in path.read_text(encoding="utf-8").split(";")
                        if statement.strip()
                    ]
                    for statement in statements:
                        cursor.execute(statement)
                    cursor.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)",
                        (version,),
                    )
                    connection.commit()
                    app.logger.info("Applied database migration %s", version)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def init_app(app):
    app.teardown_appcontext(close_db)
