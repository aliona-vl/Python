# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import (
    Blueprint, request, jsonify, render_template, redirect, url_for, make_response, current_app, session,
)
from datetime import datetime, date, time, timedelta, timezone

from typing import Any, Dict, List, Optional
from ...utils.guard import require_roles, require_role
from ...core.db import execute, query  

# DB + Auth
from ...core.db import conn as db_conn
from ...core.security import get_payload_from_request
from collections import OrderedDict


from zoneinfo import ZoneInfo
BERLIN = ZoneInfo("Europe/Berlin")

bp = Blueprint("legacy_manager", __name__)

BERLIN = ZoneInfo("Europe/Berlin")
TEILBEREICHE = ["besprechung", "zeichnung", "aufmass", "konstruktion", "sonstige"]

# Labels für die Tabellenüberschriften im Template
MANAGER_LABEL = OrderedDict([
    ("besprechung",  "Besprechung"),
    ("konstruktion", "Konstruktion"),
    ("zeichnung",    "Zeichnung"),
    ("aufmass",      "Aufmaß"),
    ("sonstige",     "Sonstige"),
])

FERT_LABEL = OrderedDict([
    ("schweissen",             "Schweißen"),
    ("umformen",               "Umformen"),
    ("zerspanung",             "Zerspanung"),
    ("oberflaechenbehandlung", "Oberflächenbehandlung"),
    ("montagevorbereitung",    "Montagevorbereitung"),
])

def _get_json():
    try:
        return request.get_json(silent=True) or {}
    except Exception:
        return {}

def _tz_local(dt):
    if not dt:
        return None
    return dt.astimezone(BERLIN) if getattr(dt, "tzinfo", None) else dt



def _read_name() -> str:
    """
    Liest 'name' aus JSON/Form/Query; wird von Mitarbeiter/Companies genutzt.
    """
    data = _get_json()
    if isinstance(data, dict) and data.get("name"):
        return str(data["name"]).strip()
    if request.form.get("name"):
        return request.form.get("name").strip()
    if request.values.get("name"):
        return request.values.get("name").strip()
    return ""


# Hilfsfunktionen (füge sie oberhalb der Routen ein)
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    return (
        s.replace("ä", "a")
         .replace("ö", "o")
         .replace("ü", "u")
         .replace("ß", "ss")
    )

# Slugs/Labels aus TEILBEREICHE ableiten (egal ob Liste von Strings oder (slug,label)-Tuples)
try:
    SLUGS
    LABELS
except NameError:
    if TEILBEREICHE and isinstance(TEILBEREICHE[0], (tuple, list)):
        SLUGS  = [s for s, _ in TEILBEREICHE]
        LABELS = {s: l for s, l in TEILBEREICHE}
    else:
        SLUGS  = list(TEILBEREICHE)
        LABELS = {s: s.capitalize() for s in SLUGS}

def _read_data():
    # JSON, Form oder Query akzeptieren
    return (request.get_json(silent=True) or
            (request.form.to_dict() if request.method == "POST" else request.args.to_dict()) or
            {})

def _build_summary(von_str: str | None, bis_str: str | None):
    """
    Liefert Rohdaten:
      projects: Liste pro Projekt mit Minuten je Bereich (Manager/Fertigung/Installation)
      totals_*: Summen in Minuten
    """
    dt_from, dt_to, *_ = _local_day_range(von_str, bis_str)

    with db_conn() as c, c.cursor() as cur:
        # --- Manager: alle sitzungen EXKL. Installation
        cur.execute("""
            SELECT s.projekt_id, LOWER(s.teilbereich) AS tb, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
              FROM sitzungen s
             WHERE s.end_zeit   >= %s
               AND s.start_zeit <= %s
               AND (s.installation_mitarbeiter IS NULL OR s.installation_mitarbeiter = '')
             GROUP BY s.projekt_id, LOWER(s.teilbereich)
        """, (dt_from, dt_to))
        mgr_rows = cur.fetchall()

        # --- Fertigung: eigene Tabelle (falls vorhanden), sonst leer
        fert_rows = []
        try:
            cur.execute("""
                SELECT sf.projekt_id, LOWER(sf.teilbereich) AS tb, COALESCE(SUM(sf.dauer_minuten),0)::int AS mins
                  FROM sitzungen_fertigung sf
                 WHERE (sf.end_zeit IS NOT NULL AND sf.end_zeit >= %s)
                   AND sf.start_zeit <= %s
                 GROUP BY sf.projekt_id, LOWER(sf.teilbereich)
            """, (dt_from, dt_to))
            fert_rows = cur.fetchall()
        except Exception:
            fert_rows = []

        # --- Installation: über Spalte installation_mitarbeiter, fallback auf teilbereich Namen
        try:
            cur.execute("""
                SELECT s.projekt_id, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
                  FROM sitzungen s
                 WHERE s.end_zeit   >= %s
                   AND s.start_zeit <= %s
                   AND s.installation_mitarbeiter IS NOT NULL
                   AND s.installation_mitarbeiter <> ''
                 GROUP BY s.projekt_id
            """, (dt_from, dt_to))
            inst_rows = cur.fetchall()
        except Exception:
            cur.execute("""
                SELECT s.projekt_id, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
                  FROM sitzungen s
                 WHERE s.end_zeit   >= %s
                   AND s.start_zeit <= %s
                   AND LOWER(s.teilbereich) IN ('installation','montage')
                 GROUP BY s.projekt_id
            """, (dt_from, dt_to))
            inst_rows = cur.fetchall()

        # betroffene Projekte
        pids = sorted(set([r[0] for r in mgr_rows] + [r[0] for r in fert_rows] + [r[0] for r in inst_rows]))
        if not pids:
            return {
                "von": (von_str or ""), "bis": (bis_str or ""),
                "projects": [], "totals_manager_min": 0, "totals_fert_min": 0,
                "totals_inst_min": 0, "totals_all_min": 0, "totals_all_text": _fmt_min(0),
            }

        cur.execute("SELECT id, name, kunde FROM projekte WHERE id = ANY(%s)", (pids,))
        proj_info = {r[0]: {"name": r[1], "kunde": r[2]} for r in cur.fetchall()}

    # Aggregation
    per_mgr  = {pid: {k: 0 for k in MANAGER_LABEL.keys()} for pid in pids}
    per_fert = {pid: {k: 0 for k in FERT_LABEL.keys()}    for pid in pids}
    per_inst = {pid: 0 for pid in pids}

    for pid, tb, mins in mgr_rows:
        per_mgr[pid][_norm_tb_key(tb)] = per_mgr[pid].get(_norm_tb_key(tb), 0) + int(mins)

    for pid, tb, mins in fert_rows:
        per_fert[pid][_norm_tb_key(tb)] = per_fert[pid].get(_norm_tb_key(tb), 0) + int(mins)

    for pid, mins in inst_rows:
        per_inst[pid] += int(mins)

    totals_manager_min = totals_fert_min = totals_inst_min = 0
    projects = []
    for pid in pids:
        info     = proj_info.get(pid, {}) or {}
        mgr_sum  = sum(per_mgr[pid].values())
        fert_sum = sum(per_fert[pid].values())
        inst_sum = per_inst[pid]
        total    = mgr_sum + fert_sum + inst_sum

        totals_manager_min += mgr_sum
        totals_fert_min    += fert_sum
        totals_inst_min    += inst_sum

        projects.append({
            "id": pid,
            "name":  info.get("name")  or f"Projekt #{pid}",
            "kunde": info.get("kunde") or "-",
            # ACHTUNG: hier liefern wir bereits flache int-Minuten (für dein Template)
            "manager":    {k: int(v) for k, v in per_mgr[pid].items()},
            "fertigung":  {k: int(v) for k, v in per_fert[pid].items()},
            "installation_min": int(inst_sum),
            "sum_all_text": _fmt_min(total),
        })

    return {
        "von": (von_str or ""), "bis": (bis_str or ""),
        "projects": projects,
        "totals_manager_min": totals_manager_min,
        "totals_fert_min": totals_fert_min,
        "totals_inst_min": totals_inst_min,
        "totals_all_min": (totals_manager_min + totals_fert_min + totals_inst_min),
        "totals_all_text": _fmt_min(totals_manager_min + totals_fert_min + totals_inst_min),
    }

