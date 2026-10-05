"""
matrix.py – travel distance / time matrices for the route planner.

Providers (tried in this order, automatic fallback):
  1. google    – Google Distance Matrix API with `departure_time`, which makes
                 Google apply its **historic traffic model** for that day/hour.
  2. osrm      – OpenStreetMap road routing (free, no key). Returns free-flow
                 times which we scale by a Chennai hour-of-day congestion
                 profile (editable in Settings).
  3. haversine – straight-line distance × circuity factor. Last resort, works
                 even without internet.

Every origin→destination pair is cached (matrix_cache in the data store), so a stable
customer base costs (almost) zero API calls on subsequent days.
"""
import datetime as dt
import math
import threading
import time

import requests

import storage
from storage import CHENNAI_PROFILE

OSRM_URL = "https://router.project-osrm.org"
GOOGLE_DM_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
UA = {"User-Agent": "OnionRoutePlanner/1.0 (local delivery planning tool)"}
CACHE_TTL_DAYS = 45
SUNDAY_DAMPING = 0.6  # Sunday congestion = 1 + (weekday_excess * 0.6)


# ---------------------------------------------------------------------------
# Traffic profile helpers
# ---------------------------------------------------------------------------
def hour_factor(profile, h: float) -> float:
    """Linear interpolation of the 24-value hourly profile at fractional hour h."""
    h = h % 24
    i = int(h)
    f = h - i
    return profile[i] * (1 - f) + profile[(i + 1) % 24] * f


def shift_factor(profile, depart_hour: float, hours: float, sunday: bool = False) -> float:
    """
    Average congestion over the busiest part of the shift (first <=3 h after
    departure, sampled every 10 minutes). One factor is applied to the whole
    matrix because the solver needs time-independent arc costs.
    """
    hours = max(0.5, min(float(hours), 3.0))
    steps = int(hours * 6)
    vals = [hour_factor(profile, depart_hour + k / 6.0) for k in range(steps + 1)]
    f = sum(vals) / len(vals)
    if sunday:
        f = 1 + (f - 1) * SUNDAY_DAMPING
    return round(f, 3)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------
class _Cache:
    def __init__(self):
        self.lock = threading.Lock()
        try:
            self.data = storage.load("matrix_cache", {}) or {}
        except Exception:
            self.data = {}

    @staticmethod
    def k(src, a, b, bucket):
        return f"{src}|{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}|{bucket}"

    def get(self, key):
        e = self.data.get(key)
        if e and time.time() - e.get("ts", 0) < CACHE_TTL_DAYS * 86400:
            return e
        return None

    def put(self, key, d, t):
        self.data[key] = {"d": int(d), "t": int(t), "ts": int(time.time())}

    def flush(self):
        with self.lock:
            try:
                storage.save("matrix_cache", self.data)
            except Exception:
                pass   # caching is best-effort

    def stats(self):
        return {"pairs_cached": len(self.data)}


CACHE = _Cache()


# ---------------------------------------------------------------------------
# Provider 1: Google Distance Matrix (historic traffic)
# ---------------------------------------------------------------------------
def google_matrix(points, api_key, depart_dt, settings):
    if not api_key:
        raise RuntimeError("no Google API key configured")
    n = len(points)
    now = dt.datetime.now()
    offset = int(settings.get("traffic_sample_offset_min", 45))
    sample = depart_dt + dt.timedelta(minutes=offset)
    # Google requires departure_time >= now. For a future timestamp Google uses
    # its historical traffic model for that weekday/hour – exactly what we want.
    while sample < now + dt.timedelta(minutes=5):
        sample += dt.timedelta(days=1)
    day_type = "su" if sample.weekday() == 6 else "wd"
    bucket = f"g-{day_type}-{sample.hour:02d}"
    ts = int(sample.timestamp())
    model = settings.get("traffic_model", "best_guess")

    dist = [[0] * n for _ in range(n)]
    tim = [[0] * n for _ in range(n)]
    calls = 0
    for i in range(n):
        missing = []
        for j in range(n):
            if i == j:
                continue
            e = CACHE.get(_Cache.k("google", points[i], points[j], bucket))
            if e:
                dist[i][j], tim[i][j] = e["d"], e["t"]
            else:
                missing.append(j)
        for s in range(0, len(missing), 25):  # API limit: 25 destinations / request
            chunk = missing[s:s + 25]
            params = {
                "origins": f"{points[i][0]},{points[i][1]}",
                "destinations": "|".join(f"{points[j][0]},{points[j][1]}" for j in chunk),
                "mode": "driving",
                "departure_time": ts,
                "traffic_model": model,
                "key": api_key,
            }
            r = requests.get(GOOGLE_DM_URL, params=params, timeout=25)
            calls += 1
            js = r.json()
            if js.get("status") != "OK":
                raise RuntimeError(f"Google Distance Matrix: {js.get('status')} {js.get('error_message', '')}".strip())
            for j, el in zip(chunk, js["rows"][0]["elements"]):
                if el.get("status") != "OK":
                    raise RuntimeError(f"Google: no route between point {i} and {j} ({el.get('status')})")
                d = el["distance"]["value"]
                t = el.get("duration_in_traffic", el["duration"])["value"]
                dist[i][j], tim[i][j] = int(d), int(t)
                CACHE.put(_Cache.k("google", points[i], points[j], bucket), d, t)
    if calls:
        CACHE.flush()
    return {
        "dist_m": dist,
        "time_s": tim,
        "source": "google",
        "traffic_factor": None,
        "note": (f"Google historic traffic for {sample.strftime('%a %d %b, %H:%M')} "
                 f"({model}); {calls} API call(s), remaining pairs from cache"),
    }


