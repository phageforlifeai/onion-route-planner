"""
app.py – Onion Route Planner (Chennai) – web server + API.

Run:   python app.py        → open http://localhost:5000
"""
import datetime as dt
import hashlib
import hmac
import io
import os
import time
import uuid

from flask import (Flask, jsonify, redirect, render_template_string, request, send_file,
                   send_from_directory, session, url_for)

import storage
from export_xlsx import build_route_workbook, build_template, parse_customers_xlsx, parse_orders_xlsx
from geocode import geocode
from matrix import CACHE
from planner import PlanError, import_customers, import_orders, plan

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="/static")
app.secret_key = storage.secret_key()
app.permanent_session_lifetime = dt.timedelta(days=60)

# ---------------------------------------------------------------------------
# access control: set APP_PASSWORD to require a login (shared team password)
# ---------------------------------------------------------------------------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
_AUTH_TAG = hashlib.sha256(APP_PASSWORD.encode()).hexdigest()[:16]   # changes when the password changes

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in – Onion Route Planner</title>
<link rel="manifest" href="/static/manifest.webmanifest"><meta name="theme-color" content="#7b3f00">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f2ee;font-family:system-ui,sans-serif;color:#2b2b2b}
form{background:#fff;padding:28px 26px;border-radius:14px;box-shadow:0 4px 18px rgba(0,0,0,.12);width:min(360px,92vw);text-align:center}
h1{font-size:20px;margin:6px 0 2px}p{color:#6b6b6b;font-size:13px;margin:0 0 18px}
input{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border:1px solid #cfc7bd;border-radius:8px;margin-bottom:12px}
button{width:100%;padding:12px;font-size:16px;border:0;border-radius:8px;background:#e67e22;color:#fff;font-weight:600}
.err{color:#c0392b;font-size:13px;margin-bottom:10px}</style></head><body>
<form method="post"><div style="font-size:44px">🧅</div><h1>Onion Route Planner</h1><p>Enter the team password</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<input type="password" name="password" placeholder="Password" autofocus required autocomplete="current-password">
<button type="submit">Sign in</button></form></body></html>"""


@app.before_request
def _require_login():
    if not APP_PASSWORD:
        return None
    if request.endpoint in ("login", "static", "health") or request.path.startswith("/static/"):
        return None
    if session.get("auth") == _AUTH_TAG:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not signed in – reload the page"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect("/")
    error = ""
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", ""), APP_PASSWORD):
            session.permanent = True
            session["auth"] = _AUTH_TAG
            nxt = request.args.get("next") or "/"
            return redirect(nxt if nxt.startswith("/") else "/")
        time.sleep(1.0)   # slow down guessing
        error = "Wrong password"
    return render_template_string(LOGIN_HTML, error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login") if APP_PASSWORD else "/")


@app.get("/health")
def health():
    return jsonify({"ok": True, "storage": storage.backend_description()})

# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    s = storage.get_settings()
    s["_cache"] = CACHE.stats()
    s["_auth_enabled"] = bool(APP_PASSWORD)
    s["_storage"] = storage.backend_description()
    return jsonify(s)


@app.put("/api/settings")
def put_settings():
    s = storage.get_settings()
    body = request.get_json(force=True) or {}
    for k in ("_cache", "_auth_enabled", "_storage"):
        body.pop(k, None)
    s.update(body)
    # sanity
    s["vehicles"] = [v for v in s.get("vehicles", []) if float(v.get("capacity_kg") or 0) > 0]
    for i, v in enumerate(s["vehicles"]):
        v.setdefault("id", f"V{i + 1}")
        v["name"] = v.get("name") or f"Vehicle {i + 1}"
        v["capacity_kg"] = int(float(v["capacity_kg"]))
    if len(s.get("traffic_profile") or []) != 24:
        s["traffic_profile"] = storage.CHENNAI_PROFILE
    storage.save("settings", s)
    return jsonify(s)


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------
@app.get("/api/customers")
def list_customers():
    return jsonify(storage.get_customers())


@app.put("/api/customers")
def save_customers():
    body = request.get_json(force=True) or []
    out = []
    for c in body:
        if not (c.get("name") or "").strip():
            continue
        c.setdefault("id", uuid.uuid4().hex[:8])
        for k in ("lat", "lng"):
            try:
                c[k] = float(c[k]) if c.get(k) not in (None, "") else None
            except (TypeError, ValueError):
                c[k] = None
        out.append(c)
    storage.save("customers", out)
    return jsonify(out)


@app.post("/api/geocode")
def api_geocode():
    body = request.get_json(force=True) or {}
    res = geocode(body.get("address", ""), storage.get_settings())
    if not res:
        return jsonify({"error": "Address not found. Try 'Area, Landmark' or paste a Google Maps link / lat,lng."}), 404
    return jsonify(res)


# ---------------------------------------------------------------------------
# daily orders (persisted so a refresh doesn't lose them)
# ---------------------------------------------------------------------------
@app.get("/api/orders")
def get_orders():
    return jsonify(storage.load("orders", {"date": dt.date.today().isoformat(), "orders": []}))


@app.put("/api/orders")
def put_orders():
    body = request.get_json(force=True) or {}
    storage.save("orders", body)
    return jsonify({"ok": True})


@app.get("/api/template.xlsx")
def template():
    return send_file(io.BytesIO(build_template(storage.get_customers())),
                     as_attachment=True, download_name="onion_orders_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/import")
def import_orders_endpoint():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    data = f.read()
    customers = storage.get_customers()
    settings = storage.get_settings()
    c_created, c_updated, problems = import_customers(parse_customers_xlsx(data), customers, settings)
    orders, o_problems, o_created, o_updated = import_orders(parse_orders_xlsx(data), customers, settings)
    created, updated = c_created + o_created, c_updated + o_updated
    if created or updated:
        storage.save("customers", customers)
    return jsonify({"orders": orders, "problems": problems + o_problems, "customers_created": created,
                    "customers_updated": updated, "customers": customers})


# ---------------------------------------------------------------------------
# the optimiser
# ---------------------------------------------------------------------------
@app.post("/api/optimize")
def optimize():
    body = request.get_json(force=True) or {}
    settings = storage.get_settings()
    settings.update(body.get("settings") or {})      # per-run overrides (date, departure, strategy…)
    try:
        return jsonify(plan(body.get("orders") or [], settings, storage.get_customers()))
    except PlanError as e:
        return jsonify({"error": e.message, **e.extra}), e.status


@app.post("/api/export.xlsx")
def export_xlsx():
    result = request.get_json(force=True)
    data = build_route_workbook(result)
    name = f"onion_routes_{result['summary']['date']}.xlsx"
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Onion Route Planner → http://localhost:{port}   (storage: {storage.backend_description()}; "
          f"login: {'ON' if APP_PASSWORD else 'off – set APP_PASSWORD to enable'})\n")
    try:
        from waitress import serve          # production-grade server, works on Windows too
        serve(app, host="0.0.0.0", port=port, threads=6)
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