def _ctx_for_gesamtbericht(von: str | None, bis: str | None):
    dt_from, dt_to, _, _ = _local_day_range(von, bis)

    with db_conn() as c, c.cursor() as cur:
        # Manager-Zeiten (alles in sitzungen, EXKL. Installation)
        cur.execute("""
            SELECT s.projekt_id, LOWER(s.teilbereich) AS tb, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
              FROM sitzungen s
             WHERE s.end_zeit   >= %s
               AND s.start_zeit <= %s
               AND (s.installation_mitarbeiter IS NULL OR s.installation_mitarbeiter = '')
             GROUP BY s.projekt_id, LOWER(s.teilbereich)
        """, (dt_from, dt_to))
        mgr_rows = cur.fetchall()

        # Fertigung (eigene Tabelle, falls vorhanden)
        fert_rows = []
        try:
            cur.execute("""
                SELECT sf.projekt_id, LOWER(sf.teilbereich) AS tb, COALESCE(SUM(sf.dauer_minuten),0)::int AS mins
                  FROM sitzungen_fertigung sf
                 WHERE (sf.end_zeit IS NOT NULL AND sf.end_zeit >= %s)
                   AND sf.start_zeit <= %s
                 GROUP BY sf.projekt_id, LOWER(sf.teilbereich)
            """, (dt_from, dt_to))
            fert_rows = cur.fetchall()
        except Exception:
            fert_rows = []

        # Installation (über installation_mitarbeiter; Fallback über Teilbereichsnamen)
        try:
            cur.execute("""
                SELECT s.projekt_id, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
                  FROM sitzungen s
                 WHERE s.end_zeit   >= %s
                   AND s.start_zeit <= %s
                   AND s.installation_mitarbeiter IS NOT NULL
                   AND s.installation_mitarbeiter <> ''
                 GROUP BY s.projekt_id
            """, (dt_from, dt_to))
            inst_rows = cur.fetchall()
        except Exception:
            cur.execute("""
                SELECT s.projekt_id, COALESCE(SUM(s.dauer_minuten),0)::int AS mins
                  FROM sitzungen s
                 WHERE s.end_zeit   >= %s
                   AND s.start_zeit <= %s
                   AND LOWER(s.teilbereich) IN ('installation','montage')
                 GROUP BY s.projekt_id
            """, (dt_from, dt_to))
            inst_rows = cur.fetchall()

        pids = sorted(set([r[0] for r in mgr_rows] + [r[0] for r in fert_rows] + [r[0] for r in inst_rows]))
        if not pids:
            return {
                "von": von or "", "bis": bis or (von or ""),
                "projekte": [],
                "manager_label": MANAGER_LABEL, "fert_label": FERT_LABEL,
                "manager_sum_global": {k: "0m" for k in MANAGER_LABEL},
                "fert_sum_global":    {k: "0m" for k in FERT_LABEL},
                "installation_sum_global": "0m",
                "gesamt_alle": "0m",
            }

        # Projekt-Stammdaten
        cur.execute("SELECT id, name, kunde FROM projekte WHERE id = ANY(%s)", (pids,))
        proj_info = {r[0]: {"name": r[1], "kunde": r[2]} for r in cur.fetchall()}

        # Projektzeitraum aus Sitzungen (+ optional Fertigung)
        cur.execute("""
            SELECT s.projekt_id, MIN(s.start_zeit), MAX(COALESCE(s.end_zeit, s.start_zeit))
              FROM sitzungen s
             WHERE s.projekt_id = ANY(%s)
             GROUP BY s.projekt_id
        """, (pids,))
        span_a = {pid: (start, end) for pid, start, end in cur.fetchall()}
        try:
            cur.execute("""
                SELECT sf.projekt_id, MIN(sf.start_zeit), MAX(COALESCE(sf.end_zeit, sf.start_zeit))
                  FROM sitzungen_fertigung sf
                 WHERE sf.projekt_id = ANY(%s)
                 GROUP BY sf.projekt_id
            """, (pids,))
            span_b = {pid: (start, end) for pid, start, end in cur.fetchall()}
        except Exception:
            span_b = {}

    # --- Aggregation
    per_mgr  = {pid: {k: 0 for k in MANAGER_LABEL} for pid in pids}
    per_fert = {pid: {k: 0 for k in FERT_LABEL}    for pid in pids}
    per_inst = {pid: 0 for pid in pids}

    for pid, tb, mins in mgr_rows:
        key = _norm_tb_key(tb)
        per_mgr[pid][key] = per_mgr[pid].get(key, 0) + int(mins)

    for pid, tb, mins in fert_rows:
        key = _norm_tb_key(tb)
        per_fert[pid][key] = per_fert[pid].get(key, 0) + int(mins)

    for pid, mins in inst_rows:
        per_inst[pid] += int(mins)

    mgr_global  = {k: 0 for k in MANAGER_LABEL}
    fert_global = {k: 0 for k in FERT_LABEL}
    inst_global = 0

    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    # FEHLTE BEI DIR: Liste initialisieren, bevor wir .append() aufrufen
    projekte = []
    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

    for pid in pids:
        info = proj_info.get(pid, {}) or {}
        m = per_mgr[pid]
        f = per_fert[pid]
        i = per_inst[pid]

        for k, v in m.items(): mgr_global[k]  = mgr_global.get(k, 0) + int(v)
        for k, v in f.items(): fert_global[k] = fert_global.get(k, 0) + int(v)
        inst_global += int(i)

        a = span_a.get(pid); b = span_b.get(pid)
        start_dt = min([x for x in [a[0] if a else None, b[0] if b else None] if x is not None], default=None)
        end_dt   = max([x for x in [a[1] if a else None, b[1] if b else None] if x is not None], default=None)
        zeitraum = f"{start_dt.date().isoformat()} – {end_dt.date().isoformat()}" if (start_dt and end_dt) else "-"

        projekte.append({
            "id": pid,
            "name":  info.get("name")  or f"Projekt #{pid}",
            "kunde": info.get("kunde") or "-",
            "manager":   {k: int(v) for k, v in m.items()},
            "fertigung": {k: int(v) for k, v in f.items()},
            "installation_min": int(i),
            "gesamt_text": _fmt_min(sum(m.values()) + sum(f.values()) + int(i)),
            "zeitraum": zeitraum,
        })

    # Labels ggf. dynamisch erweitern (falls neue Keys wie "sonstige" auftauchen)
    manager_label_out = dict(MANAGER_LABEL)
    for k in mgr_global:
        if k not in manager_label_out:
            manager_label_out[k] = k.capitalize()

    fert_label_out = dict(FERT_LABEL)
    for k in fert_global:
        if k not in fert_label_out:
            fert_label_out[k] = k.capitalize()

    return {
        "von": von or "", "bis": bis or (von or ""),
        "projekte": projekte,
        "manager_label": manager_label_out,
        "fert_label": fert_label_out,
        "manager_sum_global": {k: _fmt_min(v) for k, v in mgr_global.items()},
        "fert_sum_global":    {k: _fmt_min(v) for k, v in fert_global.items()},
        "installation_sum_global": _fmt_min(inst_global),
        "gesamt_alle": _fmt_min(sum(mgr_global.values()) + sum(fert_global.values()) + inst_global),
    }


def _fmt_min(mins: int) -> str:
    mins = int(mins or 0)
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)

def _local_day_range(von_str: str | None, bis_str: str | None):
    d_to   = _parse_date(bis_str) or date.today()
    d_from = _parse_date(von_str) or (d_to - timedelta(days=30))
    dt_from = datetime.combine(d_from, time.min).replace(tzinfo=timezone.utc)
    dt_to   = datetime.combine(d_to + timedelta(days=1), time.min).replace(tzinfo=timezone.utc)
    return dt_from, dt_to, d_from, d_to

def _norm_key(s: str) -> str:
    s = (s or "").strip().lower()
    return (s
            .replace("ä","a").replace("ö","o").replace("ü","u")
            .replace("ß","ss"))

def _norm_tb_key(s: str) -> str:
    k = _norm(s)
    mapping = {
        "aufmaß": "aufmass", "aufmass": "aufmass",
        "planung": "besprechung", "meeting": "besprechung",
        "anderes": "sonstige",
        "schweißen": "schweissen",
        "oberflächenbehandlung": "oberflaechenbehandlung",
        "montagevorb": "montagevorbereitung",
    }
    return mapping.get(k, k)


def _local_day_range(von_str: str | None, bis_str: str | None):
    d_to   = _parse_date(bis_str) or date.today()
    d_from = _parse_date(von_str) or (d_to - timedelta(days=30))
    dt_from = datetime.combine(d_from, time.min).replace(tzinfo=timezone.utc)
    dt_to   = datetime.combine(d_to + timedelta(days=1), time.min).replace(tzinfo=timezone.utc)
    return dt_from, dt_to, d_from, d_to



def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    return (s.replace("ä","a").replace("ö","o").replace("ü","u").replace("ß","ss"))

