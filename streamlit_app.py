"""
streamlit_app.py – Streamlit front end for the Onion Route Planner.

Runs on Streamlit Community Cloud (free) with a free Neon Postgres database:

    secrets (Streamlit Cloud → app → Settings → Secrets):
        APP_PASSWORD        = "team password"
        DATABASE_URL        = "postgresql://…neon.tech/neondb?sslmode=require"
        GOOGLE_MAPS_API_KEY = ""          # optional – historic-traffic routing

Locally:  streamlit run streamlit_app.py      (uses ./data/*.json like the Flask app)

Same engine as the Flask app (planner.py / solver.py / matrix.py) – only the screens differ.
"""
import datetime as dt
import hashlib
import hmac
import math
import os
import re
import time
import urllib.parse
import uuid

import streamlit as st

st.set_page_config(page_title="Onion Route Planner", page_icon="🧅", layout="wide",
                   initial_sidebar_state="collapsed",
                   menu_items={"About": "Onion Route Planner – multi-trip CVRPTW with OR-Tools."})


# ---------------------------------------------------------------------------
# Secrets → environment.  MUST happen before `import storage` (it reads
# DATABASE_URL / DATA_DIR at import time).
# ---------------------------------------------------------------------------
def _secrets_to_env():
    try:
        for k in ("APP_PASSWORD", "DATABASE_URL", "GOOGLE_MAPS_API_KEY", "SECRET_KEY", "DATA_DIR"):
            if k in st.secrets and str(st.secrets[k]).strip() and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k]).strip()
    except Exception:          # no secrets.toml locally – that's fine
        pass


_secrets_to_env()

import folium  # noqa: E402
import pandas as pd  # noqa: E402
from streamlit_folium import st_folium  # noqa: E402

import storage  # noqa: E402
from export_xlsx import build_route_workbook, build_template, parse_orders_xlsx  # noqa: E402
from geocode import geocode  # noqa: E402
from matrix import CACHE  # noqa: E402
from planner import PlanError, import_orders, plan  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
STRATEGIES = {"lowest_cost": "Lowest cost (km / time)", "fewest_vehicles": "Fewest vehicles",
              "balanced": "Balanced workload"}
