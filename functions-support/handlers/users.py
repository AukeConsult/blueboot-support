"""handlers/users.py — list teammates for case-assignment pickers.

Read-only. The board and case-detail pages need a list of "who can this case
be assigned to" — this reads the same settings/users/users/{email} docs that
handlers/shared._get_user_role() already uses, just listed instead of looked
up one at a time. No mailbox credentials live here, so this is safe at the
default GET floor (any signed-in role >= user).
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify

from handlers.shared import _err, _get_db

bp = Blueprint("users", __name__)


def _db():
    return getattr(g, "db", None) or _get_db()


_ASSIGNABLE_ROLES = ("user", "campaign-user", "admin")


@bp.route("/api/support/users", methods=["GET"])
def list_users():
    """List every teammate who has a role assigned (for assignment dropdowns)."""
    try:
        db = _db()
        docs = list(
            db.collection("settings")
              .document("users")
              .collection("users")
              .stream()
        )
        users = []
        for d in docs:
            data = d.to_dict() or {}
            role = (data.get("role") or "").strip()
            if role not in _ASSIGNABLE_ROLES:
                continue  # skip guests / docs with no valid role
            users.append({"email": d.id, "role": role})
        users.sort(key=lambda u: u["email"])
        return jsonify({"users": users})
    except Exception as exc:
        return _err(str(exc), 500)