def _collect_tb_for_projects(c, project_ids):
    """
    Возвращает:
      per_project_tb: {pid: {teilbereich: {"gesamt_minuten": int, "anzahl_sitzungen": int}, ...}, ...}
      teilbereiche_sum: суммарно по всем проектам
    """
    per_project_tb = {pid: _empty_tb() for pid in project_ids}
    teilbereiche_sum = {k: 0 for k in TEILBEREICHE}

    with c.cursor() as cur:
        cur.execute("""
            SELECT s.projekt_id,
                   LOWER(s.teilbereich) AS tb_raw,
                   COALESCE(SUM(s.dauer_minuten),0)::int AS mins,
                   COUNT(*)::int AS cnt
              FROM sitzungen s
             WHERE s.projekt_id = ANY(%s)
             GROUP BY s.projekt_id, LOWER(s.teilbereich)
        """, (project_ids,))
        for pid, tb_raw, mins, cnt in cur.fetchall():
            k = _norm_tb_key(tb_raw)
            # завести неизвестный ключ динамически (чтобы не потерять данные)
            if k not in per_project_tb[pid]:
                per_project_tb[pid][k] = {"gesamt_minuten": 0, "anzahl_sitzungen": 0}
                if k not in teilbereiche_sum:
                    teilbereiche_sum[k] = 0
            per_project_tb[pid][k]["gesamt_minuten"] += int(mins)
            per_project_tb[pid][k]["anzahl_sitzungen"] += int(cnt)
            teilbereiche_sum[k] += int(mins)

    return per_project_tb, teilbereiche_sum

def _payload_from_request_local():
    """
    Liefert Payload für Manager-UI und Device/Kiosk:
    1) Session (falls vorhanden)
    2) Manager-Cookies zr_uid/zr_role
    3) JWT/Bearer über get_payload_from_request()
    """

    # 1) Session-payload
    for key in ("user_payload", "user", "me"):
        p = session.get(key)
        if isinstance(p, dict) and (p.get("role") or p.get("roles")):
            return p

    # 2) Manager-Cookies (so arbeitet dein Dashboard!)
    uid = request.cookies.get("zr_uid")
    role = (request.cookies.get("zr_role") or "").lower()
    if uid and role:
        try:
            return {"id": int(uid), "role": role, "roles": [role]}
        except ValueError:
            pass

    # 3) JWT/Bearer (falls vorhanden)
    try:
        p = get_payload_from_request() or {}
        if isinstance(p, dict) and (p.get("role") or p.get("roles")):
            return p
    except Exception:
        pass

    return {}


def _roles_from_payload(p):
    if not isinstance(p, dict):
        return []
    if isinstance(p.get("roles"), (list, tuple)):
        return list(p["roles"])
    if isinstance(p.get("role"), str) and p["role"]:
        return [p["role"]]
    return []
# ------------------------------- #
#  JINJA FILTER
# ------------------------------- #
@bp.app_template_filter("german_time")
def german_time_filter(v):
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v
        return dt.astimezone(BERLIN).strftime("%H:%M")
    except Exception:
        return str(v)

@bp.app_template_filter("german_date")
def german_date_filter(v):
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v
        return dt.astimezone(BERLIN).strftime("%d.%m.")
    except Exception:
        return str(v)

@bp.app_template_filter("aktuelle_dauer")
def aktuelle_dauer_filter(start_zeit):
    try:
        start = datetime.fromisoformat(start_zeit.replace("Z", "+00:00")) if isinstance(start_zeit, str) else start_zeit
        jetzt = datetime.now(timezone.utc) if start.tzinfo else datetime.now()
        mins = max(0, int((jetzt - start).total_seconds() / 60))
        return f"{mins//60}h {mins%60}m"
    except Exception:
        return "0h 0m"

@bp.app_template_filter("format_minuten")
def format_minuten_filter(mins):
    try:
        mins = int(mins or 0)
        h, m = mins // 60, mins % 60
        return f"{h}h {m}m" if h and m else (f"{h}h" if h else f"{m}m")
    except Exception:
        return str(mins)

# ------------------------------- #
#        SCHEMA / SEED
# ------------------------------- #
def ensure_manager_schema():
    sqls = [
        """
        CREATE TABLE IF NOT EXISTS kunden (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitarbeiter (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS projekte (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            kunde TEXT NOT NULL,
            ersteller TEXT,
            status TEXT NOT NULL DEFAULT 'gestoppt', -- gestoppt | aktiv | pausiert | beendet
            erstellt_am TIMESTAMPTZ NOT NULL DEFAULT now(),
            erster_start TIMESTAMPTZ,
            letzter_start TIMESTAMPTZ,
            beendet_am  TIMESTAMPTZ
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS aktive_sitzungen (
            id SERIAL PRIMARY KEY,
            projekt_id INT NOT NULL REFERENCES projekte(id) ON DELETE CASCADE,
            mitarbeiter TEXT NOT NULL,
            teilbereich TEXT NOT NULL,
            start_zeit TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS sitzungen (
            id SERIAL PRIMARY KEY,
            projekt_id INT NOT NULL REFERENCES projekte(id) ON DELETE CASCADE,
            mitarbeiter TEXT NOT NULL,
            teilbereich TEXT NOT NULL,
            start_zeit TIMESTAMPTZ NOT NULL,
            end_zeit   TIMESTAMPTZ NOT NULL,
            dauer_minuten INT NOT NULL
        );
        """
    ]
    with db_conn() as c, c.cursor() as cur:
        for s in sqls:
            cur.execute(s)
        # Seed nur, wenn leer
        cur.execute("SELECT COUNT(*) FROM mitarbeiter;")
        if cur.fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO mitarbeiter(name) VALUES (%s) ON CONFLICT DO NOTHING",
                [(n,) for n in ["Andreas","Mark","Fritz","Sabine","Thomas"]]
            )
        cur.execute("SELECT COUNT(*) FROM kunden;")
        if cur.fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO kunden(name) VALUES (%s) ON CONFLICT DO NOTHING",
                [(k,) for k in [
                    "Bosch Lollar","Buderus Guss GmbH","Duktus","Fritz Winter",
                    "Geissler","Hasenclever","Herborner Pumpenfabrik","Nowakowski"
                ]]
            )
        c.commit()

ensure_manager_schema()