OBJECTIVES = {"time": "Travel time (traffic-aware)", "distance": "Distance (km)"}
TRAFFIC_SOURCES = {"auto": "Auto (Google if key set, else OSRM)", "google": "Google Maps (historic traffic)",
                   "osrm": "OSRM + Chennai hour profile (free)", "haversine": "Straight-line estimate (offline)"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

st.markdown("""
<style>
  .block-container {padding-top: 2.6rem; padding-bottom: 2rem;}
  div[data-testid="stMetric"] {background: var(--secondary-background-color); border-radius: 10px; padding: 8px 12px;}
  .chip {display:inline-block; padding:2px 10px; border-radius:12px; color:#fff; font-size:12px; margin:2px 4px 2px 0;}
  @media (max-width: 640px) { .block-container {padding-left: .6rem; padding-right: .6rem;} }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Login (shared team password).  ?k=<key> in the URL keeps a bookmark signed in.
# ---------------------------------------------------------------------------
def _link_key(pw: str) -> str:
    return hashlib.sha256(f"onion-route-planner:{pw}".encode()).hexdigest()[:16]


def require_login():
    if not APP_PASSWORD or st.session_state.get("authed"):
        return
    k = st.query_params.get("k", "")
    if k and hmac.compare_digest(k, _link_key(APP_PASSWORD)):
        st.session_state.authed = True
        return
    st.markdown("## 🧅 Onion Route Planner")
    st.caption("Daily delivery route planning · Chennai")
    with st.form("login"):
        pw = st.text_input("Team password", type="password")
        remember = st.checkbox("Keep me signed in on this device (adds a key to the link – bookmark it)", value=True)
        ok = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if ok:
        if pw and hmac.compare_digest(pw.strip(), APP_PASSWORD):
            st.session_state.authed = True
            if remember:
                st.query_params["k"] = _link_key(APP_PASSWORD)
            st.rerun()
        st.error("Wrong password.")
    st.stop()


require_login()


# ---------------------------------------------------------------------------
# Data (cached per browser session; "Reload" pulls the latest from storage)
# ---------------------------------------------------------------------------
def _default_date() -> str:
    now = dt.datetime.now(IST)
    return (now + dt.timedelta(days=1 if now.hour >= 12 else 0)).date().isoformat()


def load_all(force=False):
    ss = st.session_state
    if force or "customers" not in ss:
        ss.customers = storage.get_customers()
        ss.settings = storage.get_settings()
        ss.orders_doc = storage.load("orders", {"date": _default_date(), "orders": []}) or {}
        ss.orders_doc.setdefault("orders", [])
        ss.orders_doc.setdefault("date", _default_date())
        ss.loaded_at = dt.datetime.now(IST).strftime("%H:%M")
        bump("orders_ver")
        bump("cust_ver")
        bump("set_ver")


def bump(name):
    st.session_state[name] = st.session_state.get(name, 0) + 1


def save_orders():
    storage.save("orders", st.session_state.orders_doc)


def cust_label(c) -> str:
    return c["name"] if not c.get("area") else f"{c['name']} ({c['area']})"


def isblank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        return bool(pd.isna(v))
    except Exception:
        return False


def to_time(s):
    """'06:30' → datetime.time; None when blank/invalid."""
    try:
        h, m = str(s).strip().split(":")[:2]
        return dt.time(int(h), int(m))
    except Exception:
        return None


def to_hhmm(v) -> str:
    if isblank(v):
        return ""
    if isinstance(v, (dt.time, dt.datetime, pd.Timestamp)):
        return v.strftime("%H:%M")
    s = str(v).strip()
    return s[:5] if re.match(r"^\d{1,2}:\d{2}", s) else ""


load_all()
ss = st.session_state
customers = ss.customers
settings = ss.settings
cust_by_id = {c["id"]: c for c in customers}
labels = sorted((cust_label(c) for c in customers), key=str.lower)
label_to_cust = {cust_label(c): c for c in customers}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🧅 Onion Route Planner")
    st.caption(f"Storage: **{storage.backend_description()}**  \nData loaded at {ss.loaded_at} IST")
    if st.button("🔄 Reload data", use_container_width=True, help="Pull the latest orders / customers saved by a colleague"):
        load_all(force=True)
        st.rerun()
    if APP_PASSWORD and st.button("Sign out", use_container_width=True):
        ss.authed = False
        st.query_params.clear()
        st.rerun()


if not os.environ.get("DATABASE_URL") and os.getcwd().startswith("/mount/src"):   # Streamlit Community Cloud
    st.warning("**No DATABASE_URL secret** – this server's disk is wiped on every restart, so customers and orders "
               "will be lost. Add a free Neon Postgres database (see DEPLOY.md §F.1) under *Settings → Secrets*.")

PAGES = ["📦 Plan", "🗺️ Map", "👥 Customers", "⚙️ Settings", "❓ Help"]
h1, h2 = st.columns([1.4, 2.3], vertical_alignment="center")
with h1:
    st.markdown("## 🧅 Onion Route Planner")
with h2:
    # one page per rerun (lighter than st.tabs, and Leaflet gets a visible container for fit_bounds)
    page = st.segmented_control("Page", PAGES, default=PAGES[0], key="page", label_visibility="collapsed") or PAGES[0]


# ===========================================================================
# 📦 PLAN
# ===========================================================================
ORDER_COLS = ["Customer", "Qty (kg)", "From", "To", "Service (min)", "Notes", "_id"]


def orders_to_df(orders):
    rows = []
    for o in orders:
        c = cust_by_id.get(o.get("customer_id"))
        if not c:
            continue
        sm = str(o.get("service_min") or "").strip()
        rows.append({"Customer": cust_label(c), "Qty (kg)": float(o.get("qty_kg") or 0) or None,
                     "From": to_time(o.get("tw_from")), "To": to_time(o.get("tw_to")),
                     "Service (min)": int(float(sm)) if sm else None, "Notes": o.get("notes") or "", "_id": o.get("id")})
    df = pd.DataFrame(rows, columns=ORDER_COLS)
    for col in ("From", "To"):
        df[col] = df[col].astype(object)
    return df


def df_to_orders(df):
    """Editor dataframe → orders list. Also returns True if a new row was auto-filled with customer defaults."""
    out, autofilled = [], False
    for _, r in df.iterrows():
        c = label_to_cust.get(r.get("Customer")) if not isblank(r.get("Customer")) else None
        if not c:
            continue
        qty, f, t, sm = r.get("Qty (kg)"), r.get("From"), r.get("To"), r.get("Service (min)")
        if isblank(qty) and isblank(f) and isblank(t):          # freshly added row → customer's regular order
            qty, f, t, autofilled = c.get("default_qty_kg") or 0, c.get("default_tw_from") or "", c.get("default_tw_to") or "", True
        out.append({"id": r["_id"] if not isblank(r.get("_id")) else uuid.uuid4().hex[:8],
                    "customer_id": c["id"], "qty_kg": float(qty or 0),
                    "tw_from": to_hhmm(f), "tw_to": to_hhmm(t),
                    "service_min": "" if isblank(sm) else int(float(sm)),
                    "notes": "" if isblank(r.get("Notes")) else str(r.get("Notes")).strip()})
    return out, autofilled


if page == PAGES[0]:
    doc = ss.orders_doc
    c1, c2, c3, c4 = st.columns([1.1, 1, 1.4, 1.4])
    with c1:
        d = st.date_input("Delivery date", value=dt.date.fromisoformat(doc["date"]), format="DD/MM/YYYY")
        if d.isoformat() != doc["date"]:
            doc["date"] = d.isoformat()
            save_orders()
    with c2:
        dep = st.time_input("Leave depot", value=to_time(settings.get("departure_time")) or dt.time(5, 0), step=900)
    with c3:
        strategy = st.selectbox("Strategy", list(STRATEGIES), index=list(STRATEGIES).index(settings.get("strategy", "lowest_cost")),
                                format_func=STRATEGIES.get)
    with c4:
        objective = st.selectbox("Minimise", list(OBJECTIVES), index=list(OBJECTIVES).index(settings.get("objective", "time")),
                                 format_func=OBJECTIVES.get)

    # ---- order list -------------------------------------------------------
    st.markdown(f"#### Orders for {d.strftime('%a %d %b %Y')}")
    if not customers:
        st.info("Add customers in the 👥 Customers tab first.")
    df = orders_to_df(doc["orders"])
    edited = st.data_editor(
        df, key=f"orders_editor_{ss.orders_ver}", num_rows="dynamic", hide_index=True, use_container_width=True,
        column_config={
            "Customer": st.column_config.SelectboxColumn("Customer", options=labels, required=True, width="medium"),
            "Qty (kg)": st.column_config.NumberColumn("Qty (kg)", min_value=0, step=10, format="%d"),
            "From": st.column_config.TimeColumn("From", format="HH:mm", step=300, help="Earliest delivery time (blank = any)"),
            "To": st.column_config.TimeColumn("To", format="HH:mm", step=300, help="Latest delivery time (blank = any)"),
            "Service (min)": st.column_config.NumberColumn("Service", min_value=0, max_value=180, step=5,
                                                           help=f"Minutes at the stop (blank = {settings.get('default_service_min', 10)})"),
            "Notes": st.column_config.TextColumn("Notes", width="medium"),
            "_id": None,
        },
    )
    new_orders, autofilled = df_to_orders(edited)
    if new_orders != doc["orders"]:
        doc["orders"] = new_orders
        save_orders()
        ss.result = None                                 # plan is stale
        if autofilled:
            bump("orders_ver")
            st.rerun()

    total_kg = sum(o["qty_kg"] for o in doc["orders"])
    cap = sum(float(v.get("capacity_kg") or 0) for v in settings.get("vehicles", []))
    trips = max(1, int(settings.get("trips_per_vehicle") or 1))
    n_ord = len(doc["orders"])
    st.caption(f"**{n_ord} order{'s' if n_ord != 1 else ''} · {total_kg:g} kg** — fleet {cap:g} kg × {trips} trip(s) = {cap * trips:g} kg/day. "
               "Pick a customer in the empty last row to add an order (quantity & window fill in from the customer card).")

    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if st.button("➕ Add all regulars", use_container_width=True, help="One order per customer with their usual quantity"):
            have = {o["customer_id"] for o in doc["orders"]}
            for c in customers:
                if c["id"] not in have and float(c.get("default_qty_kg") or 0) > 0:
                    doc["orders"].append({"id": uuid.uuid4().hex[:8], "customer_id": c["id"],
                                          "qty_kg": float(c["default_qty_kg"]), "tw_from": c.get("default_tw_from") or "",
                                          "tw_to": c.get("default_tw_to") or "", "service_min": "", "notes": ""})
            save_orders()
            bump("orders_ver")
            ss.result = None
            st.rerun()
    with b2:
        if st.button("🗑️ Clear orders", use_container_width=True, disabled=not doc["orders"]):
            doc["orders"] = []
            save_orders()
            bump("orders_ver")
            ss.result = None
            st.rerun()
    with b3:
        st.download_button("📄 Excel template", data=build_template(customers), file_name="orders_template.xlsx",
                           mime=XLSX, use_container_width=True)
    with b4:
        with st.popover("📥 Import Excel", use_container_width=True):
            up = st.file_uploader("Filled template (.xlsx)", type=["xlsx"], label_visibility="collapsed")
            if up is not None and ss.get("last_upload") != up.file_id:
                ss.last_upload = up.file_id
                try:
                    with st.spinner("Importing…"):
                        raw = parse_orders_xlsx(up.getvalue())
                        imp, problems, created = import_orders(raw, customers, settings)
                    if created:
                        storage.save("customers", customers)
                    ss.import_msg = (len(imp), problems, created)
                    doc["orders"] = imp
                    save_orders()
                    bump("orders_ver")
                    bump("cust_ver")
                    ss.result = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Import failed: {e}")
    if ss.get("import_msg"):
        n, problems, created = ss.pop("import_msg")
        st.success(f"Imported {n} orders" + (f", created {created} new customer(s)" if created else "") + ".")
        for p in problems:
            st.warning(p)

    # ---- optimise ---------------------------------------------------------
    st.divider()
    if st.button("⚡ Optimise routes", type="primary", use_container_width=True, disabled=not doc["orders"]):
        run_settings = dict(settings)
        run_settings.update({"date": doc["date"], "departure_time": dep.strftime("%H:%M"),
                             "strategy": strategy, "objective": objective})
        with st.spinner("Building travel-time matrix and solving… (usually 10–20 s)"):
            try:
                ss.result = plan(doc["orders"], run_settings, customers)
                ss.result_xlsx = build_route_workbook(ss.result)
                ss.result_error = None
            except PlanError as e:
                ss.result, ss.result_error = None, (e.message, e.extra)
            except Exception as e:                       # pragma: no cover
                ss.result, ss.result_error = None, (f"Unexpected error: {e}", {})
        CACHE.flush()

    if ss.get("result_error"):
        msg, extra = ss.result_error
        st.error(msg)
        if extra.get("dropped"):
            st.dataframe(pd.DataFrame(extra["dropped"])[["customer", "qty_kg", "window", "reason"]],
                         hide_index=True, use_container_width=True)
        if extra.get("missing"):
            st.warning("Customers without coordinates: " + ", ".join(extra["missing"]) +
                       " → fix them in 👥 Customers (📍 Geocode missing).")

    res = ss.get("result")
    if res:
        sm = res["summary"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total distance", f"{sm['total_km']:g} km",
                  delta=f"-{sm['saving_km']:g} km vs nearest-first ({sm['saving_pct']:g}%)" if sm.get("saving_km") else None,
                  delta_color="inverse" if sm.get("saving_km", 0) < 0 else "normal")
        k2.metric("Driving time", f"{sm['total_drive_min']:g} min", delta=f"{sm['baseline_drive_min']:g} min nearest-first",
                  delta_color="off")
        k3.metric("Vehicles · trips", f"{sm['vehicles_used']} of {sm['vehicles_total']} · {sm['trips']}")
        k4.metric("Load / fill", f"{sm['load_kg']:g} kg · {sm['utilisation_pct']:g}%",
                  delta=f"{sm['stops_served']} stops" + (f", {sm['stops_dropped']} dropped" if sm["stops_dropped"] else ""),
                  delta_color="inverse" if sm["stops_dropped"] else "off")
        st.caption(f"Travel times: **{sm['matrix_source']}** (traffic factor ×{sm['traffic_factor']}) · "
                   f"{sm.get('matrix_note', '')} · solved in {sm['solve_seconds']} s · "
                   f"{STRATEGIES.get(sm['strategy'], sm['strategy'])}, minimising {sm['objective']}.")
        for w in res.get("warnings", []):
            st.warning(w)
        if res.get("dropped"):
            st.error(f"{len(res['dropped'])} order(s) could not be fitted – add a vehicle/trip, widen the window or deliver tomorrow:")
            st.dataframe(pd.DataFrame(res["dropped"])[["customer", "qty_kg", "window", "reason"]],
                         hide_index=True, use_container_width=True)

        dl1, dl2 = st.columns([1, 2])
        with dl1:
            st.download_button("⬇️ Download Excel plan", data=ss.get("result_xlsx") or build_route_workbook(res),
                               file_name=f"routes_{sm['date']}.xlsx", mime=XLSX, use_container_width=True)
        with dl2:
            st.markdown("".join(f'<span class="chip" style="background:{r["color"]}">{r["vehicle"]["name"]}</span>'
                                for r in res["routes"] if r["used"]), unsafe_allow_html=True)

        for rt in res["routes"]:
            if not rt["used"]:
                continue
            v = rt["vehicle"]
            title = (f"🚚 {v['name']} — {rt['total_km']:g} km · {len(rt['stops'])} stops · "
                     f"{rt['load_kg']:g}/{rt['capacity_kg']:g} kg · leave {rt['suggested_departure']} → back {rt['return']['arrival']}")
            with st.expander(title, expanded=True):
                rows = [{"#": s["seq"], "Trip": s["trip"], "Customer": s["customer"], "Area": s.get("area") or "",
                         "kg": s["qty_kg"], "Window": s.get("window") or "any", "Arrive": s["arrival"],
                         "Leave": s["depart"], "Wait": s.get("wait_min") or 0, "Leg km": s["leg_km"], "Cum km": s["cum_km"]}
                        for s in rt["stops"]]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                             column_config={"kg": st.column_config.NumberColumn(format="%g"),
                                            "Leg km": st.column_config.NumberColumn(format="%.1f"),
                                            "Cum km": st.column_config.NumberColumn(format="%.1f")})
                if len(rt["trips"]) > 1:
                    st.caption(" · ".join(f"Trip {t['trip']}: leave {t['depart']} with {t['load_kg']:g} kg, "
                                          f"{t['total_km']:g} km, back {t['return']['arrival']}" for t in rt["trips"]))
                l1, l2 = st.columns([1, 1])
                with l1:
                    st.link_button("💬 Send to driver on WhatsApp",
                                   "https://wa.me/?text=" + urllib.parse.quote(rt["text"]), use_container_width=True)
                with l2:
                    urls = rt["gmaps_urls"]
                    for i, u in enumerate(urls, 1):
                        st.link_button(f"🧭 Google Maps navigation{f' (part {i}/{len(urls)})' if len(urls) > 1 else ''}", u,
                                       use_container_width=True)
                st.code(rt["text"], language=None)   # has a copy button

        unused = [r["vehicle"]["name"] for r in res["routes"] if not r["used"]]
        if unused:
            st.info("Not needed today: " + ", ".join(unused))


# ===========================================================================
# 🗺️ MAP
# ===========================================================================
def build_map(res):
    depot = settings.get("depot") or {}
    lat0, lng0 = float(depot.get("lat") or 13.0694), float(depot.get("lng") or 80.1948)
    m = folium.Map(location=[lat0, lng0], zoom_start=11, control_scale=True, tiles="OpenStreetMap")
    folium.Marker([lat0, lng0], tooltip=depot.get("name") or "Depot",
                  icon=folium.Icon(color="black", icon="home", prefix="fa")).add_to(m)
    bounds = [[lat0, lng0]]
    if res:
        for rt in res["routes"]:
            if not rt["used"]:
                continue
            col = rt["color"]
            for tr in rt["trips"]:
                geom = tr.get("geometry") or []
                if geom:
                    folium.PolyLine(geom, color=col, weight=4, opacity=0.85,
                                    dash_array="8 8" if tr["trip"] > 1 else None,
                                    tooltip=f"{rt['vehicle']['name']} – trip {tr['trip']} ({tr['total_km']:g} km)").add_to(m)
                    bounds += geom[:: max(1, len(geom) // 50)]
                for s in tr["stops"]:
                    html = (f'<div style="background:{col};color:#fff;border-radius:50%;width:26px;height:26px;'
                            f'line-height:24px;text-align:center;font-weight:700;font-size:12px;border:2px solid #fff;'
                            f'box-shadow:0 1px 4px rgba(0,0,0,.5)">{s["seq"]}</div>')
                    popup = (f"<b>{s['seq']}. {s['customer']}</b><br>{s.get('area') or ''}<br>{s['qty_kg']:g} kg · "
                             f"window {s.get('window') or 'any'}<br>Arrive {s['arrival']} · leave {s['depart']}<br>"
                             f"{rt['vehicle']['name']}, trip {s['trip']}")
                    folium.Marker([s["lat"], s["lng"]], tooltip=f"{s['seq']}. {s['customer']} – {s['qty_kg']:g} kg",
                                  popup=folium.Popup(popup, max_width=260),
                                  icon=folium.DivIcon(html=html, icon_size=(26, 26), icon_anchor=(13, 13))).add_to(m)
                    bounds.append([s["lat"], s["lng"]])
    else:
        for c in customers:
            if c.get("lat") and c.get("lng"):
                folium.CircleMarker([c["lat"], c["lng"]], radius=6, color="#8b4513", fill=True, fill_opacity=0.8,
                                    tooltip=f"{c['name']} ({c.get('area') or ''})").add_to(m)
                bounds.append([c["lat"], c["lng"]])
    if len(bounds) > 1:
        m.fit_bounds(bounds, padding=(25, 25), max_zoom=14)
    return m


if page == PAGES[1]:
    res = ss.get("result")
    if res:
        sm = res["summary"]
        st.caption(f"{sm['date']} · {sm['total_km']:g} km · {sm['vehicles_used']} vehicle(s), {sm['trips']} trip(s). "
                   "Solid line = trip 1, dashed = trip 2. Tap a number for details.")
        st.markdown("".join(f'<span class="chip" style="background:{r["color"]}">{r["vehicle"]["name"]}</span>'
                            for r in res["routes"] if r["used"]), unsafe_allow_html=True)
    else:
        st.caption("No plan yet – showing the depot and all customers with coordinates. Run ⚡ Optimise in the Plan tab.")
    st_folium(build_map(res), height=560, use_container_width=True, returned_objects=[])
    missing = [c["name"] for c in customers if not (c.get("lat") and c.get("lng"))]
    if missing:
        st.warning("No coordinates yet (cannot be routed): " + ", ".join(missing))


# ===========================================================================
# 👥 CUSTOMERS
# ===========================================================================
CUST_COLS = ["name", "area", "address", "phone", "default_qty_kg", "default_tw_from", "default_tw_to", "lat", "lng", "notes", "id"]

if page == PAGES[2]:
    st.caption("Edit directly in the table (add rows at the bottom, select a row + Delete key to remove), then **Save**. "
               "Leave lat/lng empty and use **Geocode missing**, or paste `13.0827, 80.2707` / a Google Maps link in **Find coordinates**.")
    cdf = pd.DataFrame(customers, columns=CUST_COLS)
    ced = st.data_editor(
        cdf, key=f"cust_editor_{ss.cust_ver}", num_rows="dynamic", hide_index=True, use_container_width=True, height=420,
        column_config={
            "name": st.column_config.TextColumn("Customer", required=True, width="medium"),
            "area": st.column_config.TextColumn("Area"),
            "address": st.column_config.TextColumn("Address", width="large"),
            "phone": st.column_config.TextColumn("Phone"),
            "default_qty_kg": st.column_config.NumberColumn("Usual kg", min_value=0, step=10, format="%g"),
            "default_tw_from": st.column_config.TextColumn("Window from", help="HH:MM, blank = any"),
            "default_tw_to": st.column_config.TextColumn("Window to", help="HH:MM, blank = any"),
            "lat": st.column_config.NumberColumn("Lat", format="%.5f"),
            "lng": st.column_config.NumberColumn("Lng", format="%.5f"),
            "notes": st.column_config.TextColumn("Notes"),
            "id": None,
        },
    )

    def _clean_customers(df):
        out, errs = [], []
        for _, r in df.iterrows():
            name = "" if isblank(r.get("name")) else str(r["name"]).strip()
            if not name:
                continue
            c = {"id": r["id"] if not isblank(r.get("id")) else uuid.uuid4().hex[:8], "name": name}
            for k in ("area", "address", "phone", "notes"):
                c[k] = "" if isblank(r.get(k)) else str(r[k]).strip()
            for k in ("default_tw_from", "default_tw_to"):
                v = to_hhmm(r.get(k))
                if not isblank(r.get(k)) and str(r.get(k)).strip() and not v:
                    errs.append(f"{name}: '{r.get(k)}' is not a HH:MM time")
                c[k] = v
            c["default_qty_kg"] = 0 if isblank(r.get("default_qty_kg")) else float(r["default_qty_kg"])
            for k in ("lat", "lng"):
                c[k] = None if isblank(r.get(k)) else float(r[k])
            out.append(c)
        return out, errs

    s1, s2, s3 = st.columns([1, 1, 2])
    with s1:
        if st.button("💾 Save customers", type="primary", use_container_width=True):
            new, errs = _clean_customers(ced)
            if errs:
                for e in errs:
                    st.error(e)
            else:
                storage.save("customers", new)
                ss.customers = new
                bump("cust_ver")
                bump("orders_ver")
                st.success(f"Saved {len(new)} customers.")
                time.sleep(0.6)
                st.rerun()
    with s2:
        if st.button("📍 Geocode missing", use_container_width=True, help="Look up coordinates for customers without lat/lng"):
            new, errs = _clean_customers(ced)
            todo = [c for c in new if not (c.get("lat") and c.get("lng"))]
            if not todo:
                st.info("Everyone already has coordinates.")
            else:
                found, failed = 0, []
                prog = st.progress(0.0, text="Geocoding…")
                for i, c in enumerate(todo, 1):
                    q = c.get("address") or f"{c['name']}, {c.get('area') or ''}, Chennai"
                    g = geocode(q, settings)
                    if g:
                        c["lat"], c["lng"], found = g["lat"], g["lng"], found + 1
                    else:
                        failed.append(c["name"])
                    prog.progress(i / len(todo), text=f"Geocoding… {i}/{len(todo)}")
                prog.empty()
                storage.save("customers", new)
                ss.customers = new
                bump("cust_ver")
                ss.geocode_msg = (found, failed)
                st.rerun()
    if ss.get("geocode_msg"):
        found, failed = ss.pop("geocode_msg")
        st.success(f"Found coordinates for {found} customer(s).")
        if failed:
            st.warning("Not found (use Find coordinates and paste lat,lng): " + ", ".join(failed))

    with st.expander("🔎 Find coordinates (address, landmark, 'lat, lng' or a Google Maps link)"):
        q = st.text_input("Search", placeholder="e.g. Saravana Bhavan, Anna Nagar, Chennai")
        if st.button("Search") and q.strip():
            g = geocode(q.strip(), settings)
            if g:
                st.success(f"**{g['lat']:.5f}, {g['lng']:.5f}** — {g.get('display', '')} ({g.get('source')})  \n"
                           f"Copy these into the Lat / Lng columns above. "
                           f"[Check on Google Maps](https://www.google.com/maps/search/?api=1&query={g['lat']},{g['lng']})")
            else:
                st.error("Not found. Try 'Area, Landmark, Chennai' or paste coordinates from Google Maps "
                         "(long-press the spot → copy the numbers).")


# ===========================================================================
# ⚙️ SETTINGS
# ===========================================================================
if page == PAGES[3]:
    with st.form("settings_form"):
        st.markdown("#### Depot")
        d1, d2 = st.columns([2, 1])
        depot = dict(settings.get("depot") or {})
        with d1:
            depot_name = st.text_input("Name", depot.get("name", ""))
            depot_addr = st.text_input("Address", depot.get("address", ""))
        with d2:
            depot_lat = st.number_input("Lat", value=float(depot.get("lat") or 13.0694), format="%.5f")
            depot_lng = st.number_input("Lng", value=float(depot.get("lng") or 80.1948), format="%.5f")

        st.markdown("#### Vehicles")
        vdf = pd.DataFrame(settings.get("vehicles") or [], columns=["id", "name", "capacity_kg"])
        ved = st.data_editor(vdf, key=f"veh_editor_{ss.set_ver}", num_rows="dynamic", hide_index=True, use_container_width=True,
                             column_config={"id": st.column_config.TextColumn("ID", width="small"),
                                            "name": st.column_config.TextColumn("Name", required=True),
                                            "capacity_kg": st.column_config.NumberColumn("Capacity (kg)", min_value=1, step=50, format="%d")})

        st.markdown("#### Daily defaults")
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            dep_default = st.time_input("Departure", value=to_time(settings.get("departure_time")) or dt.time(5, 0), step=900)
            trips_pv = st.number_input("Trips per vehicle", 1, 4, int(settings.get("trips_per_vehicle") or 1))
        with e2:
            max_h = st.number_input("Max route hours", 1.0, 14.0, float(settings.get("max_route_hours") or 6), step=0.5)
            reload_min = st.number_input("Reload time at depot (min)", 0, 120, int(settings.get("reload_min") or 20), step=5)
        with e3:
            service_min = st.number_input("Default service min / stop", 1, 90, int(settings.get("default_service_min") or 10))
            solver_s = st.number_input("Solver seconds", 2, 60, int(settings.get("solver_seconds") or 8))
        with e4:
            strat_default = st.selectbox("Default strategy", list(STRATEGIES), format_func=STRATEGIES.get,
                                         index=list(STRATEGIES).index(settings.get("strategy", "lowest_cost")))
            obj_default = st.selectbox("Default objective", list(OBJECTIVES), format_func=OBJECTIVES.get,
                                       index=list(OBJECTIVES).index(settings.get("objective", "time")))

        st.markdown("#### Traffic & travel times")
        t1, t2, t3 = st.columns([1.3, 1, 1])
        with t1:
            src = st.selectbox("Source", list(TRAFFIC_SOURCES), format_func=TRAFFIC_SOURCES.get,
                               index=list(TRAFFIC_SOURCES).index(settings.get("traffic_source", "auto")))
            env_key = bool(os.environ.get("GOOGLE_MAPS_API_KEY"))
            gkey = st.text_input("Google Maps API key", settings.get("google_api_key") or "", type="password",
                                 help="Leave empty to use the key from the GOOGLE_MAPS_API_KEY secret" + (" (set)" if env_key else " (not set)"))
        with t2:
            tmodel = st.selectbox("Google traffic model", ["best_guess", "pessimistic", "optimistic"],
                                  index=["best_guess", "pessimistic", "optimistic"].index(settings.get("traffic_model", "best_guess")))
            osrm_f = st.number_input("OSRM base factor", 0.8, 2.0, float(settings.get("osrm_base_factor") or 1.1), step=0.05,
                                     help="OSRM free-flow time × this = typical night-time driving")
        with t3:
            base_speed = st.number_input("Fallback speed km/h", 10, 60, int(settings.get("base_speed_kmh") or 30),
                                         help="Only used for the straight-line estimate")
        with st.expander("Hourly congestion profile (× free-flow time, 0–23 h)"):
            prof = settings.get("traffic_profile") or storage.CHENNAI_PROFILE
            pdf = pd.DataFrame({"Hour": [f"{h:02d}:00" for h in range(24)], "Factor": [float(x) for x in prof[:24]]})
            ped = st.data_editor(pdf, key=f"prof_editor_{ss.set_ver}", hide_index=True, use_container_width=True, height=300,
                                 column_config={"Hour": st.column_config.TextColumn(disabled=True),
                                                "Factor": st.column_config.NumberColumn(min_value=0.5, max_value=4.0, step=0.05, format="%.2f")},
                                 disabled=["Hour"])

        if st.form_submit_button("💾 Save settings", type="primary", use_container_width=True):
            s = dict(settings)
            s["depot"] = {"name": depot_name.strip() or "Depot", "address": depot_addr.strip(), "lat": depot_lat, "lng": depot_lng}
            vehicles = []
            for i, r in ved.reset_index(drop=True).iterrows():
                if isblank(r.get("capacity_kg")) or float(r["capacity_kg"]) <= 0:
                    continue
                vehicles.append({"id": (str(r["id"]).strip() if not isblank(r.get("id")) and str(r["id"]).strip() else f"V{i + 1}"),
                                 "name": (str(r["name"]).strip() if not isblank(r.get("name")) else "") or f"Vehicle {i + 1}",
                                 "capacity_kg": int(float(r["capacity_kg"]))})
            s["vehicles"] = vehicles
            s.update({"departure_time": dep_default.strftime("%H:%M"), "trips_per_vehicle": int(trips_pv),
                      "max_route_hours": float(max_h), "reload_min": int(reload_min), "default_service_min": int(service_min),
                      "solver_seconds": int(solver_s), "strategy": strat_default, "objective": obj_default,
                      "traffic_source": src, "google_api_key": gkey.strip(), "traffic_model": tmodel,
                      "osrm_base_factor": float(osrm_f), "base_speed_kmh": int(base_speed)})
            factors = [float(x) for x in ped["Factor"].tolist()]
            s["traffic_profile"] = factors if len(factors) == 24 and all(f > 0 for f in factors) else storage.CHENNAI_PROFILE
            if not vehicles:
                st.error("Add at least one vehicle with a capacity.")
            else:
                storage.save("settings", s)
                ss.settings = s
                ss.result = None
                bump("set_ver")
                st.success("Settings saved.")
                time.sleep(0.6)
                st.rerun()

    st.caption(f"Storage: {storage.backend_description()} · travel-time cache: {CACHE.stats()['pairs_cached']} pairs · "
               f"password protection: {'on' if APP_PASSWORD else 'OFF (set APP_PASSWORD)'}")


# ===========================================================================
# ❓ HELP
# ===========================================================================
if page == PAGES[4]:
    st.markdown(f"""
#### Daily routine (2 minutes)
1. **📦 Plan** → check the date (defaults to tomorrow after 12:00) and the departure time.
2. Add orders: pick a customer in the empty row (usual kg & window fill in), click **Add all regulars**, or import the Excel template.
3. **⚡ Optimise routes** → read the KPIs, open each vehicle, press **WhatsApp** to send the driver his stop list, or **Google Maps navigation**.
4. Morning changes? Edit the quantities, optimise again. The orders are shared – a colleague who presses **Reload data** (sidebar) sees them.

#### Tips
* **Bookmark / Add to Home Screen** after signing in – the link keeps you signed in on that phone.
* Hotels get early windows (e.g. 05:00–07:00), shops later – the solver respects them and reports anything that can't fit (**dropped**) with a reason.
* **Strategy**: *Lowest cost* = fewest km/minutes; *Fewest vehicles* = pack trips so a van can stay home; *Balanced* = similar workload for all drivers.
* Travel times come from Google Maps historic traffic if a key is set, otherwise free OSRM roads × a Chennai hour-of-day profile (editable in ⚙️ Settings). Results are cached for 45 days, so repeat runs are fast.
* The first visit after ~12 h of inactivity takes 30–60 s while the free server wakes up – that's normal.

Storage: **{storage.backend_description()}**. Engine: Google OR-Tools (multi-trip capacitated VRP with time windows).
""")
