# backend/api/manager/views.py
from __future__ import annotations
from flask import Blueprint, request, jsonify, make_response
from datetime import datetime, timezone, timedelta
from ...core.db import query, execute
from ...core.security import require_auth


bp = Blueprint("manager", __name__)

bp_compat = Blueprint("manager_compat", __name__)

def ok(**data): return jsonify({"ok": True, "status": "success", **data})
def err(msg, http=400, code="ERROR"):
    return jsonify({"ok": False, "status": "error", "error": {"code": code, "message": msg}}), http

TZ = timezone.utc
TEILBEREICHE = ("besprechung", "zeichnung", "aufmass", "anderes")

def _ensure_tables():

    execute("""
    CREATE TABLE IF NOT EXISTS time_entries(
      id           BIGSERIAL PRIMARY KEY,
      project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      user_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
      worker_name  TEXT,
      activity     TEXT, -- alias für typ
      typ          TEXT   NOT NULL,
      minutes      INTEGER NOT NULL DEFAULT 0,
      started_at   TIMESTAMPTZ,
      ended_at     TIMESTAMPTZ,
      comment      TEXT
    );""")
    
    execute("""
    CREATE TABLE IF NOT EXISTS active_sessions(
      id           BIGSERIAL PRIMARY KEY,
      project_id   BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      typ          TEXT   NOT NULL,
      worker_name  TEXT,
      started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );""")

