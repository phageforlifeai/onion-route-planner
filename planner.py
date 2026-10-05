"""
planner.py – UI-independent planning logic shared by the Flask app and the Streamlit app.

    result = plan(orders, settings, customers)   # raises PlanError on user-fixable problems
    orders, problems, created = import_orders(raw_rows, customers, settings)
"""
import datetime as dt
import math
import time
import uuid

from geocode import geocode
from matrix import build_matrix, route_geometry
from solver import baseline_nearest_neighbour, solve_vrp

COLORS = ["#d62828", "#1b7f3b", "#1d4ed8", "#f77f00", "#7b2cbf", "#0096c7", "#c2185b", "#6d4c41"]


class PlanError(Exception):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def hhmm_to_s(t):
    """'06:30' → 23400 seconds since midnight; None if blank/invalid."""
    if not t:
        return None
    t = str(t).strip()
    try:
        h, m = t.split(":")[:2]
        return int(h) * 3600 + int(m) * 60
    except Exception:
        return None


def fmt_t(depart_dt, secs):
    return (depart_dt + dt.timedelta(seconds=int(secs))).strftime("%H:%M")


def km(m):
    return round(m / 1000.0, 1)


def mins(s):
    return int(round(s / 60.0))


def gmaps_urls(points):
    """Google Maps navigation links (max 9 waypoints each) for depot→stops→depot."""
    urls, i = [], 0
    while i < len(points) - 1:
        chunk = points[i:i + 11]
        origin, dest, wps = chunk[0], chunk[-1], chunk[1:-1]
        u = (f"https://www.google.com/maps/dir/?api=1&travelmode=driving"
             f"&origin={origin[0]:.6f},{origin[1]:.6f}&destination={dest[0]:.6f},{dest[1]:.6f}")
        if wps:
            u += "&waypoints=" + "|".join(f"{a:.6f},{b:.6f}" for a, b in wps)
        urls.append(u)
        i += len(chunk) - 1
    return urls


def driver_text(route, summary, depot_name):
    lines = [f"🚚 {route['vehicle']['name']} – {summary['date']}", f"Leave {depot_name}: {route['suggested_departure']}"]
    multi = len(route["trips"]) > 1
    for tr in route["trips"]:
        if multi:
            lines.append(f"— Trip {tr['trip']}: leave depot {tr['depart']} ({tr['load_kg']:g} kg) —")
        for st in tr["stops"]:
            w = f" (window {st['window']})" if st.get("window") else ""
            ph = f" 📞 {st['phone']}" if st.get("phone") else ""
            lines.append(f"{st['seq']}. {st['customer']} – {st['qty_kg']:g} kg – ETA {st['arrival']}{w}{ph}")
        lines.append(f"Back at depot ≈ {tr['return']['arrival']} | {tr['total_km']} km")
        for k, u in enumerate(tr["gmaps_urls"], 1):
            lines.append(f"Navigation{' part ' + str(k) if len(tr['gmaps_urls']) > 1 else ''}: {u}")
    lines.append(f"Total: {route['total_km']} km | {len(route['stops'])} stops | {route['load_kg']:g} kg")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the optimiser
