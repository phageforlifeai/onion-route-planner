"""
traffic_history.py – lightweight, privacy-preserving historical traffic model.

Stores aggregated Google traffic observations by:
    origin/destination coordinates + weekday + hour

The model is intentionally simple and robust: it learns a traffic multiplier
(Google traffic ETA / OSRM free-flow ETA) and only replaces a Google request
when enough observations exist for the same route/time bucket.
"""
import datetime as dt
import math
import threading
import time

import storage

_LOCK = threading.Lock()
_CACHE = None
_MAX_AGE_DAYS = 180


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = storage.load("traffic_history", {}) or {}
        except Exception:
            _CACHE = {}
    return _CACHE


def _key(a, b, when):
    # Coordinates are already stable in the customer master; rounding prevents
    # tiny floating-point differences from creating duplicate route histories.
    return (
        f"{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}|"
        f"{when.weekday()}|{when.hour:02d}"
    )


def _save():
    try:
        storage.save("traffic_history", _CACHE or {})
    except Exception:
        pass


def record(a, b, when, freeflow_s, traffic_s, distance_m=None):
    """Record one Google observation as an online mean/variance aggregate."""
    if not freeflow_s or freeflow_s <= 0 or not traffic_s or traffic_s <= 0:
        return
    ratio = max(0.5, min(float(traffic_s) / float(freeflow_s), 4.0))
    k = _key(a, b, when)
    now = int(time.time())
    with _LOCK:
        data = _load()
        e = data.get(k)
        if not e:
            e = {"n": 0, "mean": 0.0, "m2": 0.0, "last_ts": now,
                 "distance_m": int(distance_m or 0)}
        n = int(e.get("n", 0)) + 1
        old_mean = float(e.get("mean", 0.0))
        delta = ratio - old_mean
        mean = old_mean + delta / n
        m2 = float(e.get("m2", 0.0)) + delta * (ratio - mean)
        e.update({"n": n, "mean": round(mean, 6), "m2": round(m2, 6),
                  "last_ts": now, "distance_m": int(distance_m or e.get("distance_m", 0))})
        data[k] = e
        # Keep the aggregate store bounded. Old buckets are never useful for
        # route prediction after this horizon.
        cutoff = now - _MAX_AGE_DAYS * 86400
        if len(data) > 50000:
            data = {kk: vv for kk, vv in data.items() if int(vv.get("last_ts", 0)) >= cutoff}
            _CACHE = data
        _save()


def predict(a, b, when, min_observations=3, max_age_days=180):
    """Return {factor, n, confidence} or None."""
    with _LOCK:
        data = _load()
        e = data.get(_key(a, b, when))
        if not e:
            return None
        last_ts = int(e.get("last_ts", 0))
        if last_ts < time.time() - max_age_days * 86400:
            return None
        n = int(e.get("n", 0))
        if n < int(min_observations):
            return None
        mean = float(e.get("mean", 1.0))
        if not math.isfinite(mean):
            return None
        variance = float(e.get("m2", 0.0)) / max(n - 1, 1)
        sd = math.sqrt(max(variance, 0.0))
        # Confidence is only informational; the prediction itself remains the
        # empirical mean and is clipped to a sensible road-traffic range.
        confidence = min(1.0, n / 10.0) * (1.0 / (1.0 + sd))
        return {"factor": max(0.5, min(mean, 4.0)), "n": n,
                "confidence": round(confidence, 3), "sd": round(sd, 4)}


def stats():
    with _LOCK:
        data = _load()
        observations = sum(int(v.get("n", 0)) for v in data.values())
        buckets = len(data)
        return {"buckets": buckets, "observations": observations}


def clear():
    global _CACHE
    with _LOCK:
        _CACHE = {}
        _save()
