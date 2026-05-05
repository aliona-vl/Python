from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    render_template_string,
    request,
)


from ...core.db import query as db_query, execute as db_exec


bp = Blueprint("kiosk", __name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = PROJECT_ROOT / "frontend"



def ensure_kiosk_schema() -> None:
    
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS mitarbeiter_fertigung (
            id         BIGSERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        (),
    )

    db_exec(
        "ALTER TABLE mitarbeiter_fertigung "
        "ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;",
        (),
    )

    
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS sitzungen_fertigung (
            id            BIGSERIAL PRIMARY KEY,
            projekt_id    BIGINT NOT NULL,
            teilbereich   TEXT   NOT NULL,
            mitarbeiter   TEXT   NOT NULL,
            start_zeit    TIMESTAMPTZ NOT NULL,
            end_zeit      TIMESTAMPTZ,
            dauer_minuten INTEGER NOT NULL DEFAULT 0 CHECK (dauer_minuten >= 0),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        (),
    )

    db_exec(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='sitzungen_fertigung' AND column_name='project_id'
          )
          AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='sitzungen_fertigung' AND column_name='projekt_id'
          ) THEN
            EXECUTE 'ALTER TABLE sitzungen_fertigung RENAME COLUMN project_id TO projekt_id';
          END IF;
        END$$;
        """,
        (),
    )

    db_exec(
        "ALTER TABLE sitzungen_fertigung "
        "ADD COLUMN IF NOT EXISTS session_date DATE NOT NULL DEFAULT CURRENT_DATE;",
        (),
    )

    db_exec(
        """
        DO $$
        DECLARE
          fkname text;
        BEGIN
          SELECT conname INTO fkname
          FROM pg_constraint c
          JOIN pg_attribute a
               ON a.attrelid = c.conrelid
              AND a.attnum   = ANY(c.conkey)
          WHERE c.conrelid = 'sitzungen_fertigung'::regclass
            AND c.contype  = 'f'
            AND a.attname  = 'projekt_id'
          LIMIT 1;

          IF fkname IS NOT NULL THEN
            EXECUTE 'ALTER TABLE sitzungen_fertigung DROP CONSTRAINT ' || quote_ident(fkname);
          END IF;

          BEGIN
            ALTER TABLE sitzungen_fertigung
            ADD CONSTRAINT sitz_fert_proj_fk
            FOREIGN KEY (projekt_id) REFERENCES projekte(id) ON DELETE CASCADE;
          EXCEPTION WHEN duplicate_object THEN
            NULL;
          END;
        END$$;
        """,
        (),
    )


    db_exec(
        "CREATE INDEX IF NOT EXISTS idx_fert_proj      ON sitzungen_fertigung(projekt_id);",
        (),
    )
    db_exec(
        "CREATE INDEX IF NOT EXISTS idx_fert_proj_teil ON sitzungen_fertigung(projekt_id, teilbereich);",
        (),
    )
    db_exec(
        "CREATE INDEX IF NOT EXISTS idx_fert_date      ON sitzungen_fertigung(session_date);",
        (),
    )


ensure_kiosk_schema()


TEILBEREICHE: Dict[str, str] = {
    "schweissen": "Schweißen",
    "umformen": "Umformen",
    "zerspanung": "Zerspanung",
    "oberflaechenbehandlung": "Oberflächenbehandlung",
    "montagevorbereitung": "Montagevorbereitung",
    "sonstige": "Sonstige",
}
TB_KEYS: List[str] = list(TEILBEREICHE.keys())



@bp.get("/kiosk")
def kiosk_dashboard():
    rows = db_query(
        """
        SELECT
            p.id,
            p.name,
            p.kunde,
            COALESCE(
              SUM(CASE WHEN sf.id IS NOT NULL AND sf.end_zeit IS NULL THEN 1 ELSE 0 END),
              0
            )::int AS open_cnt,
            COALESCE(SUM(
              CASE
                WHEN sf.id IS NULL THEN 0
                WHEN sf.end_zeit IS NULL
                  THEN GREATEST(0, EXTRACT(EPOCH FROM (NOW() - sf.start_zeit)) / 60.0)
                ELSE sf.dauer_minuten
              END
            ), 0)::int AS gesamt_minuten
        FROM projekte p
        LEFT JOIN sitzungen_fertigung sf ON sf.projekt_id = p.id
        WHERE p.status IN ('aktiv','pausiert')
        GROUP BY p.id, p.name, p.kunde, p.erstellt_am
        ORDER BY p.erstellt_am DESC;
        """,
        (),
    )

    def _fmt_hhmm(mins: int) -> str:
        m = int(mins) if mins else 0
        return f"{m // 60}:{m % 60:02d}"

    projekte = []
    for r in rows:
        gm = int(r["gesamt_minuten"] or 0)
        oc = int(r["open_cnt"] or 0)
        projekte.append(
            {
                "id": r["id"],
                "name": r["name"],
                "kunde": r.get("kunde"),
                "fertigung_laeuft": oc > 0,
                "gesamt_minuten": gm,
                "gesamt_hhmm": _fmt_hhmm(gm),
            }
        )


    try:
        ma_rows = db_query(
            "SELECT name FROM mitarbeiter_fertigung WHERE active IS TRUE ORDER BY name",
            (),
        )
    except Exception:
        ma_rows = db_query("SELECT name FROM mitarbeiter_fertigung ORDER BY name", ())
    mitarbeiter = [m["name"] for m in ma_rows]

    return render_template("kiosk.html", projekte=projekte, mitarbeiter=mitarbeiter)


@bp.get("/kiosk/projekt/<int:pid>")
def kiosk_projekt(pid: int):
    pr = db_query("SELECT id, name, kunde FROM projekte WHERE id=%s", (pid,))
    if not pr:
        return "Projekt nicht gefunden", 404
    projekt = pr[0]

    # Mitarbeiter aus mitarbeiter_fertigung
    try:
        ma_rows = db_query(
            "SELECT name FROM mitarbeiter_fertigung WHERE active IS TRUE ORDER BY name",
            (),
        )
    except Exception:
        ma_rows = db_query("SELECT name FROM mitarbeiter_fertigung ORDER BY name", ())
    mitarbeiter = [r["name"] for r in ma_rows]

    offene = db_query(
        """
        SELECT id, mitarbeiter, teilbereich, start_zeit
        FROM sitzungen_fertigung
        WHERE projekt_id=%s AND end_zeit IS NULL
        ORDER BY start_zeit ASC;
        """,
        (pid,),
    )

    # Einträge mit formatiertem Datum und Dauer
    eintraege_raw = db_query(
        """
        SELECT id, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
        FROM sitzungen_fertigung
        WHERE projekt_id=%s
        ORDER BY COALESCE(end_zeit, start_zeit) DESC
        LIMIT 50;
        """,
        (pid,),
    )

   
    from datetime import datetime
    import pytz

    berlin_tz = pytz.timezone('Europe/Berlin')
    eintraege = []

    for e in eintraege_raw:
        # Datum formatieren
        dt = e["start_zeit"]
        if hasattr(dt, 'tzinfo') and dt.tzinfo:
            dt_berlin = dt.astimezone(berlin_tz)
        else:
            dt_berlin = dt

        datum_formatted = dt_berlin.strftime("%d.%m.%y")

        # Dauer formatieren
        mins = int(e["dauer_minuten"] or 0)
        h = mins // 60
        m = mins % 60
        dauer_formatted = f"{h}h {m}m" if h > 0 else f"{m}m"

        eintraege.append({
            "id": e["id"],
            "mitarbeiter": e["mitarbeiter"],
            "teilbereich": e["teilbereich"],
            "start_zeit": e["start_zeit"],
            "end_zeit": e["end_zeit"],
            "dauer_minuten": e["dauer_minuten"],
            "datum": datum_formatted,
            "dauer_text": dauer_formatted
        })

    html = (FRONTEND_DIR / "kiosk_projekt.html").read_text(encoding="utf-8")
    return render_template_string(
        html,
        projekt=projekt,
        mitarbeiter=mitarbeiter,
        offene=offene,
        eintraege=eintraege,
        teilbereiche=TEILBEREICHE,
    )


@bp.get("/kiosk/mitarbeiter/list")
def kiosk_ma_list():
    
    try:
        rows = db_query(
            "SELECT name FROM mitarbeiter_fertigung WHERE active IS TRUE ORDER BY name",
            (),
        )
    except Exception:
        rows = db_query("SELECT name FROM mitarbeiter_fertigung ORDER BY name", ())
    return jsonify(ok=True, data=[r["name"] for r in rows])


@bp.post("/kiosk/mitarbeiter/hinzufuegen")
def kiosk_ma_add():
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, message="Name fehlt"), 400

  
    existing = db_query(
        "SELECT 1 FROM mitarbeiter_fertigung WHERE name=%s LIMIT 1", (name,)
    )
    if existing:
        db_exec("UPDATE mitarbeiter_fertigung SET active=TRUE WHERE name=%s", (name,))
    else:
        db_exec(
            "INSERT INTO mitarbeiter_fertigung(name, active) VALUES (%s, TRUE)", (name,)
        )
    return jsonify(ok=True, name=name)


@bp.post("/kiosk/mitarbeiter/loeschen")
def kiosk_ma_del():
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, message="Name fehlt"), 400
    db_exec("UPDATE mitarbeiter_fertigung SET active=FALSE WHERE name=%s", (name,))
    return jsonify(ok=True, name=name)



def _to_minutes(val) -> int:
    """'90' → 90, '1:30' → 90, '01:05' → 65 …"""
    if val is None:
        return 0
    s = str(val).strip()
    if not s:
        return 0
    if ":" in s:
        hh, mm = s.split(":", 1)
        try:
            return max(0, int(hh) * 60 + int(mm))
        except Exception:
            return 0
    try:
        return max(0, int(float(s)))
    except Exception:
        return 0


@bp.post("/api/kiosk/session/manuell")
def kiosk_session_manuell():
    data = request.get_json(silent=True) or request.form
    pid = int(data.get("projekt_id", 0))
    tb = (data.get("teilbereich") or "").lower()
    name = (data.get("mitarbeiter") or "").strip()
    dur = _to_minutes(data.get("dauer"))
    if not pid or tb not in TB_KEYS or not name or dur <= 0:
        return jsonify(ok=False, message="Projekt/Mitarbeiter/Teilbereich/Dauer ungültig"), 400

    now = datetime.now(timezone.utc)
    db_exec(
        """
        INSERT INTO sitzungen_fertigung(projekt_id, teilbereich, mitarbeiter, start_zeit, end_zeit, dauer_minuten)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (pid, tb, name, now, now, dur),
    )
    return jsonify(ok=True)


