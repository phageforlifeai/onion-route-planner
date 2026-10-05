# Running the planner for your team (PC + mobile)

The planner is a web app: it runs on **one** machine (your office PC or a cloud
server) and everyone else – you, the operations manager, drivers – simply opens
a web address in their phone or PC browser. Nothing to install on phones.

```
                 ┌──────────────────────────┐
  Manager phone ─┤                          │
  Your PC       ─┤  Planner (one instance)  ├─ data/ (customers, orders, settings)
  Driver phone  ─┤  https://…/              │
                 └──────────────────────────┘
```

Always set a **team password** (`APP_PASSWORD`) before sharing – without it
anyone with the link can see and change your customers.

| Option | Cost | Works from | PC must stay on? | Setup effort |
|---|---|---|---|---|
| **F. Streamlit Community Cloud + Neon** ⭐ free & no PC | ₹0 | Anywhere, 24×7 (wakes in ~30–60 s after 12 h idle) | No | 20 min, no server skills |
| **A. Office PC on Wi-Fi** | ₹0 | Same Wi-Fi only | Yes | 5 min |
| **B. Office PC + Tailscale** | ₹0 | Anywhere | Yes | 15 min |
| **C. Cloud – Railway or Render** (always-on, no wake-up) | ≈ $5–7 / month | Anywhere, 24×7 | No | 15 min, no server skills |
| **D. Cloud – PythonAnywhere** | ₹0 (needs Google key) or $5 | Anywhere, 24×7 | No | 25 min |
| **E. Your own server / VPS (Docker)** | ≈ $4–6 / month | Anywhere, 24×7 | No | 20 min, basic Linux |

---

## F. Streamlit Community Cloud + Neon Postgres (free, nothing to keep switched on)

