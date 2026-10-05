"""
storage.py – persistence layer.

Default: JSON files in ./data (or $DATA_DIR – point it at a persistent disk on
cloud hosts, e.g. /data).  Optional: set $DATABASE_URL (Postgres, e.g. a free
Neon/Supabase database) and everything is stored in one key-value table
instead – useful for hosts without a persistent filesystem (Render free tier,
Cloud Run …).

Values stored: customers, settings, orders, matrix_cache, secret.
"""
import json
import os
import secrets
import shutil
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DATA = os.path.join(BASE_DIR, "data")          # ships with sample customers
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or BUNDLED_DATA
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
os.makedirs(DATA_DIR, exist_ok=True)

_file_lock = threading.Lock()
_db_lock = threading.Lock()
_pg = None


# ---------------------------------------------------------------------------
# Postgres key-value backend (only used when DATABASE_URL is set)
# ---------------------------------------------------------------------------
def _pg_exec(sql, params=(), fetch=False):
    global _pg
    import psycopg2  # imported lazily so it is only needed with DATABASE_URL

    with _db_lock:
        for attempt in (1, 2):   # retry once: serverless Postgres drops idle connections
            try:
                if _pg is None or _pg.closed:
                    _pg = psycopg2.connect(DATABASE_URL, connect_timeout=15)
                    _pg.autocommit = True
                    with _pg.cursor() as cur:
                        cur.execute("CREATE TABLE IF NOT EXISTS kv (name TEXT PRIMARY KEY, value JSONB NOT NULL, "
                                    "updated TIMESTAMPTZ NOT NULL DEFAULT now())")
                with _pg.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall() if fetch else None
            except Exception:
                try:
                    _pg.close()
                except Exception:
                    pass
                _pg = None
                if attempt == 2:
                    raise


def _bundled(name):
    p = os.path.join(BUNDLED_DATA, f"{name}.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def _path(name: str) -> str:
    return os.path.join(DATA_DIR, f"{name}.json")


def load(name: str, default):
    if DATABASE_URL:
        rows = _pg_exec("SELECT value FROM kv WHERE name = %s", (name,), fetch=True)
        if rows:
            return rows[0][0]
        seed = _bundled(name) if name == "customers" else None   # first run: sample customers
        if seed is not None:
            save(name, seed)
            return seed
        return default

    p = _path(name)
    if not os.path.exists(p):
        if name == "customers" and DATA_DIR != BUNDLED_DATA:     # first run on a fresh disk
            src = os.path.join(BUNDLED_DATA, "customers.json")
            if os.path.exists(src):
                shutil.copy(src, p)
                return load(name, default)
        return default
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name: str, obj) -> None:
    if DATABASE_URL:
        _pg_exec("INSERT INTO kv (name, value, updated) VALUES (%s, %s::jsonb, now()) "
                 "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated = now()",
                 (name, json.dumps(obj, ensure_ascii=False)))
        return
    with _file_lock:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path(name))


def secret_key() -> str:
    """Stable Flask session key (env SECRET_KEY wins; else generated once and stored)."""
    env = os.environ.get("SECRET_KEY", "").strip()
    if env:
        return env
    s = load("secret", None)
    if not s:
        s = {"key": secrets.token_hex(32)}
        save("secret", s)
    return s["key"]


def db_info() -> dict:
    """Password-free description of DATABASE_URL for diagnostics screens."""
    from urllib.parse import urlparse, parse_qs
    info = {"configured": bool(DATABASE_URL)}
    if not DATABASE_URL:
        return info
    try:
        u = urlparse(DATABASE_URL)
        q = parse_qs(u.query)
        info.update({"scheme": u.scheme, "host": u.hostname or "", "port": u.port or 5432, "user": u.username or "",
                     "database": (u.path or "/").lstrip("/"), "sslmode": (q.get("sslmode") or [""])[0],
                     "password_len": len(u.password or ""), "password_masked": bool(u.password) and any(ch in u.password for ch in "*•")})
    except Exception as e:
        info["parse_error"] = str(e)
    info["looks_odd"] = [m for m, bad in (
        ("starts with 'psql' – paste only the postgresql://… part", DATABASE_URL.lower().startswith("psql")),
        ("contains spaces or quotes – the string got wrapped or quoted twice", any(c in DATABASE_URL for c in " '\"\n\t")),
        ("does not start with postgresql:// or postgres://", not DATABASE_URL.lower().startswith(("postgresql://", "postgres://"))),
        ("password looks masked (•••/***) – use Neon's copy button, not the displayed text", info.get("password_masked", False)),
        ("missing ?sslmode=require", "sslmode" not in DATABASE_URL.lower()),
    ) if bad]
    return info


def db_check():
    """None if the database answers, else a sanitised error string (password removed)."""
    if not DATABASE_URL:
        return None
    try:
        _pg_exec("SELECT 1", fetch=True)
        return None
    except Exception as e:
        msg = " ".join(str(e).split())
        from urllib.parse import urlparse
        try:
            pw = urlparse(DATABASE_URL).password
            if pw:
                msg = msg.replace(pw, "***")
        except Exception:
            pass
        return f"{type(e).__name__}: {msg}"


def backend_description() -> str:
    if DATABASE_URL:
        return "Postgres"
    return f"files in {DATA_DIR}"


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
    for k, v in DEFAULT_SETTINGS.items():     # back-fill keys added in later versions
        s.setdefault(k, v)
    # the Google key can also come from the environment (recommended on cloud hosts)
    if not s.get("google_api_key") and os.environ.get("GOOGLE_MAPS_API_KEY"):
        s["google_api_key"] = os.environ["GOOGLE_MAPS_API_KEY"].strip()
    return s


def get_customers() -> list:
    return load("customers", [])