def _minutes(a: datetime, b: datetime) -> int:
    return max(1, int((b - a).total_seconds() // 60))



@bp.get("/projekte")
@require_auth(["manager", "admin"])
def projekte_list_api():
    return _projekte_list()

@bp_compat.get("/projekte")
@require_auth(["manager", "admin"])
def projekte_list_compat():
    return _projekte_list()

def _projekte_list():
    _ensure_tables()
    st = (request.args.get("status") or "").strip().lower()
    q  = (request.args.get("q") or "").strip().lower()

    where = ["1=1"]; params = []
    if st:
        where.append("p.status = %s"); params.append(st)
    if q:
        where.append("(LOWER(p.name) LIKE %s OR LOWER(c.name) LIKE %s)")
        params += [f"%{q}%", f"%{q}%"]

    rows = query(f"""
        SELECT p.id, p.name, p.status,
               COALESCE(SUM(te.minutes),0) AS total_minutes,
               c.name AS company_name
        FROM projects p
        LEFT JOIN time_entries te ON te.project_id = p.id
        LEFT JOIN companies c ON c.id = p.company_id
        WHERE {" AND ".join(where)}
        GROUP BY p.id, p.name, p.status, c.name
        ORDER BY p.status, p.created_at DESC NULLS LAST, p.id DESC
    """, tuple(params))
    return ok(items=rows)

@bp.post("/projekt/neu")
@require_auth(["manager", "admin"])
def projekt_neu_api():
    return _projekt_neu()

@bp_compat.post("/projekt/neu")
@require_auth(["manager", "admin"])
def projekt_neu_compat():
    return _projekt_neu()

def _projekt_neu():
    _ensure_tables()
    data = request.get_json(silent=True) or request.form.to_dict()
    name = (data.get("name") or "").strip()
    company_id = data.get("company_id")
    if not name:
        return err("Projektname erforderlich")
    if not company_id:
        return err("company_id erforderlich")
    row = query("""
        INSERT INTO projects(name, company_id, status, created_at)
        VALUES (%s,%s,'gestoppt', NOW())
        RETURNING id
    """, (name, company_id), one=True)
    return ok(id=row["id"])

@bp.post("/projekte/bulk-delete")
@require_auth(["manager", "admin"])
def projekte_bulk_delete_api():
    return _bulk_delete()

@bp_compat.post("/projekte/bulk-delete")
@require_auth(["manager", "admin"])
def projekte_bulk_delete_compat():
    return _bulk_delete()

def _bulk_delete():
    _ensure_tables()
    ids = (request.get_json(silent=True) or {}).get("projekt_ids") or []
    if not ids:
        return err("Keine Projekte ausgewählt")
    execute("DELETE FROM projects WHERE id = ANY(%s)", (ids,))
    return ok(deleted=len(ids))

@bp.post("/projekte/<int:pid>/beenden")
@require_auth(["manager", "admin"])
def projekt_beenden_api(pid: int):
    return _projekt_beenden(pid)

@bp_compat.post("/projekte/<int:pid>/beenden")
@require_auth(["manager", "admin"])
def projekt_beenden_compat(pid: int):
    return _projekt_beenden(pid)

def _projekt_beenden(pid: int):
    _ensure_tables()
    execute("""
        UPDATE projects
           SET status='beendet', completed_at=NOW()
         WHERE id=%s
    """, (pid,))
    return ok(id=pid, status="beendet")



@bp.post("/projekt/<int:pid>/aktivität/starten")
@require_auth(["manager", "admin"])
def aktiv_start_api(pid: int):
    return _aktiv_start(pid)

@bp_compat.post("/projekt/<int:pid>/aktivität/starten")
@require_auth(["manager", "admin"])
def aktiv_start_compat(pid: int):
    return _aktiv_start(pid)

def _aktiv_start(pid: int):
    _ensure_tables()
    data = request.get_json(silent=True) or request.form.to_dict()
    typ = (data.get("teilbereich") or data.get("typ") or "anderes").strip().lower()
    worker = (data.get("mitarbeiter") or data.get("worker_name") or "").strip() or None
    if typ not in TEILBEREICHE:
        return err("Ungültiger Teilbereich")
    
    execute("""
        INSERT INTO active_sessions(project_id, typ, worker_name, started_at)
        VALUES (%s,%s,%s, NOW())
    """, (pid, typ, worker))
    sid = query("SELECT currval(pg_get_serial_sequence('active_sessions','id')) AS id", one=True)["id"]
    return ok(id=sid)

@bp.post("/projekt/<int:pid>/aktivität/beenden")
@require_auth(["manager", "admin"])
def aktiv_stop_api(pid: int):
    return _aktiv_stop(pid)

@bp_compat.post("/projekt/<int:pid>/aktivität/beenden")
@require_auth(["manager", "admin"])
def aktiv_stop_compat(pid: int):
    return _aktiv_stop(pid)

def _aktiv_stop(pid: int):
    _ensure_tables()
    data = request.get_json(silent=True) or request.form.to_dict()
    worker = (data.get("mitarbeiter") or data.get("worker_name") or "").strip() or None

    sess = query("""
        SELECT * FROM active_sessions
         WHERE project_id=%s AND (%s IS NULL OR worker_name=%s)
         ORDER BY started_at DESC
         LIMIT 1
    """, (pid, worker, worker), one=True)
    if not sess:
        return err("Keine laufende Aktivität gefunden", 404)

    started = sess["started_at"]
    ended   = datetime.now(TZ)
    mins    = _minutes(started, ended)

    execute("""
        INSERT INTO time_entries(project_id, typ, activity, minutes, started_at, ended_at, worker_name)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (pid, sess["typ"], sess["typ"], mins, started, ended, sess["worker_name"]))
    execute("DELETE FROM active_sessions WHERE id=%s", (sess["id"],))
    return ok(dauer_min=mins)

@bp.post("/projekt/<int:pid>/manuell")
@require_auth(["manager", "admin"])
def aktiv_manuell_api(pid: int):
    return _aktiv_manuell(pid)

@bp_compat.post("/projekt/<int:pid>/manuell")
@require_auth(["manager", "admin"])
def aktiv_manuell_compat(pid: int):
    return _aktiv_manuell(pid)

def _aktiv_manuell(pid: int):
    _ensure_tables()
    data = request.get_json(silent=True) or request.form.to_dict()
    typ = (data.get("typ") or data.get("teilbereich") or "anderes").strip().lower()
    mins = int(data.get("dauer_min") or data.get("minutes") or 0)
    comment = (data.get("kommentar") or data.get("comment") or "").strip() or None
    if typ not in TEILBEREICHE: typ = "anderes"
    if mins <= 0: return err("Minuten > 0 erforderlich")

    now = datetime.now(TZ)
    execute("""
        INSERT INTO time_entries(project_id, typ, activity, minutes, started_at, ended_at, comment)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (pid, typ, typ, mins, now - timedelta(minutes=mins), now, comment))
    new_id = query("SELECT currval(pg_get_serial_sequence('time_entries','id')) AS id", one=True)["id"]
    return ok(id=new_id)



def _fmt_min(mins: int) -> str:
    mins = int(mins or 0)
    return f"{mins//60}h {mins%60}min" if mins >= 60 else f"{mins}min"

def _teilbereich_sum(pid: int) -> dict:
    sums = {}
    for t in ("besprechung","zeichnung","aufmass","anderes"):
        r = query("SELECT COALESCE(SUM(minutes),0) AS m FROM time_entries WHERE project_id=%s AND typ=%s",
                  (pid, t), one=True)
        sums[t] = int(r["m"] or 0)
    return sums

@bp.get("/projekt/<int:pid>/bericht")
@require_auth(["manager", "admin"])
def report_project_api(pid: int):
    return _report_project(pid)

@bp_compat.get("/projekt/<int:pid>/bericht")
@require_auth(["manager", "admin"])
def report_project_compat(pid: int):
    return _report_project(pid)

def _report_project(pid: int):
    _ensure_tables()
    p = query("""
        SELECT p.id, p.name, p.status, c.name AS company
          FROM projects p
          LEFT JOIN companies c ON c.id=p.company_id
         WHERE p.id=%s
    """, (pid,), one=True)
    if not p: return err("Projekt nicht gefunden", 404)
    parts = _teilbereich_sum(pid); total = sum(parts.values())

    html = f"""<!doctype html><meta charset="utf-8">
    <title>Bericht – {p['name']}</title>
    <style>
      body{{font:14px system-ui,Segoe UI,Arial; margin:20px}}
      .card{{border:1px solid #ddd;border-radius:10px;padding:14px;margin:10px 0}}
      .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}
      .pill{{display:inline-block;background:#1976d2;color:#fff;padding:4px 10px;border-radius:999px;font-weight:700}}
      @media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
    </style>
    <h2>📄 Projektbericht</h2>
    <div class="card"><b>{p['name']}</b><br>Unternehmen: {p.get('company') or '–'}<br>Status: <span class="pill">{p['status']}</span></div>
    <div class="card grid">
      <div>🗣️ Besprechung<br><b>{_fmt_min(parts['besprechung'])}</b></div>
      <div>✏️ Zeichnung<br><b>{_fmt_min(parts['zeichnung'])}</b></div>
      <div>📏 Aufmaß<br><b>{_fmt_min(parts['aufmass'])}</b></div>
    </div>
    <div class="card">⏱️ Gesamt: <b>{_fmt_min(total)}</b></div>
    <p><button onclick="print()">🖨️ Drucken</button></p>
    """
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})

