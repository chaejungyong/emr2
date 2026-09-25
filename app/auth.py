import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import audit, execute, fetch_one, new_id, transaction


bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def init_admin(app):
    password = app.config.get("ADMIN_PASSWORD")
    if not password:
        app.logger.warning(
            "ADMIN_PASSWORD is not configured. Existing administrators can log in, "
            "but a first administrator will not be created."
        )
        return
    with app.app_context():
        username = app.config["ADMIN_USERNAME"]
        existing = fetch_one("SELECT id FROM admins WHERE username = %s", (username,))
        if existing:
            return
        with transaction():
            execute(
                """
                INSERT INTO admins (id, username, password_hash)
                VALUES (%s, %s, %s)
                """,
                (new_id(), username, generate_password_hash(password, method="scrypt")),
            )
        app.logger.info("Created initial administrator '%s'.", username)


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return jsonify({"error": "authentication_required"}), 401
        return view(*args, **kwargs)

    return wrapped


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def install_guards(app):
    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/"):
            return None
        public_paths = {"/api/health", "/api/auth/login"}
        if request.path not in public_paths and not session.get("admin_id"):
            return jsonify({"error": "authentication_required"}), 401
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.path != "/api/auth/login"
        ):
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(supplied, expected):
                return jsonify({"error": "invalid_csrf_token"}), 403
        return None


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400

    admin = fetch_one("SELECT * FROM admins WHERE username = %s", (username,))
    now = datetime.utcnow()
    if admin and admin["locked_until"] and admin["locked_until"] > now:
        return jsonify({"error": "account_temporarily_locked"}), 429

    valid = bool(
        admin
        and admin["is_active"]
        and check_password_hash(admin["password_hash"], password)
    )
    if not valid:
        if admin:
            failures = admin["failed_login_count"] + 1
            locked_until = None
            if failures >= current_app.config["LOGIN_MAX_FAILURES"]:
                locked_until = now + timedelta(
                    minutes=current_app.config["LOGIN_LOCK_MINUTES"]
                )
                failures = 0
            with transaction():
                execute(
                    """
                    UPDATE admins
                    SET failed_login_count = %s, locked_until = %s
                    WHERE id = %s
                    """,
                    (failures, locked_until, admin["id"]),
                )
        return jsonify({"error": "invalid_credentials"}), 401

    session.clear()
    session.permanent = True
    session["admin_id"] = admin["id"]
    session["username"] = admin["username"]
    token = csrf_token()
    with transaction():
        execute(
            """
            UPDATE admins
            SET failed_login_count = 0, locked_until = NULL, last_login_at = %s
            WHERE id = %s
            """,
            (now, admin["id"]),
        )
        audit("auth.login", "admin", admin["id"])
    return jsonify(
        {"id": admin["id"], "username": admin["username"], "csrf_token": token}
    )


@bp.post("/logout")
def logout():
    admin_id = session.get("admin_id")
    with transaction():
        audit("auth.logout", "admin", admin_id)
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    admin = fetch_one(
        "SELECT id, username, last_login_at FROM admins WHERE id = %s AND is_active = TRUE",
        (session["admin_id"],),
    )
    if not admin:
        session.clear()
        return jsonify({"error": "authentication_required"}), 401
    admin["csrf_token"] = csrf_token()
    return jsonify(admin)
