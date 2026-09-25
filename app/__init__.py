from flask import Flask, jsonify, request, send_from_directory
from pymysql.err import IntegrityError

from .config import Config
from .json_provider import ISOJSONProvider


def create_app(config_object=Config):
    app = Flask(__name__, static_folder="../static", static_url_path="/static")
    app.json = ISOJSONProvider(app)
    app.config.from_object(config_object)
    config_object.validate()

    app.config["XRAY_ROOT"].mkdir(parents=True, exist_ok=True)
    app.config["KNOWLEDGE_ROOT"].mkdir(parents=True, exist_ok=True)

    from . import db

    db.init_app(app)
    if app.config.get("RUN_MIGRATIONS", True):
        db.run_migrations(app)

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .auth import init_admin, install_guards
    from .clinical import bp as clinical_bp
    from .soap import bp as soap_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(clinical_bp)
    app.register_blueprint(soap_bp)
    app.register_blueprint(admin_bp)
    install_guards(app)
    init_admin(app)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        try:
            row = db.fetch_one("SELECT 1 AS ok")
            database_ok = row["ok"] == 1
        except Exception:
            database_ok = False
        status = "ok" if database_ok else "degraded"
        return jsonify({"status": status, "db": database_ok}), (
            200 if database_ok else 503
        )

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(_error):
        return jsonify({"error": "conflict", "message": "중복되거나 참조 중인 값입니다."}), 409

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": "request_too_large"}), 413

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not_found"}), 404
        return _error

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/") or request.path == "/" or request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
