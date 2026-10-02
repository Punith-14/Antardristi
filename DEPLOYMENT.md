# Running Antardrishti as a product

One server serves the web app and the API from one address. Everything it
needs to know comes from `backend/.env` (template: `backend/.env.example`).

## 1. First-time setup (any machine)

1. **Configuration.** Copy `backend/.env.example` to `backend/.env` and fill in:
   - `EE_PROJECT_ID`, `GROQ_API_KEY`
   - `SECRET_KEY` — a long random string:
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `ADMIN_USERNAME` / `ADMIN_PASSWORD` for the first admin (created on the
     first start only; delete the password from the file afterwards), **or**
     create the admin from the command line (step 3).
2. **Earth Engine credentials.**
   - Laptop: `earthengine authenticate` once (a personal login).
   - Server: a Google Cloud **service account** registered for Earth Engine.
     Set `EE_SERVICE_ACCOUNT` and `EE_PRIVATE_KEY_FILE` (path to its JSON
     key). Never commit the key; `secrets/` is gitignored.
3. **Accounts** (from `backend/`):
   ```
   python -m scripts.manage_users create <name> --role admin
   python -m scripts.manage_users create <name> --role analyst
   python -m scripts.manage_users list
   ```
   Roles: **viewer** (reads results, maps, PDFs, downloads) · **analyst**
   (also runs analyses and uploads) · **admin** (also manages users).
   Admins can also manage users from the web app (Users button).

## 2a. Run on a Windows laptop

```
.\start.ps1            # builds the web app if needed, starts, opens the browser
.\start.ps1 -Rebuild   # after changing frontend code
```

Then open http://127.0.0.1:8000 and sign in.

## 2b. Run in Docker (any host)

```
docker compose up --build        # http://localhost:8000
```

Users, jobs and the result cache live in the `antardrishti-data` volume.
For Earth Engine inside the container use a service account (mount the key
as shown in `docker-compose.yml`).

## 2c. A cloud platform

Any host that runs a container works (Google Cloud Run, Render, Railway,
Azure Container Apps, a VM). Requirements:

- the image from the `Dockerfile`, port 8000;
- the `.env` values as the platform's environment variables / secrets;
- a **persistent volume** on `/app/data` (otherwise users and history reset
  on every deploy);
- HTTPS in front, with `COOKIE_SECURE=true`;
- **one** instance (see "Scaling" below);
- no long request timeout is needed: analyses run as
  background jobs and the browser polls.

## 3. Check it is healthy

| URL | Meaning |
|---|---|
| `/health` | the process answers (for uptime monitors) |
| `/ready` | Earth Engine starts, a Groq key is set, an account exists — 503 with the reason if not |

Logs are one JSON line per request (`LOG_FORMAT=json`), with request id,
user, status and duration; an unexpected error is logged with its traceback
and an incident id, and the user sees only the incident id.

## 4. What is protected, and how

| Area | Protection |
|---|---|
| Sign-in | PBKDF2-SHA256 password hashes; HMAC-signed sessions in an HttpOnly, SameSite=Strict cookie; 5 failed attempts lock a username for 15 min |
| Roles | checked on every request; a role change or deactivation applies at once |
| CSRF | cookie-authenticated changes need the `X-Requested-With` header |
| CORS | only the origins in `CORS_ORIGINS` (the built app needs none) |
| Quota | per-user hourly limit on analyses (`ANALYSES_PER_HOUR`) |
| Uploads | 25 MB request limit, zip-bomb check, uploaded files deleted after `UPLOAD_RETENTION_DAYS` |
| Files served | only upload overlays; the cache, raw data and uploaded boundaries are never served |
| Headers | nosniff, no framing, no referrer, a content-security policy for the app |
| Secrets | `.env`, keys and the user database are gitignored and kept out of the image |

## 5. Scaling beyond one instance

The job queue, the rate limiter and the SQLite store are per process, which
is right for a department-sized deployment. To run several instances:
move users/jobs to PostgreSQL (`DATABASE_PATH` -> a database URL), the
result cache to object storage, the rate limiter to Redis, and the job pool
to a queue worker (Celery/RQ). The code isolates each of these in one module
(`core/auth.py`, `core/jobs.py`, `core/cache.py`, `core/security.py`).

## 6. Known external dependencies

- **Earth Engine** quota (non-commercial: about 150 EECU-hours a month).
- **Groq** rate limits; if the model is unavailable, reports fall back to the
  deterministic template and are still verified.
- **OpenStreetMap Overpass** public servers for village and road names;
  three are tried, and if all fail the names are reported as unavailable —
  the flood result is unaffected. For heavy use, run a private Overpass.
- **FAO GAUL 2015** boundaries are deprecated by Earth Engine; GAUL 2025 is
  measured (`scripts/compare_boundaries.py`) and used as a fallback.
