"""functions-support/main.py — Support API entry point.

Exposes one Cloud Function: supportApi (30 s timeout).
All business logic lives in handlers/*.py and support_mail/*.py.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from firebase_functions import https_fn, options as fn_options
from flask import Flask, g, jsonify, request as req
from flask_cors import CORS

from handlers.shared import _err, _get_db, _get_user_role, ROLE_LEVELS

# ── Flask app ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

# ── Register Blueprints ───────────────────────────────────────────────────────

from handlers.cases         import bp as cases_bp
from handlers.mail_check    import bp as mail_check_bp
from handlers.sla_check     import bp as sla_check_bp
from handlers.channels      import bp as channels_bp
from handlers.users         import bp as users_bp
from handlers.saved_filters import bp as saved_filters_bp

for bp in (cases_bp, mail_check_bp, sla_check_bp, channels_bp, users_bp, saved_filters_bp):
    app.register_blueprint(bp)

# ── Minimum role per blueprint for mutating requests ─────────────────────────

_BLUEPRINT_MIN_ROLES: dict[str, str] = {
    "cases":      "campaign-user",
    "mail_check": "campaign-user",
    "sla_check":  "campaign-user",
    # GET is read-only for any signed-in user; POST/PATCH/DELETE (channel
    # create/edit/delete, which include mailbox credentials) are additionally
    # gated to role=admin inside handlers/channels.py (_require_admin), since
    # campaign-user is too broad for write access to mailbox passwords.
    "channels":   "campaign-user",
}

# ── Auth middleware ───────────────────────────────────────────────────────────

# Paths that bypass user auth (called by Cloud Scheduler via service account)
_SERVICE_PATHS = {"/check-mail", "/check-sla"}


@app.before_request
def check_auth():
    import firebase_admin.auth as _fb_auth
    import logging as _log

    if req.method == "OPTIONS":
        return  # CORS preflight

    # Service-account-only paths (scheduler calls)
    if req.path.rstrip("/") in _SERVICE_PATHS:
        return

    db = _get_db()

    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _err("Sign in required", 401)

    try:
        decoded    = _fb_auth.verify_id_token(auth_header[7:])
        user_email = decoded.get("email", "").strip().lower()
    except _fb_auth.ExpiredIdTokenError:
        return _err("Session expired — please sign in again", 401)
    except (_fb_auth.InvalidIdTokenError, Exception):
        return _err("Sign in required", 401)

    role = _get_user_role(db, user_email)
    _log.info(f"[auth] role={role}  user={user_email}  {req.method} {req.path}")

    g.user_email = user_email
    g.user_role  = role
    g.db         = db

    endpoint  = req.endpoint or ""
    blueprint = endpoint.split(".")[0] if "." in endpoint else None

    # Guests blocked everywhere (support is internal-only)
    if ROLE_LEVELS.get(role, 0) < ROLE_LEVELS["user"]:
        return _err("Access denied", 403)

    # Mutating requests require campaign-user minimum
    if req.method not in ("GET", "HEAD", "OPTIONS"):
        min_role  = _BLUEPRINT_MIN_ROLES.get(blueprint or "", "campaign-user")
        min_level = ROLE_LEVELS.get(min_role, 2)
        if ROLE_LEVELS.get(role, 0) < min_level:
            return _err(f"Requires role: {min_role}", 403)


# ── GET /api/support/me — current user's email + role ────────────────────────
# Lets the frontend show/hide admin-only UI (e.g. the channel management
# link) without guessing from a failed write call.

@app.route("/api/support/me", methods=["GET"])
def whoami():
    return jsonify({"email": g.user_email, "role": g.user_role})


# ── Cloud Function entry point ────────────────────────────────────────────────

@https_fn.on_request(region="us-central1", timeout_sec=30)
def supportApi(req: https_fn.Request) -> https_fn.Response:
    with app.request_context(req.environ):
        try:
            return app.full_dispatch_request()
        except Exception as exc:
            return _err(str(exc), 500)