# ------------------------------- #
#   HILFSFUNKTIONEN
# ------------------------------- #
def rowify(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

#def get_json_or_form() -> Dict[str, Any]:
   # if request.is_json:
    #    return request.get_json(force=True, silent=True) or {}
   # if request.form:
   #     return {k: v for k, v in request.form.items()}
   # return {}

def format_min_text(mins: int) -> str:
    mins = int(mins or 0)
    return f"{mins//60}h {mins%60}min" if mins >= 60 else f"{mins}min"

def token_user():
    """Liest JWT und gibt (role, name/email) zurück oder (None, None)."""
    payload = get_payload_from_request() or {}
    return payload.get("role"), payload.get("name") or payload.get("email")

def list_kunden() -> List[str]:
    with db_conn() as c, c.cursor() as cur:
        cur.execute("SELECT name FROM companies ORDER BY name")
        return [r[0] for r in cur.fetchall()]


def list_mitarbeiter() -> List[str]:
    with db_conn() as c, c.cursor() as cur:
        cur.execute("SELECT name FROM mitarbeiter ORDER BY name")
        return [r[0] for r in cur.fetchall()]

def projekte_for_template() -> List[Dict[str, Any]]:
    with db_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, name, kunde, status, erstellt_am, beendet_am
              FROM projekte
             ORDER BY erstellt_am DESC, id DESC
        """)
        projects = rowify(cur)

        out: List[Dict[str, Any]] = []
        for p in projects:
            cur.execute("""
                SELECT teilbereich, COALESCE(SUM(dauer_minuten),0) AS mins
                  FROM sitzungen
                 WHERE projekt_id=%s
                 GROUP BY teilbereich
            """, (p["id"],))
            agg = {r[0]: int(r[1]) for r in cur.fetchall()}
            tb = _agg_tb_for_project(cur, p["id"])
            out.append({
                "id": p["id"], "name": p["name"], "kunde": p["kunde"],
                "status": p["status"], "teilbereiche": tb,
                "erstellt_am": p["erstellt_am"], "beendet_am": p.get("beendet_am")
            })
        return out

    # --- Hilfen (nah bei den anderen Helfern platzieren) -----------------


def _parse_ids_from_request():
    """Nimmt JSON {projekt_ids:[…]} ODER Form-Data 'projekt_ids' entgegen."""
    ids = []
    if request.is_json:
        js = request.get_json(silent=True) or {}
        ids = js.get("projekt_ids") or js.get("ids") or []
    elif request.form:
        # kommt als mehrere Felder projekt_ids=12&projekt_ids=15 …
        vals = request.form.getlist("projekt_ids") or request.form.getlist("ids")
        ids = [int(v) for v in vals if str(v).strip().isdigit()]
    # Fallback: Query ?ids=1,2,3
    if not ids and request.args.get("ids"):
        ids = [int(v) for v in request.args.get("ids").split(",") if v.strip().isdigit()]
    ids = [int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()]
    if not ids:
        return None, "Keine Projekt-IDs übergeben"
    return ids, None


def _delete_projects(ids):
    """Löscht Projekte CASCADE (aktive_sitzungen/sitzungen verknüpft)."""
    if not ids:
        return
    with db_conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM projekte WHERE id = ANY(%s)", (ids,))
        c.commit()

    def _get_json():
        try:
            return request.get_json(silent=True) or {}
        except Exception:
            return {}

    def _read_name():
        """Liest 'name' aus JSON/Form/Query und trimmt."""
        data = _get_json()
        name = (
                data.get("name")
                or request.form.get("name")
                or request.values.get("name")
                or ""
        ).strip()
        return name


def _teilbereich_sum(pid: int) -> dict:
    sums = {}
    for t in ("besprechung", "zeichnung", "aufmass", "konstruktion", "sonstige"):
        if t == "sonstige":
            # Legacy: alte Einträge mit typ='anderes' mitzählen
            r = query(
                "SELECT COALESCE(SUM(minutes),0) AS m "
                "FROM time_entries "
                "WHERE project_id=%s AND (typ='sonstige' OR typ='anderes')",
                (pid,), one=True
            )
        else:
            r = query(
                "SELECT COALESCE(SUM(minutes),0) AS m "
                "FROM time_entries "
                "WHERE project_id=%s AND typ=%s",
                (pid, t), one=True
            )
        sums[t] = int(r["m"] or 0)
    return sums



# ------------------------------- #
#               UI
# ------------------------------- #
@bp.get("/dashboard")
def dashboard():
    # 1) Session prüfen (Login legt session["user"])
    u = session.get("user")

    # 2) Fallback: wenn Session fehlt, aus Cookies rehydrieren (werden beim Login gesetzt)
    if not u:
        uid  = request.cookies.get("zr_uid")
        role = (request.cookies.get("zr_role") or "").lower()
        if uid and role:
            try:
                session.permanent = True
                session["user"] = {
                    "id": int(uid),
                    "email": None,
                    "name": None,
                    "role": role,
                    "roles": [role],
                }
                u = session["user"]
            except Exception:
                u = None

    # 3) Nicht eingeloggt → zurück zur Startseite
    if not u:
        return redirect("/")

    # 4) Rollencheck: nur Manager/Admin
    role = (u.get("role") or "").lower()
    if role not in ("manager", "admin"):
        return redirect("/")

    who = u.get("name") or u.get("email") or ""

    # 5) Daten laden und Template rendern (wie bisher)
    kunden    = list_kunden()
    mitarb    = list_mitarbeiter()
    projekte  = projekte_for_template()
    return render_template(
        "dashboard_manager.html",
        kunden=kunden,
        mitarbeiter=mitarb,
        projekte=projekte,
        benutzer_name=who
    )

@bp.get("/projekt/<int:projekt_id>")
def projekt_detail(projekt_id: int):
    # --- Session wie beim Dashboard prüfen ---
    u = session.get("user")
    if not u:
        uid  = request.cookies.get("zr_uid")
        role = (request.cookies.get("zr_role") or "").lower()
        if uid and role:
            try:
                session.permanent = True
                session["user"] = {
                    "id": int(uid),
                    "email": None,
                    "name": None,
                    "role": role,
                    "roles": [role],
                }
                u = session["user"]
            except Exception:
                u = None

    if not u or (u.get("role") or "").lower() not in ("manager", "admin"):
        return redirect("/")

    # --- ab hier wie gehabt: Projekt & Daten laden ---
    with db_conn() as c, c.cursor() as cur:
        # 1) Projekt laden
        cur.execute("""
            SELECT id, name, kunde, status, erstellt_am
            FROM projekte
            WHERE id=%s
        """, (projekt_id,))
        rows = rowify(cur)
        if not rows:
            return redirect("/dashboard")
        projekt = rows[0]

        # 2) Aggregierte Minuten je Teilbereich
        cur.execute("""
            SELECT teilbereich, COALESCE(SUM(dauer_minuten),0) AS mins
            FROM sitzungen
            WHERE projekt_id=%s
            GROUP BY teilbereich
        """, (projekt_id,))
        projekt["teilbereiche"] = _agg_tb_for_project(cur, projekt_id)

        # 3) Aktive Sitzungen
        cur.execute("""
            SELECT mitarbeiter, teilbereich, start_zeit
            FROM aktive_sitzungen
            WHERE projekt_id=%s
            ORDER BY start_zeit DESC
        """, (projekt_id,))
        aktive_sitzungen = {}
        for mitarbeiter, teilbereich, start in cur.fetchall():
            if start.tzinfo:
                start_berlin = start.astimezone(BERLIN).strftime("%H:%M")
            else:
                start_berlin = start.strftime("%H:%M")
            aktive_sitzungen[mitarbeiter] = {
                "teilbereich": teilbereich,
                "start": start.isoformat(),
                "start_berlin": start_berlin,
            }

        # 4) Beendete Sitzungen
        cur.execute("""
            SELECT mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
            FROM sitzungen
            WHERE projekt_id=%s
            ORDER BY start_zeit DESC
        """, (projekt_id,))
        beendete_sitzungen = []
        for mitarbeiter, teilbereich, start, ende, mins in cur.fetchall():
            sb = (start.astimezone(BERLIN).strftime("%H:%M")
                  if start.tzinfo else start.strftime("%H:%M"))
            eb = (ende.astimezone(BERLIN).strftime("%H:%M")
                  if ende.tzinfo else ende.strftime("%H:%M"))
            beendete_sitzungen.append({
                "mitarbeiter": mitarbeiter,
                "teilbereich": teilbereich,
                "start": start.isoformat(),
                "start_berlin": sb,
                "end_berlin": eb,
                "dauer_minuten": int(mins or 0),
            })

    return render_template(
        "projekt_details.html",
        projekt=projekt,
        aktive_sitzungen=aktive_sitzungen,
        beendete_sitzungen=beendete_sitzungen,
        mitarbeiter=list_mitarbeiter(),
        teilbereiche=TEILBEREICHE,
    )

# ------------------------------- #
#       Neue Projekt
# ------------------------------- #
@bp.post("/projekt/neu")
def projekt_neu():
    name  = (request.form.get("name")  or "").strip()
    kunde = (request.form.get("kunde") or "").strip()
    if not name or not kunde:
        return jsonify(status="error", message="Name und Kunde sind erforderlich"), 400
    with db_conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO projekte(name, kunde, status) VALUES (%s,%s,'laufend') RETURNING id",
            (name, kunde)
        )
        pid = cur.fetchone()[0]
        c.commit()
    return jsonify(status="success", id=pid)


#-----Projekt löschen----
@bp.route("/projekte/löschen", methods=["POST"])
@bp.route("/projekte/loeschen", methods=["POST"])      # ASCII-Alias
@bp.route("/projekte/bulk-delete", methods=["POST"])    # Kompatibel zum Frontend
def projekte_loeschen_bulk():
    """
    Löscht Projekte in Bulk. Akzeptiert:
      JSON: {"ids":[1,2]} oder {"projekt_ids":[1,2]}
      FORM:  projekt_ids=1,2  oder  projekt_ids[]=1&projekt_ids[]=2
    """
    try:
        # --- IDs einlesen (ids ODER projekt_ids, JSON ODER Form) ---
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or data.get("projekt_ids")
        if not ids:
            ids = request.form.getlist("projekt_ids[]") or request.form.getlist("ids[]")
        if not ids:
            s = request.form.get("projekt_ids") or request.form.get("ids")
            if s:
                ids = [x.strip() for x in s.split(",") if x.strip()]

        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(",") if x.strip()]
        if not isinstance(ids, list) or not ids:
            return jsonify(status="error", message="Keine Projekt-IDs übergeben"), 400

        try:
            ids = [int(x) for x in ids]
        except Exception:
            return jsonify(status="error", message="Ungültige IDs"), 400

        # --- tatsächliches Löschen: erst Kinder, dann Projekte ---
        with db_conn() as c, c.cursor() as cur:
            # falls vorhanden – sicherheitshalber zuerst Kindtabellen
            try:
                cur.execute("DELETE FROM aktive_sitzungen WHERE projekt_id = ANY(%s)", (ids,))
            except Exception:
                pass
            try:
                cur.execute("DELETE FROM sitzungen WHERE projekt_id = ANY(%s)", (ids,))
            except Exception:
                pass
            cur.execute("DELETE FROM projekte WHERE id = ANY(%s)", (ids,))
            c.commit()

        return jsonify(status="success", deleted=len(ids))
    except Exception as e:
        current_app.logger.exception("projekte_loeschen_bulk failed")
        # immer JSON geben, damit das Frontend keinen „Network error“ wirft
        return jsonify(status="error", message=str(e)), 500

# ─────────────────────────────────────────────────────────────
# MITARBEITER: hinzufügen / löschen / auflisten
# Tabelle: mitarbeiter(name TEXT)
# ─────────────────────────────────────────────────────────────
@bp.post("/mitarbeiter/hinzufuegen", endpoint="staff_add_ascii")
@bp.post("/mitarbeiter/hinzufügen", endpoint="staff_add_umlaut")
def mitarbeiter_hinzufuegen():
    try:
        name = _read_name()
        if not name:
            return jsonify(status="error", message="Name fehlt"), 400
        with db_conn() as c, c.cursor() as cur:
            cur.execute("INSERT INTO mitarbeiter(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
            c.commit()
        return jsonify(status="success")
    except Exception:
        current_app.logger.exception("mitarbeiter_hinzufuegen failed")
        return jsonify(status="error", message="Serverfehler"), 500


@bp.post("/mitarbeiter/loeschen", endpoint="staff_del_ascii")
@bp.post("/mitarbeiter/löschen", endpoint="staff_del_umlaut")
def mitarbeiter_loeschen():
    try:
        name = _read_name()
        if not name:
            return jsonify(status="error", message="Name fehlt"), 400
        with db_conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM mitarbeiter WHERE name=%s", (name,))
            c.commit()
        return jsonify(status="success")
    except Exception:
        current_app.logger.exception("mitarbeiter_loeschen failed")
        return jsonify(status="error", message="Serverfehler"), 500

# ─────────────────────────────────────────────────────────────
# COMPANIES: hinzufügen / löschen / auflisten
# Tabelle: companies(name TEXT)
# ─────────────────────────────────────────────────────────────
@bp.post("/companies/hinzufuegen", endpoint="companies_add_ascii")
@bp.post("/companies/hinzufügen", endpoint="companies_add_umlaut")
def companies_hinzufuegen():
    try:
        name = _read_name()
        if not name:
            return jsonify(status="error", message="Name fehlt"), 400
        with db_conn() as c, c.cursor() as cur:
            cur.execute("INSERT INTO companies(name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
            c.commit()
        return jsonify(status="success")
    except Exception:
        current_app.logger.exception("companies_hinzufuegen failed")
        return jsonify(status="error", message="Serverfehler"), 500


@bp.post("/companies/loeschen", endpoint="companies_del_ascii")
@bp.post("/companies/löschen", endpoint="companies_del_umlaut")
def companies_loeschen():
    try:
        name = _read_name()
        if not name:
            return jsonify(status="error", message="Name fehlt"), 400
        with db_conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM companies WHERE name=%s", (name,))
            c.commit()
        return jsonify(status="success")
    except Exception:
        current_app.logger.exception("companies_loeschen failed")
        return jsonify(status="error", message="Serverfehler"), 500


def _parse_dauer_to_min(text: str) -> int:
    """
    Akzeptiert "90", "1:30", "1h30", "1h", "90m" etc. -> Minuten (int).
    """
    t = (text or "").strip().lower()
    if not t:
        return 0
    # 1) "H:MM"
    if ":" in t:
        h, m = t.split(":", 1)
        return max(0, int(h or 0) * 60 + int(m or 0))
    # 2) "1h30", "1h", "90m"
    h = m = 0
    if "h" in t:
        parts = t.split("h")
        h = int(parts[0] or 0)
        rest = parts[1]
        if rest.endswith("m"):
            rest = rest[:-1]
        m = int(rest or 0) if rest else 0
        return max(0, h * 60 + m)
    if t.endswith("m"):
        t = t[:-1]
    # 3) reine Zahl = Minuten
    return max(0, int(t))

def _empty_tb():
    return {name: {"gesamt_minuten": 0} for name in TEILBEREICHE}

def _agg_tb_for_project(cur, projekt_id: int):
    """Liest MINUTEN pro Teilbereich aus sitzungen und füllt fehlende mit 0."""
    cur.execute("""
        SELECT LOWER(teilbereich) AS tb, COALESCE(SUM(dauer_minuten),0)::int AS mins
          FROM sitzungen
         WHERE projekt_id=%s
         GROUP BY LOWER(teilbereich)
    """, (projekt_id,))
    rows = {r[0]: int(r[1]) for r in cur.fetchall()}
    tb = _empty_tb()
    for k, v in rows.items():
        key = "aufmass" if k in ("aufmass", "aufmaß") else k
        if key in tb:
            tb[key]["gesamt_minuten"] = v
    return tb
# ---------- START ----------
@bp.route("/projekt/<int:projekt_id>/aktivität/starten", methods=["POST", "GET"])
@bp.route("/projekt/<int:projekt_id>/aktivitaet/starten", methods=["POST", "GET"])  # Alias ohne Umlaut
def aktivitaet_starten(projekt_id: int):
    data = _read_data()
    mitarbeiter = (data.get("mitarbeiter") or data.get("worker") or "").strip()
    raw_tb      = data.get("teilbereich") or data.get("taetigkeit") or data.get("tätigkeit") or ""
    tb_norm     = _norm(raw_tb)

    # Label → Slug mappen (z.B. "Aufmaß" → "aufmass")
    slug = None
    for s in SLUGS:
        if tb_norm in (_norm(s), _norm(LABELS.get(s, ""))):
            slug = s
            break

    if not mitarbeiter:
        return jsonify(status="error", message="Mitarbeiter erforderlich"), 400
    if not slug:
        return jsonify(status="error", message="Ungültige Tätigkeit"), 400

    with db_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM aktive_sitzungen
             WHERE projekt_id=%s AND mitarbeiter=%s
        """, (projekt_id, mitarbeiter))
        if cur.fetchone():
            return jsonify(status="error", message="Dieser Mitarbeiter läuft bereits"), 400

        cur.execute("""
            INSERT INTO aktive_sitzungen(projekt_id, mitarbeiter, teilbereich, start_zeit)
            VALUES (%s,%s,%s, now())
        """, (projekt_id, mitarbeiter, slug))

        # Projektstatus setzen
        cur.execute("""
            UPDATE projekte
               SET status='aktiv',
                   erster_start = COALESCE(erster_start, now()),
                   letzter_start = now()
             WHERE id=%s
        """, (projekt_id,))
        c.commit()

    return jsonify(status="success", message="Aktivität gestartet", teilbereich=slug, mitarbeiter=mitarbeiter)

