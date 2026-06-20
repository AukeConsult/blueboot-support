"""handlers/saved_filters.py — per-user saved board filter combinations.

Each signed-in user can save a named filter (status/priority/account/tags/
mine) for one-click reuse on the board. Stored as a `saved_filters` array on
the same settings/users/users/{email} doc handlers/shared._get_user_role()
already reads — this is the user's own document, so no admin role is
required to write it (unlike mailbox credentials or other settings/* data).
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from handlers.shared import _err, _get_db

bp = Blueprint("saved_filters", __name__)


def _db():
    return getattr(g, "db", None) or _get_db()


_MAX_SAVED_FILTERS = 25
_ALLOWED_FIELDS = {"status", "priority", "account", "tags", "mine"}


def _user_doc(db):
    email = getattr(g, "user_email", "")
    return db.collection("settings").document("users").collection("users").document(email)


# ── GET /api/support/saved-filters ───────────────────────────────────────────

@bp.route("/api/support/saved-filters", methods=["GET"])
def list_saved_filters():
    """Return the signed-in user's saved filter combinations."""
    try:
        doc = _user_doc(_db()).get()
        data = doc.to_dict() if doc.exists else {}
        return jsonify({"filters": (data or {}).get("saved_filters", [])})
    except Exception as exc:
        return _err(str(exc), 500)


# ── PUT /api/support/saved-filters ───────────────────────────────────────────

@bp.route("/api/support/saved-filters", methods=["PUT"])
def replace_saved_filters():
    """Replace the signed-in user's full list of saved filters.

    Body: {"filters": [{"name": "...", "status": "...", "priority": "...",
    "account": "...", "tags": "...", "mine": bool}, ...]}. The frontend
    sends the whole list each time (add/remove/rename all happen client
    side first) — simpler than per-filter CRUD for a list this small.
    """
    try:
        body = request.get_json(silent=True) or {}
        filters = body.get("filters")
        if not isinstance(filters, list):
            return _err("filters must be a list", 400)
        if len(filters) > _MAX_SAVED_FILTERS:
            return _err(f"Too many saved filters — max {_MAX_SAVED_FILTERS}", 400)

        cleaned = []
        for f in filters:
            if not isinstance(f, dict) or not (f.get("name") or "").strip():
                continue
            entry = {"name": str(f["name"]).strip()[:60]}
            for key in _ALLOWED_FIELDS:
                if f.get(key):
                    entry[key] = f[key]
            cleaned.append(entry)

        _user_doc(_db()).set({"saved_filters": cleaned}, merge=True)
        return jsonify({"status": "ok", "filters": cleaned})
    except Exception as exc:
        return _err(str(exc), 500)