# ---------------------------------------------------------------------------
def plan(orders, settings, customers_list):
    """
    orders:    [{id, customer_id, qty_kg, tw_from, tw_to, service_min, notes}]
    settings:  full settings dict (may contain per-run overrides: date, departure_time, strategy, objective)
    customers: list of customer dicts
    returns:   result dict {summary, routes, dropped, warnings, depot}
    """
    customers = {c["id"]: c for c in customers_list}
    vehicles = settings.get("vehicles") or []
    if not vehicles:
        raise PlanError("No vehicles configured – add at least one in Settings.")
    if not orders:
        raise PlanError("No orders to plan.")

    date_s = settings.get("date") or dt.date.today().isoformat()
    dep_s = hhmm_to_s(settings.get("departure_time")) or 5 * 3600
    depart_dt = dt.datetime.combine(dt.date.fromisoformat(str(date_s)), dt.time(0, 0)) + dt.timedelta(seconds=dep_s)
    horizon_s = int(float(settings.get("max_route_hours", 6)) * 3600)
    default_service = float(settings.get("default_service_min", 10)) * 60
    trips_per_vehicle = max(1, int(settings.get("trips_per_vehicle", 2)))
    reload_s = int(float(settings.get("reload_min", 20)) * 60)
    max_cap = max(int(v["capacity_kg"]) for v in vehicles)
    fleet_cap = sum(int(v["capacity_kg"]) for v in vehicles)

    depot = settings["depot"]
    locations = [(float(depot["lat"]), float(depot["lng"]))]
    nodes = [{"kind": "depot", "mi": 0, "demand": 0, "service_s": 0, "tw": None, "feasible": True,
              "order": None, "customer": depot, "label": depot.get("name", "Depot")}]
    warnings, pre_dropped = [], []

    # ---- order nodes (orders bigger than the largest truck are auto-split) ----
    for o in orders:
        c = customers.get(o.get("customer_id"))
        if not c:
            pre_dropped.append({"order_id": o.get("id"), "customer": o.get("customer_name", "?"), "qty_kg": o.get("qty_kg"),
                                "reason": "Customer not found in master list"})
            continue
        if c.get("lat") in (None, "") or c.get("lng") in (None, ""):
            pre_dropped.append({"order_id": o.get("id"), "customer_id": c["id"], "customer": c["name"], "qty_kg": o.get("qty_kg"),
                                "reason": "Customer has no coordinates – geocode it in the Customers tab"})
            continue
        qty = float(o.get("qty_kg") or 0)
        if qty <= 0:
            continue
        svc = float(o.get("service_min") or 0) * 60 or default_service
        f, t = hhmm_to_s(o.get("tw_from")), hhmm_to_s(o.get("tw_to"))
        tw, feasible, reason = None, True, ""
        if f is not None or t is not None:
            lo = (f if f is not None else 0) - dep_s
            hi = (t if t is not None else 24 * 3600) - dep_s
            if hi < 0:
                feasible, reason = False, f"Window closes ({o.get('tw_to')}) before departure {settings.get('departure_time')}"
            elif lo > horizon_s:
                feasible, reason = False, f"Window opens ({o.get('tw_from')}) after the max route duration ({settings.get('max_route_hours')} h)"
            tw = (max(0, lo), min(horizon_s, hi))
        parts = max(1, math.ceil(qty / max_cap))
        if parts > 1:
            warnings.append(f"{c['name']}: {qty:g} kg exceeds the largest vehicle ({max_cap} kg) – split into {parts} drops")
        per = qty / parts
        locations.append((float(c["lat"]), float(c["lng"])))
        mi = len(locations) - 1
        for p in range(parts):
            nodes.append({"kind": "order", "mi": mi, "demand": int(math.ceil(per)), "service_s": int(svc), "tw": tw,
                          "feasible": feasible, "reason": reason, "order": o, "customer": c,
                          "label": c["name"] + (f" (part {p + 1}/{parts})" if parts > 1 else "")})

    if len(nodes) <= 1:
        raise PlanError("None of the orders could be planned.", dropped=pre_dropped)

    total_demand = sum(n["demand"] for n in nodes)
    if total_demand > fleet_cap:
        if trips_per_vehicle > 1:
            warnings.append(f"Total demand {total_demand} kg exceeds fleet capacity {fleet_cap} kg – "
                            f"the planner will add reload trips at the depot where needed")
        else:
            warnings.append(f"Total demand {total_demand} kg exceeds fleet capacity {fleet_cap} kg – "
                            f"enable 'trips per vehicle' > 1 in Settings or some orders will be dropped")

    for _ in range((trips_per_vehicle - 1) * len(vehicles)):   # reload nodes at the depot
        nodes.append({"kind": "reload", "mi": 0, "demand": 0, "service_s": reload_s, "tw": None, "feasible": True,
                      "order": None, "customer": depot, "label": "Reload at depot"})

    # ---- travel matrix ----
    try:
        mx = build_matrix(locations, settings, depart_dt)
    except Exception as e:  # noqa: BLE001
        raise PlanError(f"Could not compute travel times: {e}", status=502)
    if mx.get("fallback_errors"):
        warnings.append("Travel-time provider fallback: " + " | ".join(mx["fallback_errors"]))

    for n in nodes:   # window-reachability pre-check
        if n["kind"] == "order" and n["feasible"] and n["tw"] and n["tw"][1] < mx["time_s"][0][n["mi"]]:
            n["feasible"] = False
            n["reason"] = (f"Cannot reach before window closes ({n['order'].get('tw_to')}): "
                           f"{mins(mx['time_s'][0][n['mi']])} min drive from depot")

    # ---- solve ----
    opts = {"objective": settings.get("objective", "time"), "strategy": settings.get("strategy", "lowest_cost"),
            "horizon_s": horizon_s, "time_limit_s": int(settings.get("solver_seconds", 8))}
    t0 = time.time()
    sol = solve_vrp(nodes, mx["dist_m"], mx["time_s"], vehicles, opts)
    solve_s = round(time.time() - t0, 1)
    if sol["status"] != "ok":
        raise PlanError("Solver could not find a plan. Try a longer max route duration or wider windows.", status=500)
    base = baseline_nearest_neighbour(nodes, mx["dist_m"], mx["time_s"], vehicles)

    # ---- format ----
    routes_out, used = [], 0
    tot_m = tot_drive = tot_load = served = 0
    for r in sol["routes"]:
        v = vehicles[r["vehicle"]]
        color = COLORS[r["vehicle"] % len(COLORS)]
        if not r["used"]:
            routes_out.append({"vehicle": v, "color": color, "used": False, "trips": [], "stops": [], "load_kg": 0,
                               "capacity_kg": int(v["capacity_kg"]), "utilisation_pct": 0, "total_km": 0})
            continue
        used += 1
        trips_out, all_stops, seq_no = [], [], 0
        for ti, tr in enumerate(r["trips"], start=1):
            stops, delivered = [], 0
            for st in tr["stops"]:
                seq_no += 1
                n = nodes[st["node"]]
                c, o = n["customer"], n["order"]
                delivered += n["demand"]
                w = f"{o.get('tw_from') or '…'}–{o.get('tw_to') or '…'}" if (o.get("tw_from") or o.get("tw_to")) else ""
                stops.append({
                    "seq": seq_no, "trip": ti, "order_id": o.get("id"), "customer_id": c["id"], "customer": n["label"],
                    "area": c.get("area", ""), "address": c.get("address", ""), "phone": c.get("phone", ""),
                    "lat": c["lat"], "lng": c["lng"], "qty_kg": n["demand"], "window": w,
                    "arrival": fmt_t(depart_dt, st["arrival_s"]), "start": fmt_t(depart_dt, st["start_s"]),
                    "depart": fmt_t(depart_dt, st["depart_s"]), "wait_min": mins(st["wait_s"]),
                    "leg_km": km(st["leg_m"]), "leg_min": mins(st["leg_s"]), "cum_km": km(st["cum_m"]),
                    "remaining_kg": tr["load"] - delivered, "notes": o.get("notes", ""),
                })
            pts = [locations[0]] + [locations[nodes[s["node"]]["mi"]] for s in tr["stops"]] + [locations[0]]
            trips_out.append({
                "trip": ti, "stops": stops, "depart": fmt_t(depart_dt, tr["latest_depart_s"]),
                "return": {"arrival": fmt_t(depart_dt, tr["return"]["arrival_s"]),
                           "leg_km": km(tr["return"]["leg_m"]), "leg_min": mins(tr["return"]["leg_s"])},
                "total_km": km(tr["total_m"]), "drive_min": mins(tr["drive_s"]), "service_min": mins(tr["service_s"]),
                "wait_min": mins(tr["wait_s"]), "load_kg": tr["load"], "capacity_kg": int(v["capacity_kg"]),
                "utilisation_pct": round(100.0 * tr["load"] / int(v["capacity_kg"])),
                "reload_wait_min": mins(tr.get("reload_wait_s", 0)),
                "geometry": route_geometry(pts, settings),
                "gmaps_urls": gmaps_urls(pts),
            })
            all_stops.extend(stops)
        route = {
            "vehicle": v, "color": color, "used": True, "trips": trips_out, "stops": all_stops,
            "suggested_departure": trips_out[0]["depart"], "return": trips_out[-1]["return"],
            "total_km": km(r["total_m"]), "drive_min": mins(r["drive_s"]), "service_min": mins(r["service_s"]),
            "wait_min": mins(r["wait_s"]), "route_min": mins(r["end_s"] - r["start_s"]),
            "load_kg": r["load"], "capacity_kg": int(v["capacity_kg"]),
            "utilisation_pct": round(100.0 * r["max_trip_load"] / int(v["capacity_kg"])),
            "gmaps_urls": [u for t in trips_out for u in t["gmaps_urls"]],
        }
        routes_out.append(route)
        tot_m += r["total_m"]
        tot_drive += r["drive_s"]
        tot_load += r["load"]
        served += len(all_stops)

    dropped = list(pre_dropped)
    for i in sol["dropped"]:
        n = nodes[i]
        o = n["order"]
        if not n["feasible"]:
            reason = n["reason"]
        elif n["demand"] > max_cap:
            reason = "Exceeds vehicle capacity"
        elif total_demand > fleet_cap * trips_per_vehicle:
            reason = "Fleet capacity (incl. extra trips) is full – plan separately"
        else:
            reason = "Could not fit within time windows / max route duration"
        w = f"{o.get('tw_from') or '…'}–{o.get('tw_to') or '…'}" if (o.get("tw_from") or o.get("tw_to")) else ""
        dropped.append({"order_id": o.get("id"), "customer_id": n["customer"]["id"], "customer": n["label"],
                        "qty_kg": n["demand"], "window": w, "reason": reason})

    trip_count = sum(len(rt["trips"]) for rt in routes_out if rt["used"])
    trip_cap = sum(t["capacity_kg"] for rt in routes_out if rt["used"] for t in rt["trips"])
    saving_km = km(base["dist_m"]) - km(tot_m)
    summary = {
        "date": str(date_s), "depart": settings.get("departure_time"),
        "vehicles_used": used, "vehicles_total": len(vehicles), "trips": trip_count,
        "stops_served": served, "stops_dropped": len(dropped),
        "total_km": km(tot_m), "total_drive_min": mins(tot_drive),
        "load_kg": tot_load, "fleet_capacity_kg": fleet_cap,
        "utilisation_pct": round(100.0 * tot_load / trip_cap) if trip_cap else 0,
        "baseline_km": km(base["dist_m"]), "baseline_drive_min": mins(base["time_s"]),
        "saving_km": round(saving_km, 1),
        "saving_pct": round(100.0 * saving_km / km(base["dist_m"])) if base["dist_m"] else 0,
        "matrix_source": mx["source"], "traffic_factor": mx.get("traffic_factor"),
        "matrix_note": mx.get("note", ""), "solve_seconds": solve_s,
        "strategy": opts["strategy"], "objective": opts["objective"],
    }
    for rt in routes_out:
        if rt["used"]:
            rt["text"] = driver_text(rt, summary, depot.get("name", "depot"))
    return {"summary": summary, "routes": routes_out, "dropped": dropped, "warnings": warnings, "depot": depot}