# ---------- STOP ----------
@bp.route("/projekt/<int:projekt_id>/aktivität/beenden", methods=["POST", "GET"])
@bp.route("/projekt/<int:projekt_id>/aktivitaet/beenden", methods=["POST", "GET"])  # Alias ohne Umlaut
def aktivitaet_beenden(projekt_id: int):
    data = _read_data()
    mitarbeiter = (data.get("mitarbeiter") or data.get("worker") or "").strip()
    if not mitarbeiter:
        return jsonify(status="error", message="Mitarbeiter erforderlich"), 400

    with db_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT teilbereich, start_zeit
              FROM aktive_sitzungen
             WHERE projekt_id=%s AND mitarbeiter=%s
        """, (projekt_id, mitarbeiter))
        row = cur.fetchone()
        if not row:
            return jsonify(status="error", message="Keine aktive Sitzung gefunden"), 404

        teilbereich, start_zeit = row[0], row[1]
        end_zeit  = datetime.now(timezone.utc)
        dauer_min = max(1, int((end_zeit - start_zeit).total_seconds() / 60))

        cur.execute("""
            INSERT INTO sitzungen(projekt_id, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (projekt_id, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_min))
        cur.execute("""
            DELETE FROM aktive_sitzungen
             WHERE projekt_id=%s AND mitarbeiter=%s
        """, (projekt_id, mitarbeiter))
        cur.execute("SELECT COUNT(*) FROM aktive_sitzungen WHERE projekt_id=%s", (projekt_id,))
        if (cur.fetchone()[0] or 0) == 0:
            cur.execute("UPDATE projekte SET status='pausiert' WHERE id=%s", (projekt_id,))
        c.commit()

    return jsonify(
        status="success",
        message="Aktivität beendet",
        dauer_minuten=dauer_min,
        dauer_text=(f"{dauer_min//60}h {dauer_min%60}m" if dauer_min >= 60 else f"{dauer_min}m"),
    )




