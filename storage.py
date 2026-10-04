"""
storage.py – tiny JSON persistence layer (no database needed).

Everything lives in ./data/*.json so the whole tool can be copied/backed-up
by copying one folder.
"""
import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, f"{name}.json")


def load(name: str, default):
    p = _path(name)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name: str, obj) -> None:
    with _lock:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path(name))


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Typical weekday congestion multiplier (vs. night free-flow) for each hour in Chennai.
# 1.0 = free flow (2 AM), ~1.9 = evening peak (6–7 PM). Editable in Settings.
CHENNAI_PROFILE = [
    1.00, 1.00, 1.00, 1.00, 1.00, 1.05,   # 00–05
    1.15, 1.35, 1.60, 1.80, 1.70, 1.55,   # 06–11
    1.45, 1.40, 1.40, 1.50, 1.65, 1.85,   # 12–17
    1.95, 1.90, 1.65, 1.35, 1.15, 1.05,   # 18–23
]

DEFAULT_SETTINGS = {
    "depot": {
        "name": "Koyambedu Wholesale Market",
        "address": "Koyambedu Wholesale Market Complex, Chennai",
        "lat": 13.0694,
        "lng": 80.1948,
    },
    "vehicles": [
        {"id": "V1", "name": "Tata Ace 1", "capacity_kg": 750},
        {"id": "V2", "name": "Tata Ace 2", "capacity_kg": 750},
        {"id": "V3", "name": "Tata Ace 3", "capacity_kg": 750},
    ],
    "departure_time": "05:00",
    "max_route_hours": 6,
    "default_service_min": 10,
    "objective": "time",              # time | distance
    "strategy": "lowest_cost",        # lowest_cost | fewest_vehicles | balanced
    "solver_seconds": 8,
    "trips_per_vehicle": 2,           # allow a reload at the depot for a 2nd trip
    "reload_min": 20,                 # minutes to reload at the depot
    "traffic_source": "auto",         # auto | google | osrm | haversine
    "google_api_key": "",
    "traffic_model": "best_guess",    # Google: best_guess | pessimistic | optimistic
    "traffic_sample_offset_min": 45,  # Google traffic is sampled this long after departure (mid-route)
    "osrm_base_factor": 1.10,         # OSRM free-flow is slightly optimistic for Indian roads
    "base_speed_kmh": 30,             # used only by the straight-line fallback
    "traffic_profile": CHENNAI_PROFILE,
}


def get_settings() -> dict:
    s = load("settings", None)
    if not s:
        s = json.loads(json.dumps(DEFAULT_SETTINGS))
        save("settings", s)
    # back-fill any new keys added in later versions
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)
    return s


def get_customers() -> list:
    return load("customers", [])
