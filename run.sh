#!/usr/bin/env bash
cd "$(dirname "$0")"
pip install -q -r requirements.txt
(sleep 2 && (xdg-open http://localhost:5000 || open http://localhost:5000) >/dev/null 2>&1) &
python3 app.py
