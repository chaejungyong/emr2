import os
import time

import pymysql
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'db'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'app_db'),
}


def get_db():
    return pymysql.connect(
        **DB_CONFIG,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def wait_for_db(retries=20, delay=3):
    for i in range(retries):
        try:
            get_db().close()
            print("DB connected.")
            return True
        except Exception as e:
            print(f"Waiting for DB... ({i + 1}/{retries}) Err: {e}")
            time.sleep(delay)
    return False


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/health')
def health():
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({"status": "ok", "db": db_ok})


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG') == '1'
    # The reloader re-executes this module; only wait for the DB once.
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        wait_for_db()
    app.run(host='0.0.0.0', port=5000, debug=debug)
