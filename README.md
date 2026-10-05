# 🧅 Onion Route Planner – Chennai

A daily delivery-route optimiser for a 2–3 vehicle onion supply business.
Enter each customer's requirement for the day → get the optimal assignment of
orders to vehicles, the stop sequence, ETAs, load per vehicle, a map, Google
Maps navigation links and WhatsApp-ready text for every driver.

## What it optimises

| Considers | How |
|---|---|
| Distance / driving time | Real road network (OpenStreetMap) or Google Maps |
| Traffic | Google **historic traffic** for the planned day & hour (with API key) **or** an editable hour-by-hour Chennai congestion profile (free) |
| Vehicle capacity | kg per vehicle; orders bigger than a truck are split automatically |
| Customer time windows | e.g. hotels 05:00–07:30, supermarkets 09:00–12:00 (blank = any time) |
| Unloading time | default per stop, override per order |
| Max route duration | e.g. 6 h shift |
| Second trips | If demand > fleet capacity or a customer opens late, a vehicle reloads at the depot and goes again |
| Departure time | Picks the latest departure per vehicle that avoids waiting at customers |
| Strategy | *Lowest total km/time* · *Fewest vehicles* · *Balanced workload* |

The solver is **Google OR-Tools** (constraint programming + guided local
search) – the same engine used by large logistics companies. For < 50 stops it
finds near-optimal plans in a few seconds.

## Quick start

```bash
# 1. Python 3.10+ required
pip install -r requirements.txt

# 2. Run
python app.py          # or double-click run.bat (Windows) / run.sh (Mac/Linux)

# 3. Open  http://localhost:5000
```

