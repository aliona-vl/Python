# backend/api/staff/views.py
from flask import Blueprint, request, jsonify
from ...core.db import query, execute
from ...core.security import require_auth
from flask import g

bp = Blueprint("staff", __name__)

def _ok(**data): return jsonify({"ok": True, "status": "success", "data": data})
def _err(msg, http=400, code="ERROR"):
    return jsonify({"ok": False, "status": "error", "error": {"code": code, "message": msg}}), http

@bp.get("")
@require_auth(["admin", "manager"])
def list_staff():
    rows = query("""
      SELECT id, full_name, email, role, COALESCE(is_active, TRUE) AS is_active
        FROM users
       WHERE role IN ('admin','manager')
       ORDER BY role DESC, full_name NULLS LAST, email
    """)
    return _ok(items=rows)

@bp.put("/<int:user_id>")
@require_auth(["admin", "manager"])
def update_staff(user_id: int):
    body = request.get_json(silent=True) or request.form.to_dict()
    # Aktiv/Inaktiv schalten
    if "is_active" in body:
        is_active = str(body["is_active"]).lower() in ("1","true","yes","on")
        if user_id == getattr(g, "user_id", None) and not is_active:
            return _err("Du kannst dich nicht selbst deaktivieren", 409, "SELF_BLOCK")
        execute("UPDATE users SET is_active=%s WHERE id=%s", (is_active, user_id))
        return _ok(id=user_id, is_active=is_active)
    # Rolle ändern (nur Admin darf)
    if "role" in body:
        if getattr(g, "role", "") != "admin":
            return _err("Nur Admin darf Rollen ändern", 403, "FORBIDDEN")
        role = (body["role"] or "").strip().lower()
        if role not in ("admin","manager"):
            return _err("Ungültige Rolle", 400, "BAD_ROLE")
        execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
        return _ok(id=user_id, role=role)
    return _err("Keine Änderungen angegeben", 400, "NO_CHANGE")
