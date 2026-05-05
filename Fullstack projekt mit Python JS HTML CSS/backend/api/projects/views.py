from flask import Blueprint, request, jsonify
from ...core.db import query, execute
from ...core.security import require_auth
from psycopg2.errors import ForeignKeyViolation

bp = Blueprint("projects", __name__)

def ok(data=None, **kw): return jsonify({"ok": True, "data": data or kw})
def err(code, msg, http=400): return jsonify({"ok": False, "error": {"code": code, "message": msg}}), http

# Companies (für Admin/Manager UI)
@bp.get("/companies")
@require_auth(roles=["admin","manager"])
def companies_list():
    rows = query("SELECT id, name FROM companies ORDER BY name ASC")
    return ok(companies=rows)

@bp.post("/companies")
@require_auth(roles=["admin","manager"])
def companies_add():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name: return err("BAD_INPUT","Name fehlt")
    try:
        execute("INSERT INTO companies(name) VALUES (%s)", (name,))
    except Exception:
        return err("DUPLICATE","Firma existiert bereits")
    row = query("SELECT id, name FROM companies WHERE name=%s", (name,), one=True)
    return ok(company=row)

@bp.delete("/companies/<int:c_id>")
@require_auth(roles=["admin","manager"])
def companies_del(c_id):
    try:
        execute("DELETE FROM companies WHERE id=%s", (c_id,))
    except ForeignKeyViolation:
        return err("IN_USE","Firma wird von Projekten verwendet")
    return ok()

# Projects
@bp.post("/")
@require_auth(roles=["admin","manager"])
def create_project():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    company_id = body.get("company_id")
    if not name or not company_id: return err("BAD_INPUT","Name und company_id erforderlich")
    execute("INSERT INTO projects(name, company_id, status) VALUES (%s,%s,'aktiv')", (name, company_id))
    row = query("""SELECT p.id, p.name, p.status, c.name AS company
                   FROM projects p JOIN companies c ON c.id=p.company_id
                   WHERE p.name=%s AND p.company_id=%s
                   ORDER BY p.id DESC LIMIT 1""", (name, company_id), one=True)
    return ok(project=row)

@bp.get("/")
@require_auth(roles=["admin","manager"])
def list_projects():
    status = (request.args.get("status") or "").strip()
    q = "%"+(request.args.get("q") or "").strip().lower()+"%"
    where = "WHERE 1=1"
    params = []
    if status in ("aktiv","pause","beendet"):
        where += " AND p.status=%s"; params.append(status)
    where += " AND (LOWER(p.name) LIKE %s OR LOWER(c.name) LIKE %s)"
    params += [q, q]
    rows = query(f"""SELECT p.id, p.name, p.status, p.created_at,
                            c.name AS company
                     FROM projects p JOIN companies c ON c.id=p.company_id
                     {where}
                     ORDER BY p.id DESC""", params)
    return ok(projects=rows)

@bp.get("/<int:p_id>")
@require_auth(roles=["admin","manager","worker"])
def project_detail(p_id):
    proj = query("""SELECT p.id, p.name, p.status, p.created_at, p.started_at, p.completed_at,
                           c.name AS company
                    FROM projects p JOIN companies c ON c.id=p.company_id
                    WHERE p.id=%s""", (p_id,), one=True)
    if not proj: return err("NOT_FOUND","Projekt nicht gefunden",404)
    return ok(project=proj)

@bp.post("/<int:p_id>/end")
@require_auth(roles=["admin","manager"])
def project_end(p_id):
    execute("UPDATE projects SET status='beendet', completed_at=NOW() WHERE id=%s", (p_id,))
    return ok()