@bp.post("/projekt/<int:projekt_id>/aktivitaet/manuell")
def manager_aktivitaet_manuell(projekt_id: int):
    """
    Manuelle Zeiteingabe für Manager.

    Erwartet z.B. JSON/Form:
      - mitarbeiter: "Andreas"
      - teilbereich: "besprechung" / "zeichnung" / ...
      - dauer: "90", "-30", "1:30", "1h30", "45m" usw.
        (zuerst wird versucht, als Minuten-Int zu lesen, sonst mit _parse_dauer_to_min)
    """
    data = _read_data()

    raw_name  = data.get("mitarbeiter") or data.get("worker") or ""
    raw_tb    = data.get("teilbereich") or data.get("taetigkeit") or data.get("tätigkeit") or ""
    raw_dauer = data.get("dauer")

    name    = raw_name.strip()
    tb_norm = _norm(raw_tb)

    # Teilbereich-Label → Slug
    slug = None
    for s in SLUGS:
        if tb_norm in (_norm(s), _norm(LABELS.get(s, ""))):
            slug = s
            break

    # -------- Dauer in Minuten umwandeln (mit Unterstützung für negative Ints) --------
    dauer_min = 0

    if isinstance(raw_dauer, (int, float)):
        dauer_min = int(raw_dauer)
    else:
        dauer_str = (raw_dauer or "").strip()
        if dauer_str:
            try:
                # unterstützt z.B. "90" oder "-30"
                dauer_min = int(dauer_str)
            except ValueError:
                # Fallback auf deinen alten Parser für Formate wie "1:30", "45m"
                dauer_min = _parse_dauer_to_min(dauer_str)

    # -------------------- Validierung --------------------
    if not name:
        return jsonify(status="error", message="Mitarbeiter erforderlich"), 400
    if not slug:
        return jsonify(status="error", message="Ungültige Tätigkeit"), 400
    # nur 0 verbieten – negative Werte sind Korrekturen erlaubt
    if dauer_min == 0:
        return jsonify(status="error", message="Dauer ungültig"), 400

    now = datetime.now(timezone.utc)

    execute(
        """
        INSERT INTO sitzungen
            (projekt_id, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (projekt_id, name, slug, now, now, dauer_min),
    )

    return jsonify(
        status="success",
        message="Manueller Zeiteintrag gespeichert",
        dauer_minuten=dauer_min,
        teilbereich=slug,
        mitarbeiter=name,
    )





@bp.post("/projekt/<int:projekt_id>/beenden")
@bp.post("/projekte/<int:projekt_id>/beenden")  # Alias, falls Frontend plural sendet
def projekt_beenden(projekt_id: int):
    """
    Beendet das Projekt:
    - alle aktiven Sitzungen werden bis JETZT verbucht
    - Projektstatus -> 'beendet', beendet_am = now()
    """

    # Quelle der Rollen robust zusammenführen (Body + Cookies)
    payload = _payload_from_request_local()  # falls vorhanden; sonst request.get_json(silent=True) verwenden
    body = (request.get_json(silent=True) or {})

    role = str(
        body.get("role")
        or payload.get("role")
        or request.cookies.get("zr_role")
        or ""
    ).lower()

    roles_raw = body.get("roles") or payload.get("roles") or []
    if not isinstance(roles_raw, (list, tuple)):
        roles_raw = [roles_raw] if roles_raw else []
    roles = [str(r).lower() for r in roles_raw]

    # Jetzt ist role/roles immer definiert
    if role not in ("manager", "admin") and not any(r in ("manager", "admin") for r in roles):
        return jsonify(status="error", message="Nicht erlaubt"), 403

    now = datetime.now(timezone.utc)
    closed = 0

    with db_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, mitarbeiter, teilbereich, start_zeit
              FROM aktive_sitzungen
             WHERE projekt_id=%s
             ORDER BY start_zeit ASC
        """, (projekt_id,))
        aktive = cur.fetchall()

        for sid, mitarbeiter, teilbereich, start_zeit in aktive:
            if start_zeit.tzinfo is None:
                start_zeit = start_zeit.replace(tzinfo=timezone.utc)

            dauer_min = max(1, int((now - start_zeit).total_seconds() / 60))

            cur.execute("""
                INSERT INTO sitzungen(
                    projekt_id, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
                )
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (projekt_id, mitarbeiter, teilbereich, start_zeit, now, dauer_min))

            cur.execute("DELETE FROM aktive_sitzungen WHERE id=%s", (sid,))
            closed += 1

        cur.execute("""
            UPDATE projekte
               SET status='beendet',
                   beendet_am=%s
             WHERE id=%s
        """, (now, projekt_id))

        c.commit()

    return jsonify(status="success", closed=closed)

# ------------------------------- #
#            Gesamt -Berichte
# ------------------------------- #
def _zeitraum():
    return request.args.get("von"), request.args.get("bis")

@bp.post("/export/vorschau")
def export_vorschau():
    try:
        data = request.get_json(silent=True) or {}
        von_str = (data.get("von_datum") or "").strip() or None
        bis_str = (data.get("bis_datum") or "").strip() or None
        dt_from, dt_to = _local_day_range(von_str, bis_str)

        projekte_out = []
        total_all_mins = 0
        teilbereiche_sum = {k: 0 for k in TEILBEREICHE}

        with db_conn() as c, c.cursor() as cur:
            # Minuten je Projekt & Teilbereich
            cur.execute("""
                SELECT s.projekt_id,
                       LOWER(s.teilbereich) AS tb,
                       COALESCE(SUM(s.dauer_minuten),0)::int AS mins
                  FROM sitzungen s
                 WHERE s.end_zeit   >= %s
                   AND s.start_zeit <= %s
                 GROUP BY s.projekt_id, LOWER(s.teilbereich)
                 ORDER BY s.projekt_id
            """, (dt_from, dt_to))
            rows = cur.fetchall()
            projekt_ids = sorted({r[0] for r in rows})

            per_project_tb = {
                pid: {k: {"gesamt_minuten": 0} for k in TEILBEREICHE}
                for pid in projekt_ids
            }
            for pid, tb_raw, mins in rows:
                k = _norm_tb_key(tb_raw)
                if k not in per_project_tb[pid]:
                    per_project_tb[pid][k] = {"gesamt_minuten": 0}
                    if k not in teilbereiche_sum:
                        teilbereiche_sum[k] = 0
                        if k not in TEILBEREICHE:
                            TEILBEREICHE.append(k)
                per_project_tb[pid][k]["gesamt_minuten"] += int(mins)

            proj_info = {}
            if projekt_ids:
                cur.execute("SELECT id, name, kunde FROM projekte WHERE id = ANY(%s)", (projekt_ids,))
                proj_info = {r[0]: {"name": r[1], "kunde": r[2]} for r in cur.fetchall()}

            for pid in projekt_ids:
                tb_map = per_project_tb[pid]
                mins_total = sum(v["gesamt_minuten"] for v in tb_map.values())
                total_all_mins += mins_total
                for k, v in tb_map.items():
                    teilbereiche_sum[k] = teilbereiche_sum.get(k, 0) + v["gesamt_minuten"]

                info = proj_info.get(pid, {}) or {}
                projekte_out.append({
                    "id": pid,
                    "name": info.get("name") or f"Projekt #{pid}",
                    "kunde": info.get("kunde") or "-",
                    "gesamt_zeit": _fmt_min(mins_total),
                    "teilbereiche": tb_map,
                    # Fallback-Keys für ältere Frontends:
                    "besprechung_zeit": _fmt_min(tb_map.get("besprechung", {}).get("gesamt_minuten", 0)),
                    "zeichnung_zeit":   _fmt_min(tb_map.get("zeichnung",   {}).get("gesamt_minuten", 0)),
                    "aufmass_zeit":     _fmt_min(tb_map.get("aufmass",     {}).get("gesamt_minuten", 0)),
                })

        return jsonify(
            status="success",
            projekte=projekte_out,
            gesamt_zeit=_fmt_min(total_all_mins),
            teilbereiche_def=TEILBEREICHE,
            teilbereiche_sum=teilbereiche_sum,
            gesamt_besprechung=_fmt_min(teilbereiche_sum.get("besprechung", 0)),
            gesamt_zeichnung=_fmt_min(teilbereiche_sum.get("zeichnung", 0)),
            gesamt_aufmass=_fmt_min(teilbereiche_sum.get("aufmass", 0)),
            gesamt_konstruktion=_fmt_min(teilbereiche_sum.get("konstruktion", 0)),
            gesamt_sonstige=_fmt_min(teilbereiche_sum.get("sonstige", 0)),
        )
    except Exception as e:
        current_app.logger.exception("export_vorschau failed")
        return jsonify(status="error", message=str(e)), 500

        # ... innerhalb von export_vorschau(), direkt vor dem return:

        # flache Summen für Alt-Frontend
        def _fmt_min(mins: int) -> str:
            mins = int(mins or 0)
            return f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60}m"

        gesamt_besprechung = _fmt_min(teilbereiche_sum.get("besprechung", 0))
        gesamt_zeichnung = _fmt_min(teilbereiche_sum.get("zeichnung", 0))
        gesamt_aufmass = _fmt_min(teilbereiche_sum.get("aufmass", 0))
        gesamt_konstruktion = _fmt_min(teilbereiche_sum.get("konstruktion", 0))
        gesamt_sonstige = _fmt_min(teilbereiche_sum.get("sonstige", 0))

        return jsonify(
            status="success",
            projekte=projekte_out,
            gesamt_zeit=_fmt_min(total_all_mins),
            teilbereiche_def=TEILBEREICHE,
            teilbereiche_sum=teilbereiche_sum,
            # ↓↓↓ Alt-Frontend-Kompatibilität
            gesamt_besprechung=gesamt_besprechung,
            gesamt_zeichnung=gesamt_zeichnung,
            gesamt_aufmass=gesamt_aufmass,
            gesamt_konstruktion=gesamt_konstruktion,
            gesamt_sonstige=gesamt_sonstige,
        )

        return jsonify(
            status="success",
            projekte=projekte_out,
            gesamt_zeit=_fmt_min(total_all_mins),
            teilbereiche_def=TEILBEREICHE,
            teilbereiche_sum=teilbereiche_sum,
        )

    except Exception as e:
        current_app.logger.exception("export_vorschau failed")
        return jsonify(status="error", message=str(e)), 500

    @bp.get("/export/vollbericht")
    def export_vollbericht_get():
        # gleiche Logik wie der HTML-Gesamtbericht,
        # nutzt die vorhandenen Query-Parameter ?von=YYYY-MM-DD&bis=YYYY-MM-DD
        return _report_summary()


# QR-code
# POST /api/device/qr  -> { ok:true, url:"https://…/device/activate?code=XXX" }
@bp.post("/api/device/qr")
def device_qr():
    # leichte Rollenprüfung: payload aus Session/Token lesen
    payload = session.get("user_payload") or {}
    roles = (payload.get("roles") or []) + ([payload.get("role")] if payload.get("role") else [])
    # Wenn du’s streng willst, einkommentieren:
    # if not any(r in ("manager", "admin") for r in roles):
    #     return jsonify(ok=False, message="Nicht eingeloggt oder fehlende Rechte"), 401

    # Tabelle idempotent anlegen
    execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
            token       TEXT PRIMARY KEY,
            label       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at  TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '10 minutes'),
            used        BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)

    import secrets, string
    from datetime import datetime, timezone, timedelta
    code    = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    execute("INSERT INTO device_tokens (token, label, expires_at) VALUES (%s,%s,%s)",
            (code, "Tablet-Login", expires))

    # absolute URL zur Aktivierung
    base = request.host_url.rstrip("/")
    activate_url = f"{base}/device/activate?code={code}"
    return jsonify(ok=True, url=activate_url)




@bp.get("/device/activate")
def device_activate():
    code = (request.args.get("code") or "").strip()
    if not code:
        return make_response("Code fehlt", 400)

    rows = query("SELECT token, expires_at, used FROM device_tokens WHERE token=%s LIMIT 1", (code,))
    if not rows:
        return make_response("Code ungültig", 400)
    row = rows[0]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # idempotent: ist er noch nicht benutzt, dann prüfen & markieren
    if not row["used"]:
        if row["expires_at"] < now:
            return make_response("Code abgelaufen", 410)
        execute("UPDATE device_tokens SET used=TRUE WHERE token=%s", (code,))

    # Session für Kiosk legen (Rolle 'fertigung')
    session["user_payload"] = {"sub": f"device:{code[:8]}", "roles": ["fertigung"]}
    return redirect("/kiosk")









# POST /api/auth/refresh  -> setzt neue Cookies
@bp.post("/api/auth/refresh")
def refresh():
    import datetime as dt, uuid, secrets
    device_id = request.cookies.get("device_id")
    refresh   = request.cookies.get("refresh") or ""
    if not device_id or not refresh:
        return jsonify(ok=False, message="no refresh"), 401

    row = query("""
      SELECT id, role, refresh_jti, refresh_expires, is_active
      FROM geraet_sessions WHERE device_id=%s
        AND refresh_hash = crypt(%s, refresh_hash)
    """, (device_id, refresh))
    if not row: return jsonify(ok=False, message="invalid refresh"), 401
    r = row[0]
    if not r["is_active"] or r["refresh_expires"] < dt.datetime.utcnow():
        return jsonify(ok=False, message="expired"), 401

    # rotate refresh
    new_refresh   = secrets.token_urlsafe(48)
    new_refresh_j = uuid.uuid4().hex
    new_exp       = dt.datetime.utcnow()+dt.timedelta(days=90)
    execute("""
      UPDATE geraet_sessions
         SET refresh_jti=%s, refresh_hash=crypt(%s, gen_salt('bf')),
             refresh_expires=%s, last_seen=now()
       WHERE device_id=%s
    """, (new_refresh_j, new_refresh, new_exp, device_id))

    # new access
    access_payload = {"role": r["role"], "device_id": device_id,
                      "exp": dt.datetime.utcnow()+dt.timedelta(minutes=20)}
    access = jwt_encode(access_payload)

    resp = jsonify(ok=True)
    resp.set_cookie("access", access, httponly=True, secure=True, samesite="Lax", max_age=60*20)
    resp.set_cookie("refresh", new_refresh, httponly=True, secure=True, samesite="Lax", max_age=60*60*24*90)
    return resp


# ============================================================
#                      PROJEKT-BERICHT (HTML)
# ============================================================
def _build_verlauf_rows(cur, projekt_id: int):
    rows = []

    # 1) Manager-Sitzungen (ohne Installation)
    cur.execute("""
        SELECT COALESCE(mitarbeiter,'Unbekannt') AS ma,
               teilbereich, start_zeit, end_zeit, COALESCE(dauer_minuten,0)::int AS m
          FROM sitzungen
         WHERE projekt_id=%s
           AND (installation_mitarbeiter IS NULL OR installation_mitarbeiter = '')
         ORDER BY start_zeit
    """, (projekt_id,))
    for ma, tb, s, e, m in cur.fetchall():
        s2, e2 = _tz_local(s), _tz_local(e)
        rows.append({
            "quelle": "manager",
            "datum": s2.strftime("%d.%m.%Y"),
            "start": s2.strftime("%H:%M"),
            "ende":  e2.strftime("%H:%M") if e2 else "-",
            "mitarbeiter": ma,
            "teilbereich": MANAGER_LABEL.get((tb or "").lower(), tb or "-"),
            "dauer": _fmt_min(m),
            "_sort": s2
        })

    # 2) Installation (aus sitzungen MIT installation_mitarbeiter)
    cur.execute("""
        SELECT COALESCE(installation_mitarbeiter, mitarbeiter) AS ma,
               start_zeit, end_zeit, COALESCE(dauer_minuten,0)::int AS m
          FROM sitzungen
         WHERE projekt_id=%s
           AND installation_mitarbeiter IS NOT NULL
           AND installation_mitarbeiter <> ''
         ORDER BY start_zeit
    """, (projekt_id,))
    for ma, s, e, m in cur.fetchall():
        s2, e2 = _tz_local(s), _tz_local(e)
        rows.append({
            "quelle": "installation",
            "datum": s2.strftime("%d.%m.%Y"),
            "start": s2.strftime("%H:%M"),
            "ende":  e2.strftime("%H:%M") if e2 else "-",
            "mitarbeiter": ma,
            "teilbereich": "Installation",
            "dauer": _fmt_min(m),
            "_sort": s2
        })

    # 3) Fertigung
    cur.execute("""
        SELECT COALESCE(mitarbeiter,'Unbekannt') AS ma,
               teilbereich, start_zeit, end_zeit, COALESCE(dauer_minuten,0)::int AS m
          FROM sitzungen_fertigung
         WHERE projekt_id=%s
         ORDER BY start_zeit
    """, (projekt_id,))
    for ma, tb, s, e, m in cur.fetchall():
        s2, e2 = _tz_local(s), _tz_local(e)
        rows.append({
            "quelle": "fertigung",
            "datum": s2.strftime("%d.%m.%Y"),
            "start": s2.strftime("%H:%M"),
            "ende":  e2.strftime("%H:%M") if e2 else "-",
            "mitarbeiter": ma,
            "teilbereich": FERT_LABEL.get((tb or "").lower(), tb or "-"),
            "dauer": _fmt_min(m),
            "_sort": s2
        })

    rows.sort(key=lambda r: r["_sort"])
    for r in rows:
        r.pop("_sort", None)
    return rows

##Bericht für projekt
@bp.get("/projekt/<int:projekt_id>/bericht")
def projekt_bericht(projekt_id: int):
    # Projekt laden
    with db_conn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT id, name, kunde, status, erstellt_am
            FROM projekte
            WHERE id=%s
        """, (projekt_id,))
        row = cur.fetchone()
        if not row:
            return redirect("/dashboard")
        projekt = {
            "id": row[0],
            "name": row[1],
            "kunde": row[2],
            "status": row[3],
            "erstellt_am": row[4],
        }

        # ---------------- Manager (aus sitzungen, ohne Installation) ----------------
        cur.execute("""
            SELECT lower(teilbereich) AS tb, COALESCE(SUM(dauer_minuten),0)::int
            FROM sitzungen
            WHERE projekt_id=%s
              AND (installation_mitarbeiter IS NULL OR installation_mitarbeiter = '')
            GROUP BY lower(teilbereich)
        """, (projekt_id,))
        mgr_raw = {tb: int(m) for tb, m in cur.fetchall()}
        manager_sum_by_tb = {k: mgr_raw.get(k, 0) for k in MANAGER_LABEL.keys()}
        manager_total_min = sum(manager_sum_by_tb.values())

        cur.execute("""
            SELECT COALESCE(mitarbeiter,'Unbekannt') AS ma,
                   COALESCE(SUM(dauer_minuten),0)::int
            FROM sitzungen
            WHERE projekt_id=%s
              AND (installation_mitarbeiter IS NULL OR installation_mitarbeiter = '')
            GROUP BY COALESCE(mitarbeiter,'Unbekannt')
            ORDER BY 2 DESC, 1
        """, (projekt_id,))
        manager_by_ma = [
            {"mitarbeiter": ma, "minuten": int(m), "min_text": _fmt_min(m)}
            for ma, m in cur.fetchall()
        ]

        # ---------------- Fertigung (sitzungen_fertigung) ----------------
        cur.execute("""
            SELECT lower(teilbereich) AS tb, COALESCE(SUM(dauer_minuten),0)::int
            FROM sitzungen_fertigung
            WHERE projekt_id=%s
            GROUP BY lower(teilbereich)
        """, (projekt_id,))
        fert_raw = {tb: int(m) for tb, m in cur.fetchall()}
        fert_sum_by_tb = {k: fert_raw.get(k, 0) for k in FERT_LABEL.keys()}
        fert_total_min = sum(fert_sum_by_tb.values())

        cur.execute("""
            SELECT COALESCE(mitarbeiter,'Unbekannt') AS ma,
                   COALESCE(SUM(dauer_minuten),0)::int
            FROM sitzungen_fertigung
            WHERE projekt_id=%s
            GROUP BY COALESCE(mitarbeiter,'Unbekannt')
            ORDER BY 2 DESC, 1
        """, (projekt_id,))
        fert_by_ma = [
            {"mitarbeiter": ma, "minuten": int(m), "min_text": _fmt_min(m)}
            for ma, m in cur.fetchall()
        ]

        # ---------------- Installation (separat aus sitzungen) ----------------
        cur.execute("""
            SELECT COALESCE(installation_mitarbeiter, mitarbeiter) AS ma,
                   COALESCE(SUM(dauer_minuten),0)::int
            FROM sitzungen
            WHERE projekt_id=%s
              AND installation_mitarbeiter IS NOT NULL
              AND installation_mitarbeiter <> ''
            GROUP BY COALESCE(installation_mitarbeiter, mitarbeiter)
            ORDER BY 2 DESC, 1
        """, (projekt_id,))
        installation_by_ma = [
            {"mitarbeiter": ma, "minuten": int(m), "min_text": _fmt_min(m)}
            for ma, m in cur.fetchall()
        ]
        installation_total_min = sum(x["minuten"] for x in installation_by_ma)

        # ---------------- Timelines pro Bereich (für bestehendes Template) ----------------
        cur.execute("""
            SELECT COALESCE(installation_mitarbeiter, mitarbeiter) AS ma,
                   start_zeit, end_zeit, dauer_minuten
            FROM sitzungen
            WHERE projekt_id=%s
              AND installation_mitarbeiter IS NOT NULL
              AND installation_mitarbeiter <> ''
            ORDER BY start_zeit
        """, (projekt_id,))
        inst_timeline = []
        for ma, s, e, m in cur.fetchall():
            s2 = s.astimezone(BERLIN) if getattr(s, "tzinfo", None) else s
            e2 = e.astimezone(BERLIN) if e and getattr(e, "tzinfo", None) else e
            inst_timeline.append({
                "datum": s2.strftime("%d.%m.%Y"),
                "start": s2.strftime("%H:%M"),
                "ende":  e2.strftime("%H:%M") if e2 else "-",
                "mitarbeiter": ma,
                "teilbereich": "Installation",
                "dauer": _fmt_min(m or 0),
                "quelle": "installation",
            })

        cur.execute("""
            SELECT mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
            FROM sitzungen_fertigung
            WHERE projekt_id=%s
            ORDER BY start_zeit
        """, (projekt_id,))
        fert_timeline = []
        for ma, tb, s, e, m in cur.fetchall():
            s2 = s.astimezone(BERLIN) if getattr(s, "tzinfo", None) else s
            e2 = e.astimezone(BERLIN) if e and getattr(e, "tzinfo", None) else e
            fert_timeline.append({
                "datum": s2.strftime("%d.%m.%Y"),
                "start": s2.strftime("%H:%M"),
                "ende":  e2.strftime("%H:%M") if e2 else "-",
                "mitarbeiter": ma,
                "teilbereich": FERT_LABEL.get(tb.lower(), tb),
                "dauer": _fmt_min(m or 0),
                "quelle": "fertigung",
            })

        # ---------------- KOMPLETTER VERLAUF (alle Sitzungen, sortiert) ----------------
        cur.execute("""
          SELECT 'manager' AS src, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
            FROM sitzungen
           WHERE projekt_id=%s
             AND (installation_mitarbeiter IS NULL OR installation_mitarbeiter = '')
          UNION ALL
          SELECT 'installation' AS src, COALESCE(installation_mitarbeiter, mitarbeiter),
                 'Installation', start_zeit, end_zeit, dauer_minuten
            FROM sitzungen
           WHERE projekt_id=%s
             AND installation_mitarbeiter IS NOT NULL
             AND installation_mitarbeiter <> ''
          UNION ALL
          SELECT 'fertigung' AS src, mitarbeiter, teilbereich, start_zeit, end_zeit, dauer_minuten
            FROM sitzungen_fertigung
           WHERE projekt_id=%s
          ORDER BY 4
        """, (projekt_id, projekt_id, projekt_id))
        verlauf_rows = []
        for src, ma, tb, s, e, m in cur.fetchall():
            s2 = s.astimezone(BERLIN) if getattr(s, "tzinfo", None) else s
            e2 = e.astimezone(BERLIN) if e and getattr(e, "tzinfo", None) else e
            verlauf_rows.append({
                "datum": s2.strftime("%d.%m.%Y"),
                "start": s2.strftime("%H:%M"),
                "ende": e2.strftime("%H:%M") if e2 else "-",
                "dauer": _fmt_min(m or 0),
                "teilbereich": FERT_LABEL.get(tb.lower(), tb) if src == "fertigung" else (tb or "-"),
                "mitarbeiter": ma or "Unbekannt",
                "quelle": src,
            })

    gesamt_min = manager_total_min + fert_total_min + installation_total_min
    manager_gesamt_zeit = _fmt_min(manager_total_min)

    return render_template(
        "bericht.html",
        projekt=projekt,

        # Manager
        manager_label=MANAGER_LABEL,
        manager_sum_by_tb=manager_sum_by_tb,
        manager_total_min=manager_total_min,
        manager_gesamt_zeit=manager_gesamt_zeit,  # ← NEU
        manager_by_ma=manager_by_ma,

        # Fertigung
        fert_label=FERT_LABEL,
        fert_sum_by_tb=fert_sum_by_tb,
        fert_total_min=fert_total_min,
        fert_by_ma=fert_by_ma,

        # Installation
        installation_total_min=installation_total_min,
        installation_by_ma=installation_by_ma,

        # Timelines / Verlauf
        installation={"gesamt": _fmt_min(installation_total_min), "verlauf": inst_timeline},
        fertigung={"gesamt": _fmt_min(fert_total_min), "verlauf": fert_timeline},
        verlauf_rows=verlauf_rows,          # <- kompletter Verlauf
        timeline=verlauf_rows,              # optional für alte Templates

        gesamt_min=gesamt_min,
        gesamt_zeit=_fmt_min(gesamt_min),
    )




