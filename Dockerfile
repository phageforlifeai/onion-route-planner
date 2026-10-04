# Onion Route Planner – container image (works on Railway, Render, Fly.io, Cloud Run, any VPS)
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data lives in /data – mount a volume there (or set DATABASE_URL instead)
ENV DATA_DIR=/data PORT=5000 PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 5000

CMD ["sh", "-c", "gunicorn -w 1 --threads 6 -t 180 -b 0.0.0.0:${PORT} app:app"]
