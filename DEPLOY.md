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
| **A. Office PC on Wi-Fi** | ₹0 | Same Wi-Fi only | Yes | 5 min |
| **B. Office PC + Tailscale** | ₹0 | Anywhere | Yes | 15 min |
| **C. Cloud – Railway or Render** ⭐ recommended | ≈ $5–7 / month | Anywhere, 24×7 | No | 15 min, no server skills |
| **D. Cloud – PythonAnywhere** | ₹0 (needs Google key) or $5 | Anywhere, 24×7 | No | 25 min |
| **E. Your own server / VPS (Docker)** | ≈ $4–6 / month | Anywhere, 24×7 | No | 20 min, basic Linux |

---

## A. Office PC, same Wi-Fi (simplest)

1. On the PC: `pip install -r requirements.txt`, then start **with a password**:
   * Windows (Command Prompt): `set APP_PASSWORD=YourPassword && python app.py`
   * Mac/Linux: `APP_PASSWORD=YourPassword python3 app.py`
2. Allow it when Windows Firewall asks ("Allow access" on private networks).
3. Find the PC's address: `ipconfig` (Windows) → *IPv4 Address*, e.g. `192.168.1.25`.
4. On any phone/PC on the **same Wi-Fi** open `http://192.168.1.25:5000` → sign in.
5. Phone: browser menu → **Add to Home Screen** – it then opens like an app.

Tip: give the PC a fixed IP in your router so the address never changes, and
create a shortcut `run_shared.bat` containing the two lines in step 1.

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
