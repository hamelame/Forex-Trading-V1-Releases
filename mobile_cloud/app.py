"""Production WSGI bridge for the mobile-only Forex AI 2.9.2.6 PAPER engine.

Render's existing Gunicorn command imports `app` from mobile_cloud/.
The PC desktop app and settings are not started or modified.
"""
import hmac
import json
import logging
import os
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mobile.server import MobileRuntime, WEB, MAX_BODY, clean_value

# Reuse the credential from the older mobile service without showing it.
TOKEN = (os.getenv("FX_MOBILE_TOKEN") or os.getenv("MOBILE_ACCESS_TOKEN") or "").strip()
if len(TOKEN) < 16:
    raise RuntimeError("Set FX_MOBILE_TOKEN or MOBILE_ACCESS_TOKEN to 16+ chars in Render")

# Keep historical learning paths rooted in the isolated mobile checkout.
os.chdir(ROOT)

# Opt-in durable storage for PAID Render services with a mounted disk.
# Only an actual mount protects state from restarts. Refuse to pretend that
# /var/data exists as durable storage if the operator forgot to attach a disk.
def configure_mobile_storage():
    disk = os.getenv("FX_MOBILE_STORAGE_DIR", "").strip()
    if not disk:
        os.environ.setdefault("FX_MOBILE_DB", "/tmp/fx_mobile_v2926.sqlite")
        os.environ.setdefault("FX_MOBILE_SETTINGS", "/tmp/fx_mobile_settings_v2926.json")
        return False
    directory = Path(disk)
    if not directory.is_absolute() or not directory.is_dir() or not os.path.ismount(str(directory)):
        raise RuntimeError("FX_MOBILE_STORAGE_DIR must be an existing mounted persistent disk, e.g. /var/data")
    for key, filename in (("FX_MOBILE_DB", "fx_mobile.sqlite"),
                          ("FX_MOBILE_SETTINGS", "fx_mobile_settings.json")):
        candidate = Path(os.getenv(key, str(directory / filename)))
        if not candidate.is_absolute() or not candidate.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError(f"{key} must be located inside the persistent disk")
        os.environ[key] = str(candidate)
    return True

STORAGE_PERSISTENT = configure_mobile_storage()
# Gunicorn may import this module in its parent/preload process. NEVER
# create MobileRuntime on import: its scanner + provider threads would then
# disappear after fork, leaving the engine lock apparently held forever.
# Resolve the runtime only from the serving worker, on its first authorized
# API request. Keep process identity to defend against alternate preloaders.
runtime = None
runtime_pid = None
runtime_init_lock = threading.Lock()

def get_runtime():
    global runtime, runtime_pid
    pid = os.getpid()
    if runtime is not None and runtime_pid == pid:
        return runtime
    with runtime_init_lock:
        if runtime is None or runtime_pid != pid:
            runtime = MobileRuntime()
            runtime_pid = pid
            logging.warning(
                "Mobile PAPER engine started inside serving process pid=%s; scanner_alive=%s watchdog_alive=%s",
                pid, bool(runtime.worker and runtime.worker.is_alive()),
                bool(runtime.watchdog and runtime.watchdog.is_alive())
            )
        return runtime

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY


def allowed():
    header = request.headers.get("Authorization", "")
    return hmac.compare_digest(header.encode("utf-8"), ("Bearer " + TOKEN).encode("utf-8"))


@app.after_request
def headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "object-src 'none'; frame-ancestors 'none'"
    )
    return resp


@app.route("/health")
def health():
    return jsonify(ok=True, service="fx-ai-mobile", version="2.9.2.6", paper_only=True)


@app.route("/api/state")
def state():
    if not allowed():
        return jsonify(error="Access token required"), 401
    try:
        return jsonify(clean_value(get_runtime().state()))
    except Exception:
        logging.exception("State API failure")
        return jsonify(error="Internal server error"), 500


@app.route("/api/candles")
def candles():
    if not allowed():
        return jsonify(error="Access token required"), 401
    try:
        return jsonify(clean_value(get_runtime().candles(request.args.get("symbol", ""))))
    except (KeyError, ValueError) as e:
        return jsonify(error=str(e)), 400
    except Exception:
        logging.exception("Candles API failure")
        return jsonify(error="Internal server error"), 500


@app.route("/api/replay")
def replay():
    if not allowed():
        return jsonify(error="Access token required"), 401
    trade_id = request.args.get("trade_id", "")
    if len(trade_id) > 100:
        return jsonify(error="Trade ID too long"), 400
    try:
        return jsonify(clean_value(get_runtime().replay(trade_id)))
    except (KeyError, ValueError) as e:
        return jsonify(error=str(e)), 400
    except Exception:
        logging.exception("Replay API failure")
        return jsonify(error="Internal server error"), 500


@app.route("/api/command/<name>", methods=["POST"])
def command(name):
    if not allowed():
        return jsonify(error="Access token required"), 401
    if request.mimetype != "application/json":
        return jsonify(error="JSON required"), 415
    values = request.get_json(silent=True)
    if not isinstance(values, dict):
        return jsonify(error="JSON object expected"), 400
    try:
        return jsonify(clean_value(get_runtime().command(name, values)))
    except (KeyError, ValueError) as e:
        return jsonify(error=str(e)), 400
    except Exception:
        logging.exception("Command API failure")
        return jsonify(error="Internal server error"), 500


@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/<path:asset>")
def asset(asset):
    if asset not in ("app.js", "style.css", "icon.svg", "manifest.webmanifest"):
        return jsonify(error="Not found"), 404
    return send_from_directory(WEB, asset)