@bp.get("/gesamt-bericht")
@require_auth(["manager", "admin"])
def report_summary_api():
    return _report_summary()

@bp_compat.get("/gesamt-bericht")
@require_auth(["manager", "admin"])
def report_summary_compat():
    return _report_summary()

def _report_summary():
    _ensure_tables()
    von = (request.args.get("von") or "").strip()
    bis = (request.args.get("bis") or "").strip()
    today = datetime.now(TZ).date().isoformat()
    if not bis: bis = today
    if not von:
        d = datetime.now(TZ).date().replace(day=1)
        von = d.isoformat()

    rows = query("""
      SELECT p.id, p.name, p.status, c.name AS company,
             COALESCE(SUM(te.minutes),0) AS total
        FROM projects p
        LEFT JOIN companies c ON c.id=p.company_id
        LEFT JOIN time_entries te
          ON te.project_id=p.id
         AND te.started_at::date BETWEEN %s AND %s
       GROUP BY p.id, p.name, p.status, c.name
       ORDER BY p.status, p.name
    """, (von, bis))

    def block(pid:int):
        parts = _teilbereich_sum(pid)
        return (f"<div>🗣️ {_fmt_min(parts['besprechung'])}</div>"
                f"<div>✏️ {_fmt_min(parts['zeichnung'])}</div>"
                f"<div>📏 {_fmt_min(parts['aufmass'])}</div>")

    total_all = sum(int(r["total"] or 0) for r in rows)
    body = ["<!doctype html><meta charset='utf-8'><title>Gesamt-Bericht</title>",
            "<style>body{font:14px system-ui;margin:20px} .card{border:1px solid #ddd;border-radius:10px;padding:12px;margin:10px 0} .row{display:flex;gap:8px;justify-content:space-between;flex-wrap:wrap} .pill{background:#1976d2;color:#fff;border-radius:999px;padding:2px 8px}</style>",
            f"<h2>📊 Gesamt-Bericht <small>({von} – {bis})</small></h2>",
            f"<div class='card'>⏱️ Gesamtzeit: <b>{_fmt_min(total_all)}</b> · Projekte: {len(rows)}</div>"]
    for r in rows:
        body.append("<div class='card'>")
        body.append(f"<div class='row'><div><b>{r['name']}</b><br><small>{r.get('company') or '–'}</small></div><div class='pill'>{r['status']}</div></div>")
        body.append(f"<div class='row'>{block(r['id'])}<div><b>Σ {_fmt_min(int(r['total'] or 0))}</b></div></div>")
        body.append("</div>")
    body.append("<p><button onclick='print()'>🖨️ Drucken</button></p>")
    html = "".join(body)
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})