@bp.post("/api/kiosk/session/start")
def kiosk_session_start():
    data = request.get_json(silent=True) or request.form
    pid = int(data.get("projekt_id", 0))
    tb = (data.get("teilbereich") or "").lower()
    name = (data.get("mitarbeiter") or "").strip()
    if not pid or tb not in TB_KEYS or not name:
        return jsonify(ok=False, message="Projekt/Mitarbeiter/Teilbereich ungültig"), 400

    now = datetime.now(timezone.utc)
    db_exec(
        """
        INSERT INTO sitzungen_fertigung(projekt_id, teilbereich, mitarbeiter, start_zeit, end_zeit, dauer_minuten)
        VALUES (%s,%s,%s,%s,NULL,0)
        """,
        (pid, tb, name, now),
    )
    return jsonify(ok=True)


@bp.post("/api/kiosk/session/stop")
def kiosk_session_stop():
    data = request.get_json(silent=True) or request.form
    sid = int(data.get("session_id", 0))

    rows = db_query(
        "SELECT id, start_zeit FROM sitzungen_fertigung WHERE id=%s AND end_zeit IS NULL",
        (sid,),
    )
    if not rows:
        return jsonify(ok=False, message="Offene Sitzung nicht gefunden"), 404

    start = rows[0]["start_zeit"]
    now = datetime.now(timezone.utc)
    minutes = max(0, int((now - start).total_seconds() // 60))

    db_exec(
        "UPDATE sitzungen_fertigung SET end_zeit=%s, dauer_minuten=%s WHERE id=%s",
        (now, minutes, sid),
    )
    return jsonify(ok=True, minutes=minutes)


