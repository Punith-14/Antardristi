# Antardrishti: the web app and the API in one image, one origin.
#
#   docker build -t antardrishti .
#   docker run -p 8000:8000 --env-file backend/.env -v antardrishti-data:/app/data antardrishti
#
# See DEPLOYMENT.md for Earth Engine credentials inside a container.

# ---- 1. build the web app -----------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. the API, serving the built app ----------------------------------
FROM python:3.12-slim

# Devanagari font for the Hindi PDF (shaped by uharfbuzz).
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    HINDI_FONT_PATH=/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY contracts/ contracts/
COPY evaluation/ evaluation/
COPY models/ models/
COPY --from=web /web/dist frontend/dist

# Writable state lives in /app/data (users, jobs, cache) and /app/outputs
# (upload overlays) - mount a volume on /app/data to keep it.
RUN mkdir -p data outputs/images \
 && useradd --create-home --uid 10001 app \
 && chown -R app:app /app/data /app/outputs
USER app

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

# One worker process: jobs, the rate limiter and the SQLite store are
# per-process (DEPLOYMENT.md says what to change to run several).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "1", "--no-access-log"]