# ---------------------------------------------------------------------------
# Excel import → orders (creates & geocodes brand-new customers that have an address)
# ---------------------------------------------------------------------------
def import_orders(raw, customers, settings):
    by_name = {c["name"].strip().lower(): c for c in customers}
    orders, problems, created = [], [], 0
    for r in raw:
        c = by_name.get(r["customer_name"].strip().lower())
        if not c and r.get("address"):
            g = geocode(r["address"], settings)
            if g:
                c = {"id": uuid.uuid4().hex[:8], "name": r["customer_name"].strip(), "area": "",
                     "address": r["address"], "lat": g["lat"], "lng": g["lng"], "phone": "",
                     "default_qty_kg": r["qty_kg"], "default_tw_from": r["tw_from"],
                     "default_tw_to": r["tw_to"], "notes": "auto-added from Excel import"}
                customers.append(c)
                by_name[c["name"].lower()] = c
                created += 1
                time.sleep(1.0)
        if not c:
            problems.append(f"'{r['customer_name']}' is not in your customer list (add it, or fill the Address column)")
            continue
        orders.append({"id": uuid.uuid4().hex[:8], "customer_id": c["id"], "qty_kg": r["qty_kg"],
                       "tw_from": r["tw_from"], "tw_to": r["tw_to"],
                       "service_min": r["service_min"], "notes": r["notes"]})
    return orders, problems, created