@bp.post("/export/vorschau", endpoint="export_vorschau_new")
def export_vorschau_new():
    data = request.get_json(silent=True) or {}
    von = (data.get("von_datum") or "").strip()
    bis = (data.get("bis_datum") or "").strip()
    if not _parse_date(von):
        return jsonify(ok=False, message="Ungültiges Von-Datum"), 400
    if bis and not _parse_date(bis):
        return jsonify(ok=False, message="Ungültiges Bis-Datum"), 400
    if not bis:
        bis = von
    return jsonify(
        ok=True,
        url=f"/export/vollbericht?von={_parse_date(von).isoformat()}&bis={_parse_date(bis).isoformat()}",
    )

@bp.get("/export/vollbericht", endpoint="export_vollbericht_new")
def export_vollbericht_new():
    von = request.args.get("von") or ""
    bis = request.args.get("bis") or von

    # Kontext aufbauen (liefert bereits 'projekte', 'manager_label', 'fert_label',
    # 'manager_sum_global', 'fert_sum_global', 'installation_sum_global', 'gesamt_alle', ...)
    ctx = _ctx_for_gesamtbericht(von, bis)

    # Toolbar-Infos
    ctx["back_url"] = request.args.get("back") or request.headers.get("Referer") or "/dashboard"
    ctx["print_title"] = f"Gesamtbericht {von} – {bis}"
    ctx["projekt_detail_base"] = "/manager/projekt"  # ggf. auf deine echte Detailroute anpassen

    return render_template("gesamt_bericht.html", **ctx)


## Prüft alle active sitzungen für Benachrichtigungen

@bp.get("/api/sessions/active")
def get_active_sessions():
    """Gibt alle aktiven Sitzungen zurück (für Benachrichtigungen)"""
    rows = query("""
        SELECT 
            a.projekt_id,
            p.name as projekt_name,
            a.mitarbeiter,
            a.teilbereich,
            a.start_zeit
        FROM aktive_sitzungen a
        JOIN projekte p ON p.id = a.projekt_id
        ORDER BY a.start_zeit DESC
    """)

    return jsonify(ok=True, data=rows or [])

