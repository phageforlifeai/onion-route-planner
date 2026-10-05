#!/usr/bin/env bash
# Office-PC / Mac launcher. Settings are read from ./.env (copy .env.example).
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3 is not installed – see https://www.python.org/downloads/"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Created .env – edit it (APP_PASSWORD, DATABASE_URL) and run again."; exit 0; }
python3 -m pip install -q -r requirements.txt
(sleep 2 && (xdg-open http://localhost:5000 || open http://localhost:5000) >/dev/null 2>&1) &
python3 app.py