# ---------------------------------------------------------------------------
# Provider 2: OSRM (OpenStreetMap) + Chennai congestion profile
# ---------------------------------------------------------------------------
def osrm_matrix(points, settings, depart_dt):
    n = len(points)
    base = (settings.get("osrm_url") or OSRM_URL).rstrip("/")
    dist = [[0] * n for _ in range(n)]
    ff = [[0] * n for _ in range(n)]
    missing = False
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            e = CACHE.get(_Cache.k("osrm", points[i], points[j], "ff"))
            if e:
                dist[i][j], ff[i][j] = e["d"], e["t"]
            else:
                missing = True
    if missing:
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        r = requests.get(f"{base}/table/v1/driving/{coords}",
                         params={"annotations": "duration,distance"}, headers=UA, timeout=40)
        js = r.json()
        if js.get("code") != "Ok":
            raise RuntimeError(f"OSRM table: {js.get('code')} {js.get('message', '')}".strip())
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                d = js["distances"][i][j]
                t = js["durations"][i][j]
                if d is None or t is None:
                    raise RuntimeError(f"OSRM: no road route between point {i} and {j}")
                dist[i][j], ff[i][j] = int(round(d)), int(round(t))
                CACHE.put(_Cache.k("osrm", points[i], points[j], "ff"), dist[i][j], ff[i][j])
        CACHE.flush()

    profile = settings.get("traffic_profile") or CHENNAI_PROFILE
    sunday = depart_dt.weekday() == 6
    f = shift_factor(profile, depart_dt.hour + depart_dt.minute / 60.0,
                     settings.get("max_route_hours", 6), sunday=sunday)
    basef = float(settings.get("osrm_base_factor", 1.1))
    total = round(basef * f, 2)
    tim = [[int(round(ff[i][j] * total)) for j in range(n)] for i in range(n)]
    return {
        "dist_m": dist,
        "time_s": tim,
        "source": "osrm",
        "traffic_factor": total,
        "note": (f"OpenStreetMap road distances × Chennai traffic factor {total:.2f} "
                 f"(departure {depart_dt.strftime('%H:%M')}, {'Sunday' if sunday else 'weekday'} profile)"),
    }


# ---------------------------------------------------------------------------
# Provider 3: straight-line fallback
# ---------------------------------------------------------------------------
def haversine_m(a, b) -> float:
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def haversine_matrix(points, settings, depart_dt):
    n = len(points)
    circuity = 1.35  # Chennai road distance ≈ 1.35 × straight line
    speed = float(settings.get("base_speed_kmh", 30))
    profile = settings.get("traffic_profile") or CHENNAI_PROFILE
    f = shift_factor(profile, depart_dt.hour + depart_dt.minute / 60.0,
                     settings.get("max_route_hours", 6), sunday=(depart_dt.weekday() == 6))
    dist = [[0 if i == j else int(round(haversine_m(points[i], points[j]) * circuity))
             for j in range(n)] for i in range(n)]
    tim = [[int(round(dist[i][j] / 1000.0 / speed * 3600 * f)) for j in range(n)] for i in range(n)]
    return {
        "dist_m": dist,
        "time_s": tim,
        "source": "haversine",
        "traffic_factor": f,
        "note": f"APPROXIMATE straight-line estimate × 1.35 at {speed:.0f} km/h ÷ traffic factor {f:.2f} (no internet/road data)",
    }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def build_matrix(points, settings, depart_dt):
    """points: list of (lat, lng); index 0 must be the depot."""
    source = settings.get("traffic_source", "auto")
    key = (settings.get("google_api_key") or "").strip()
    if source == "auto":
        order = (["google"] if key else []) + ["osrm", "haversine"]
    elif source == "google":
        order = ["google", "osrm", "haversine"]
    elif source == "osrm":
        order = ["osrm", "haversine"]
    else:
        order = ["haversine"]

    errors = []
    for src in order:
        try:
            if src == "google":
                res = google_matrix(points, key, depart_dt, settings)
            elif src == "osrm":
                res = osrm_matrix(points, settings, depart_dt)
            else:
                res = haversine_matrix(points, settings, depart_dt)
            res["fallback_errors"] = errors
            return res
        except Exception as e:  # noqa: BLE001 – fall through to next provider
            errors.append(f"{src}: {e}")
    raise RuntimeError("All matrix providers failed: " + "; ".join(errors))


def route_geometry(points_seq, settings):
    """Road polyline (list of [lat, lng]) for drawing a route on the map. Best effort."""
    try:
        base = (settings.get("osrm_url") or OSRM_URL).rstrip("/")
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points_seq)
        r = requests.get(f"{base}/route/v1/driving/{coords}",
                         params={"overview": "full", "geometries": "geojson"}, headers=UA, timeout=25)
        js = r.json()
        if js.get("code") == "Ok":
            return [[lat, lng] for lng, lat in js["routes"][0]["geometry"]["coordinates"]]
    except Exception:
        pass
    return [[lat, lng] for lat, lng in points_seq]  # straight lines fallback
