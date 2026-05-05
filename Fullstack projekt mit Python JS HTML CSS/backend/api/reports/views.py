from flask import Blueprint, make_response
from ...core.db import query
from ...core.security import require_auth
from ...config import Settings

bp = Blueprint("reports", __name__)

@bp.get("/project/<int:p_id>")
@require_auth(roles=["admin","manager"])
def project_pdf(p_id):
    # einfache PDF-Erzeugung (ReportLab empfohlen). Fallback: minimaler PDF-Stream.
    try:
        from reportlab.pdfgen import canvas
        from io import BytesIO
        proj = query("""SELECT p.id, p.name, p.status, c.name AS company
                        FROM projects p JOIN companies c ON c.id=p.company_id
                        WHERE p.id=%s""", (p_id,), one=True)
        entries = query("""SELECT worker_name, activity, started_at, ended_at, minutes, comment
                           FROM time_entries WHERE project_id=%s ORDER BY id""", (p_id,))
        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.setTitle(f"Projekt {p_id}")
        c.drawString(40, 800, f"Projekt #{proj['id']}: {proj['name']} – {proj['company']}  (Status: {proj['status']})")
        y = 770
        for e in entries[:100]:
            line = f"{e['worker_name'] or '-'}  {e['activity']}  {e['minutes']} min  {str(e['started_at'])[:16]}"
            c.drawString(40, y, line); y -= 14
            if y < 60: c.showPage(); y=800
        c.showPage(); c.save()
        pdf = buf.getvalue()
        resp = make_response(pdf)
        resp.headers["Content-Type"] = "application/pdf"
        resp.headers["Content-Disposition"] = f"inline; filename=project_{p_id}.pdf"
        return resp
    except Exception:
        # Fallback: sehr einfacher PDF-Header (damit das iframe nicht crasht)
        if Settings.REPORT_FALLBACK_HTML:
            html = f"<h1>Projekt {p_id}</h1><p>Installiere 'reportlab' für echte PDFs.</p>"
            resp = make_response(html)
            resp.headers["Content-Type"] = "text/html; charset=utf-8"
            return resp
        raise
