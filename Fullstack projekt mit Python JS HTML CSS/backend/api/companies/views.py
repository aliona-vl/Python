from flask import Blueprint, jsonify
from ...core.db import query

bp = Blueprint("companies", __name__)

@bp.get("")
def companies_list():
    rows = query("SELECT id, name FROM companies ORDER BY name ASC")
    return jsonify({"ok": True, "data": {"companies": rows}})
