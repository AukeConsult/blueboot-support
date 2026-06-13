"""handlers/shared.py — shared infrastructure for all support handler Blueprints."""
from __future__ import annotations

import os
import sys
import threading

import firebase_admin
from firebase_admin import credentials, firestore as fs
from flask import jsonify

GCP_PROJECT = os.getenv("GCP_PROJECT", "blueboot-market")

# ── Firestore singleton (double-checked locking) ──────────────────────────────

_fb_lock = threading.Lock()
_db = None


def _get_db():
    global _db
    if _db is not None:
        return _db
    with _fb_lock:
        if _db is not None:
            return _db
        if not firebase_admin._apps:
            # Local dev: load credentials dict from blueboot-support.secrets.py
            # Production (Cloud Functions): falls back to ApplicationDefault
            try:
                _secrets_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "..", "..", "blueboot-support.secrets.py")
                )
                _cfg: dict = {}
                with open(_secrets_path, encoding="utf-8") as _f:
                    exec(compile(_f.read(), _secrets_path, "exec"), _cfg)
                cred = credentials.Certificate(_cfg["FIREBASE_CREDENTIALS"])
            except Exception:
                cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred, {"projectId": GCP_PROJECT})
        _db = fs.client()
    return _db


# ── Role helpers ──────────────────────────────────────────────────────────────

ROLE_LEVELS: dict[str, int] = {
    "guest":         0,
    "user":          1,
    "campaign-user": 2,
    "admin":         3,
}

_VALID_ROLES = set(ROLE_LEVELS)


def _get_user_role(db, email: str) -> str:
    """Return the user role from Firestore (same path as CRM).
    Falls back to 'guest' when the user doc is missing.
    """
    if not email:
        return "guest"
    try:
        doc = (
            db.collection("settings")
              .document("users")
              .collection("users")
              .document(email.strip().lower())
              .get()
        )
        if doc.exists:
            role = (doc.to_dict() or {}).get("role", "").strip()
            if role in _VALID_ROLES:
                return role
    except Exception:
        pass
    return "guest"


# ── Response helpers ──────────────────────────────────────────────────────────

def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _ok(msg: str = "ok", **kwargs):
    return jsonify({"status": "ok", "message": msg, **kwargs})