16 sample Chennai customers are included so you can try it immediately
(**Today's plan → Add all regulars → ⚡ Optimise routes**). Replace them with
your real customers in the **Customers** tab.

## Share it with your team (PC + mobile)

Run it in one place and everyone opens the URL in a browser – phones get a
dedicated **Plan · Map · Routes** layout and can *Add to Home Screen*.
Set `APP_PASSWORD` to require a team login. See **[DEPLOY.md](DEPLOY.md)** for
the options: **Streamlit Community Cloud + free Neon Postgres (₹0, nothing to keep
switched on – uses `streamlit_app.py`)**, office PC on Wi-Fi (free), Tailscale
remote access (free), Railway / Render cloud hosting (~$5–7/month, always-on),
PythonAnywhere, or Docker on a VPS.

Two front ends, one engine:

| | `app.py` (Flask) | `streamlit_app.py` (Streamlit) |
|---|---|---|
| Best for | office PC / Docker / Railway / Render | **Streamlit Community Cloud (free)** |
| Map | Leaflet, live in the page | folium |
| Data | `data/*.json` or Postgres | Postgres (`DATABASE_URL`) or `data/*.json` |
| Login | shared password, cookie | shared password, bookmarkable `?k=` link |

Both read/write the same customers, orders and settings if they point at the same
database, so you can run Flask on the office PC *and* the Streamlit copy in the cloud.

```bash
APP_PASSWORD=YourPassword python app.py     # Windows: set APP_PASSWORD=YourPassword && python app.py
```

## Daily routine (≈ 2 minutes)

1. **Today's plan** → *Add all regulars* (or *Import Excel* using the template), fix quantities, delete customers with no order today.
2. Check date / departure time / strategy → **⚡ Optimise routes**.
3. Per vehicle: **📋 WhatsApp** (copies the route text for the driver), **▶ Google Maps** (turn-by-turn navigation with all stops), or **⬇ Excel route sheets** (printable, one tab per vehicle with a "Delivered ✓" column).

Orders are auto-saved, so a browser refresh does not lose them.

## Adding customers accurately

* Best: on your phone open Google Maps → long-press the shop → *Share* → paste the link into the address box → **🔍 Find**.
* Or type "Landmark, Area" (e.g. *Kapaleeshwarar Temple, Mylapore*) → **🔍 Find**, then drag-check on the map.
* Or **📍 Pick on map**.
* Store a default quantity and time window per customer so daily entry is one click.

## Traffic data

**Free mode (default)** – road distances from OpenStreetMap (OSRM) multiplied
by a Chennai congestion profile (Settings → *hourly congestion profile*).
Default values: 1.0× at night, ~1.8× at the 9 AM peak, ~1.95× at 6–7 PM;
Sundays are damped. The factor applied is the average over the first 3 hours of
the shift and is shown in the result note. Tune it from your drivers' experience.

**Google historic traffic** – Settings → paste a *Google Maps Platform* API key
(enable **Distance Matrix API** and **Geocoding API** in Google Cloud Console).
The planner then asks Google for travel times at the planned departure (+45 min)
on that weekday, which uses Google's historical traffic model. Every
origin→destination pair is **cached for 45 days** (`data/matrix_cache.json`),
so with a stable customer base you make almost no API calls after the first
day. Google's free allowance (since March 2025) is per API: 10,000 requests per
month for Essentials APIs and 5,000 for Pro APIs such as traffic-aware
Distance Matrix – more than enough for 20 customers/day with caching.

## Settings worth knowing

| Setting | Default | Notes |
|---|---|---|
| Vehicles | 3 × Tata Ace 750 kg | Any mix of sizes |
| Departure | 05:00 | Per-vehicle "leave at" is still optimised |
| Max route | 6 h | Hard limit incl. second trips |
| Trips per vehicle | 2 | Set 1 to forbid reload trips |
| Reload time | 20 min | Time at depot between trips |
| Solver time | 8 s | Increase to 20–30 s for 40+ stops |
| Strategy | Lowest total km/time | *Fewest vehicles* keeps a truck free; *Balanced* equalises drivers' work |

## Project layout

```
app.py          Flask server + REST API (/api/optimize, /api/customers, …), login
streamlit_app.py  Streamlit front end (Streamlit Community Cloud) – same engine, Postgres via DATABASE_URL
planner.py      Shared planning logic: orders → matrix → solver → routes/driver text (used by both UIs)
DEPLOY.md       How to run it for the team: Streamlit Cloud + Neon (free), office PC, Tailscale, Railway/Render, Docker
.streamlit/     config.toml (theme) and secrets.toml.example
Dockerfile, Procfile, render.yaml, docker-compose.yml   deployment recipes
solver.py       OR-Tools multi-trip CVRPTW model
matrix.py       Travel-time matrix: Google ⇄ OSRM ⇄ offline fallback, caching, Chennai profile
geocode.py      Address / Google-Maps-link → coordinates
export_xlsx.py  Excel route sheets, order template, Excel import
storage.py      JSON-file persistence (DATA_DIR) or Postgres (DATABASE_URL) + defaults
static/         Web UI (index.html, app.js, style.css – Leaflet map)
data/           customers.json, settings.json, orders.json, matrix_cache.json  ← back this folder up
static/         also: manifest + icons so phones can install it as an app
```

## How the model works (for the curious)

* Node 0 = depot (Koyambedu), one node per order, plus optional *reload* nodes at the depot.
* **Load dimension**: kg delivered since the last (re)load ≤ capacity. A reload node resets it.
* **Time dimension**: seconds since planned departure; waiting allowed; windows are hard constraints; ≤ max route duration.
* **Objective** = 100·travel + 50·(time on the road incl. waiting) + 1·(late departure) + fixed cost per vehicle used (+ longest-work penalty for *Balanced*). Unservable orders are dropped at a huge penalty and reported with the reason.
* A nearest-neighbour "manual-style" plan is computed for comparison so you can see the saving.

## Limitations / next steps

* Travel times are time-of-day aware via one factor per plan (or Google's estimate for the departure hour), not re-evaluated leg by leg. For a 3–4 h morning shift this is accurate enough; a fully time-dependent model is possible if needed.
* Nominatim (free geocoder) can be approximate for Indian door numbers – use Google Maps links or *Pick on map* for precision.
* Easy extensions: driver app / live tracking, per-customer priority, multi-depot, cost per km for ₹ reporting, WhatsApp API auto-send.
