from flask import Blueprint, jsonify, request, redirect, url_for, make_response, session
from ...core.db import query, execute
from ...core.security import make_token, get_payload_from_request
import secrets, datetime as dt

bp = Blueprint("qr", __name__)

def _err(msg, http=400):
    return jsonify({"ok": False, "status":"error", "error":{"code":"BAD_QR","message":msg}}), http

# --------- Helpers ---------------------------------------------------------

def _current_role() -> str | None:
    """Rolle aus Session -> Cookie -> JWT lesen (in dieser Reihenfolge)."""
    u = session.get("user")
    if u and u.get("role"):
        return str(u["role"]).lower()

    role = (request.cookies.get("zr_role") or "").lower()
    if role:
        return role

    p = get_payload_from_request() or {}
    r = p.get("role")
    return r.lower() if r else None

def _is_manager() -> bool:
    return _current_role() in ("admin", "manager")

# --------- QR erzeugen (nur Manager/Admin) ---------------------------------

@bp.post("/create")
def create_qr():
    if not _is_manager():
        return _err("Nicht erlaubt", 403)

    b = request.get_json(silent=True) or request.form.to_dict()
    project_id = int(b.get("project_id") or 0)
    if project_id <= 0:
        return _err("project_id fehlt")

    hours = int((b.get("expires_hours") or 0) or 0)
    uses  = b.get("uses_left")
    uses  = int(uses) if (uses and str(uses).isdigit()) else None

    token = secrets.token_urlsafe(24)
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)) if hours > 0 else None

    # qr_tokens existiert (ensure_min_schema)
    execute(
        """INSERT INTO qr_tokens(token, project_id, role, expires_at, uses_left)
           VALUES (%s,%s,'montager',%s,%s)""",
        (token, project_id, expires_at, uses),
    )

    return jsonify({
        "ok": True,
        "status": "success",
        "data": {
            "token": token,
            "url": url_for("qr.access_qr", token=token, _external=True)
        }
    })

# --------- QR aufrufen: Cookie setzen + zur QR-Seite leiten ----------------

@bp.get("/go/<token>")
def access_qr(token):
    row = query(
        "SELECT project_id, expires_at, uses_left FROM qr_tokens WHERE token=%s",
        (token,), one=True
    )
    if not row:
        return _err("Token ungültig", 404)
    if row["expires_at"] and row["expires_at"] < dt.datetime.now(dt.timezone.utc):
        return _err("Token abgelaufen", 400)
    if row["uses_left"] is not None and row["uses_left"] <= 0:
        return _err("Token aufgebraucht", 400)
    if row["uses_left"] is not None:
        execute("UPDATE qr_tokens SET uses_left=uses_left-1 WHERE token=%s", (token,))

    # JWT (Rolle=montager) + zusätzlich Projekt-ID als nicht-httponly Cookie für die UI
    jwt_token = make_token(user_id=0, role="montager")
    resp = make_response(redirect(f"/qr_install.html?project_id={row['project_id']}"))
    resp.set_cookie("token", jwt_token, httponly=True,  samesite="Lax", secure=False, path="/")
    resp.set_cookie("qr_project_id", str(row["project_id"]), httponly=False, samesite="Lax", secure=False, path="/")
    return resp

# --------- Installationseintrag speichern ----------------------------------

@bp.post("/submit_installation")
def submit_installation():
    data = request.get_json(silent=True) or request.form
    pid  = int((data.get("project_id") or data.get("projekt_id") or 0))
    name = (data.get("mitarbeiter") or "").strip()
    datum_str = (data.get("datum") or "").strip()
    mins = int(data.get("minuten") or 0)

    if pid <= 0 or not name or mins <= 0:
        return jsonify(ok=False, message="project_id, mitarbeiter, minuten sind Pflicht"), 400

    try:
        from datetime import date
        mont_date = date.fromisoformat(datum_str) if datum_str else None
    except ValueError:
        return jsonify(ok=False, message="Datum muss YYYY-MM-DD sein"), 400

    # Postgres: make_interval(mins => %s)
    execute("""
        INSERT INTO sitzungen
            (projekt_id, mitarbeiter, teilbereich,
             montage_datum, installation_mitarbeiter, zeit_installation,
             dauer_minuten, start_zeit, end_zeit)
        VALUES
            (%s, %s, 'installation',
             %s, %s, %s,
             %s, NOW(), NOW() + make_interval(mins => %s))
    """, (pid, name, mont_date, name, mins, mins, mins))

    return jsonify(ok=True)

# --------- Liste der letzten QR-Installations-Einträge ---------------------

@bp.get("/entries")
def list_entries():
    pid = request.args.get("project_id", type=int)
    if not pid:
        return jsonify({"ok": False, "error": {"message": "project_id fehlt"}}), 400

    rows = query("""
        SELECT
            id,
            COALESCE(montage_datum, start_zeit::date)         AS datum,
            COALESCE(installation_mitarbeiter, mitarbeiter)   AS name,
            COALESCE(zeit_installation, dauer_minuten)::int   AS minuten
        FROM sitzungen
        WHERE projekt_id = %s
          AND LOWER(teilbereich) = 'installation'
        ORDER BY COALESCE(end_zeit, start_zeit, NOW()) DESC
        LIMIT 50
    """, (pid,))

    data = [{
        "id": r["id"],
        "datum": r["datum"].isoformat() if r["datum"] else None,
        "name": r["name"],
        "minuten": int(r["minuten"] or 0),
        "teilbereich": "installation"
    } for r in rows]
    return jsonify({"ok": True, "data": data})

@bp.get("/project")
def qr_project_info():
    pid = request.args.get("project_id", type=int)
    if not pid:
        return jsonify(ok=False, message="project_id fehlt"), 400
    rows = query("SELECT id, name, kunde FROM projekte WHERE id=%s", (pid,))
    if not rows:
        return jsonify(ok=False, message="Projekt nicht gefunden"), 404
    r = rows[0]
    return jsonify(ok=True, data={"id": r["id"], "name": r["name"], "kunde": r["kunde"]})
