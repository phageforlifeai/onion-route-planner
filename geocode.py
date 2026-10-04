"""
geocode.py – turn an address (or a pasted Google-Maps link / "lat, lng") into coordinates.

Order: coordinates in the text → Google Geocoding (if key) → Nominatim (free).
"""
import re
import time

import requests

from matrix import UA

_LL = re.compile(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def parse_latlng(text: str):
    """Accepts '13.0418, 80.2341', Google Maps URLs with @lat,lng, !3d..!4d.., or ?q=lat,lng."""
    if not text:
        return None
    m = (re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", text)
         or re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", text)
         or re.search(r"[?&](?:q|ll|query|destination)=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)", text)
         or _LL.search(text))
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng
    return None


def _google(q, key):
    r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                     params={"address": q, "region": "in", "key": key}, timeout=15).json()
    if r.get("status") == "OK":
        g = r["results"][0]
        loc = g["geometry"]["location"]
        return {"lat": loc["lat"], "lng": loc["lng"], "display": g["formatted_address"], "source": "google"}
    return None


def _nominatim(q):
    r = requests.get("https://nominatim.openstreetmap.org/search",
                     params={"q": q, "format": "json", "limit": 1, "countrycodes": "in"},
                     headers=UA, timeout=15).json()
    if r:
        return {"lat": float(r[0]["lat"]), "lng": float(r[0]["lon"]),
                "display": r[0]["display_name"], "source": "nominatim"}
    return None


def geocode(address: str, settings: dict):
    ll = parse_latlng(address or "")
    if ll:
        return {"lat": ll[0], "lng": ll[1], "display": address, "source": "coordinates"}
    q = (address or "").strip()
    if not q:
        return None
    low = q.lower()
    if "chennai" not in low and "tamil" not in low:
        q += ", Chennai, Tamil Nadu, India"

    key = (settings.get("google_api_key") or "").strip()
    if key and settings.get("traffic_source") in ("auto", "google"):
        try:
            res = _google(q, key)
            if res:
                return res
        except Exception:
            pass

    # Nominatim: Indian door-number addresses often fail, so progressively drop
    # the most specific component ("No 12, Bazaar Rd, Mylapore" → "Bazaar Rd, Mylapore")
    parts = [p.strip() for p in q.split(",") if p.strip()]
    for attempt in range(min(3, max(1, len(parts) - 2))):
        try:
            res = _nominatim(", ".join(parts[attempt:]))
            if res:
                if attempt:
                    res["display"] += "  (approx. – matched on area)"
                return res
        except Exception:
            pass
        time.sleep(1.0)  # Nominatim fair-use policy: max 1 request / second
    return None