`streamlit_app.py` is a second front end for the **same** engine, written for
[Streamlit Community Cloud](https://share.streamlit.io) (free hosting straight
from this GitHub repo). Streamlit's disk is wiped on every restart, so the data
lives in a free [Neon](https://neon.tech) Postgres database via `DATABASE_URL`
(the Flask app can use the very same database).

Free-tier facts: 1 GB RAM per app, the app **sleeps after ~12 h without
visitors** and takes 30–60 s to wake on the next visit, and a free workspace
may have **one private app** (plus unlimited public ones). The password gate
works either way – keep the app private if you can.

### F.1 Database (Neon, 5 min)
1. <https://neon.tech> → sign up (GitHub/Google) → **New project**.
   Name `onion-planner`, Postgres 16/17, region **US East (N. Virginia)** or
   **US West (Oregon)** – Streamlit's servers are in the USA, the database talks
   to *them*, not to your phones.
2. On the project dashboard click **Connect** → choose **Connection string**
   (keep *pooled connection* ticked) → copy the line that looks like
   `postgresql://neondb_owner:npg_xxx@ep-xxx-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require`.
   That whole line is your `DATABASE_URL`. Nothing else to create – the app
   makes its own table on first start.

### F.2 App (Streamlit Community Cloud, 10 min)
1. <https://share.streamlit.io> → **Continue with GitHub** → allow access to
   the `onion-route-planner` repository when asked.
2. **Create app** → *Deploy a public app from GitHub* (don't worry, see step 5):
   * Repository: `phageforlifeai/onion-route-planner`
   * Branch: `main`
   * Main file path: `streamlit_app.py`
   * App URL: pick something like `onion-routes` → `https://onion-routes.streamlit.app`
3. **Advanced settings…** → Python version **3.12** → in the **Secrets** box paste:
   ```toml
   APP_PASSWORD = "your-team-password"
   DATABASE_URL = "postgresql://…neon.tech/neondb?sslmode=require"
   GOOGLE_MAPS_API_KEY = ""          # optional, for historic-traffic routing
   ```
4. **Deploy**. The first build installs OR-Tools etc. and takes 3–5 minutes;
   watch the log on the right. When it opens you'll see the password screen.
5. Make it private: app menu (⋮ bottom-right / *Manage app*) → **Settings** →
   **Sharing** → *Who can view this app* → **Only specific people**, then add
   your team's e-mail addresses (they sign in once with that Google/GitHub/e-mail
   account). You can also leave it public – the team password still applies.
6. Every `git push` to `main` redeploys automatically. Secrets can be changed
   any time under *Settings → Secrets* (the app restarts).

### F.3 Phones
Open the URL, sign in once with *Keep me signed in* ticked – the address now
ends in `?k=…`. **Add to Home Screen / bookmark that address** and it opens
straight into the Plan screen next time. The first open after a quiet night
takes 30–60 s while the free server wakes up.

### F.4 Local run of the Streamlit version
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py          # uses ./data/*.json like the Flask app
# with a database:  copy .streamlit/secrets.toml.example → .streamlit/secrets.toml and fill it in
```

---

## A. Office PC, same Wi-Fi (simplest)

1. Install Python from <https://www.python.org/downloads/> (tick **Add python.exe to PATH**).
2. Unzip the project to e.g. `C:\onion-route-planner` and double-click **`run.bat`**.
   First run: it creates `.env` and opens it in Notepad → set `APP_PASSWORD=…` and, to share
   data with the Streamlit Cloud app, `DATABASE_URL=` the same Neon string. Save and run again.
3. The window prints two addresses: `http://localhost:5000` (this PC) and
   `http://192.168.x.x:5000` (phones/PCs on the same Wi-Fi). Keep the window open.
4. Optional: make it start with Windows – press `Win+R`, type `shell:startup`, and drop a
   shortcut to `run.bat` in that folder.

## B. Office PC + Tailscale (access from anywhere, free)

Tailscale creates a private network between your devices – no port forwarding,
no public exposure.

1. Install Tailscale (tailscale.com/download) on the office PC **and** on each phone/PC that needs access; sign in with the same account (free plan: 3 users, 100 devices – invite the manager as a user).
2. Start the planner on the PC as in option A (with `APP_PASSWORD`).
3. In the Tailscale app, note the PC's name/IP (e.g. `office-pc` / `100.101.5.7`).
4. From anywhere: `http://office-pc:5000` (or `http://100.101.5.7:5000`).

Limitation of A and B: the PC must be switched on when someone plans.

## C. Cloud from GitHub – Railway or Render (recommended)

Both deploy straight from your GitHub repo, give you an HTTPS address like
`https://onion-route-planner.up.railway.app`, restart automatically and redeploy
whenever you push an update. The data folder lives on a small persistent disk.

### Railway (≈ $5/month – Hobby plan, usually covered by its included credit)
1. railway.app → *Login with GitHub* → **New Project → Deploy from GitHub repo** → choose `onion-route-planner` (Railway detects the `Dockerfile`).
2. Service → **Variables** → add
   `APP_PASSWORD` = your team password · `SECRET_KEY` = any long random text · `DATA_DIR` = `/data`
3. Service → **Volumes → + New Volume** → mount path `/data`.
4. **Settings → Networking → Generate Domain** → share that URL.
5. Optional: add `GOOGLE_MAPS_API_KEY` as a variable (or paste the key in the app's Settings).

### Render (Starter $7/month + 1 GB disk ≈ $0.25)
1. render.com → *Sign in with GitHub* → **New + → Blueprint** → select the repo. Render reads `render.yaml` (Singapore region, disk at `/data`, health check).
2. It asks for `APP_PASSWORD` → type it → **Apply**.
3. After the first build, open the service URL `https://onion-route-planner.onrender.com`.

> Render's **free** plan has no disk – data would vanish on each restart. If you
> want ₹0 on Render, create a free Postgres database at **neon.tech**, copy its
> connection string and add it as `DATABASE_URL` in Render; the planner then stores
> everything in Postgres instead of files (expect a 30–60 s wake-up on first use
> after idle periods).

## D. PythonAnywhere (always-on, web-based setup)

Free plan works **only with a Google Maps key** (free accounts cannot reach the
OpenStreetMap routing servers; `googleapis.com` is allowed). The $5 "Hacker"
plan has no such restriction.

1. pythonanywhere.com → sign up → **Consoles → Bash**:
   ```bash
   git clone https://github.com/phageforlifeai/onion-route-planner.git
   cd onion-route-planner && pip install --user -r requirements.txt
   ```
2. **Web → Add a new web app → Manual configuration → Python 3.12**.
3. *Code* section: Source code = `/home/<you>/onion-route-planner`. Click the WSGI file and replace its content with:
   ```python
   import os, sys
   sys.path.insert(0, "/home/<you>/onion-route-planner")
   os.environ["APP_PASSWORD"] = "YourPassword"
   os.environ["SECRET_KEY"] = "any-long-random-text"
   from app import app as application
   ```
4. *Static files*: URL `/static/` → directory `/home/<you>/onion-route-planner/static`.
5. **Reload** → open `https://<you>.pythonanywhere.com`. (Free accounts: click "Run until …" every 3 months.)

## E. Your own Linux server / VPS (Docker)

Any ₹300–500/month VPS (Hetzner, DigitalOcean, Hostinger, AWS Lightsail, Oracle Cloud free tier):
```bash
git clone https://github.com/phageforlifeai/onion-route-planner.git
cd onion-route-planner
nano docker-compose.yml        # set APP_PASSWORD and SECRET_KEY
docker compose up -d           # → http://<server-ip>
```
For HTTPS put Caddy or nginx in front, or use Cloudflare Tunnel.

---

## After deployment

* **Phones:** open the URL → sign in once (stays signed in 60 days) → browser menu → *Add to Home Screen*. Phone layout has three views: **Plan · Map · Routes**.
* **Drivers** don't need a login: send them the WhatsApp text or Google Maps link from the Routes view.
* **Updates:** push to GitHub → Railway/Render redeploy automatically. On a PC/VPS run `git pull` and restart.
* **Backup:** copy the `data/` folder (or the `/data` volume) occasionally – it holds customers, settings and orders.
* **Change password:** change `APP_PASSWORD` and restart – everyone is signed out automatically.

## Environment variables

| Variable | Purpose |
|---|---|
| `APP_PASSWORD` | Team login password (empty = no login, for local use only) |
| `SECRET_KEY` | Signs login cookies; set once so logins survive restarts |
| `DATA_DIR` | Folder for the JSON data files (default: `./data`) |
| `DATABASE_URL` | Optional Postgres URL – store data in a database instead of files |
| `GOOGLE_MAPS_API_KEY` | Optional – same as pasting the key in Settings |
| `PORT` | Port to listen on (default 5000; cloud hosts set this automatically) |
