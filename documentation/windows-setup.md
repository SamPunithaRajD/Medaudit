# Windows setup (backend)

Tested on Windows 11 with Python 3.12 and PostgreSQL 18. Docker is not required.

## 1. Python dependencies

```powershell
cd C:\path\to\medaudit\backend
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt
```

## 2. PostgreSQL

Install from https://www.postgresql.org/download/windows/ (the installer registers the `postgresql-x64-NN`
service). Then, in **SQL Shell (psql)** or `"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres`:

```sql
CREATE ROLE medaudit LOGIN PASSWORD 'choose-a-strong-password';
CREATE DATABASE medaudit OWNER medaudit ENCODING 'UTF8';
```

In `backend\.env`: `DATABASE_URL=postgresql+psycopg://medaudit:choose-a-strong-password@localhost:5432/medaudit`
(URL-encode special characters in the password, e.g. `@` → `%40`).

## 3. Configuration and migrations

```powershell
copy .env.example .env
.\.venv\Scripts\python scripts\generate_secrets.py     # paste the 4 lines into .env
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python scripts\create_admin.py --login-id ADM001 --email admin@hospital.local --name "Administrator"
```

The password must be ≥12 characters with three character classes and must not contain the email name.
Optional synthetic demo: `.\.venv\Scripts\python scripts\seed_demo.py` (prints a one-time demo password).

## 4. Ollama (optional, local LLM)

1. Install from https://ollama.com/download/windows (runs on `http://localhost:11434`).
2. `ollama pull llama3.2:3b`
3. Check: `curl http://localhost:11434/api/tags` and, once logged in, `GET /api/v1/system/ollama-status`.

## 5. Tesseract (optional, scanned images)

Install the UB Mannheim build (https://github.com/UB-Mannheim/tesseract/wiki). The default path
`C:\Program Files\Tesseract-OCR\tesseract.exe` is detected automatically; otherwise set `TESSERACT_CMD`.

## 6. Run

```powershell
# terminal 1
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
# terminal 2
.\.venv\Scripts\python -m app.workers.run_worker
```

For a single-terminal dev setup set `WORKER_MODE=embedded` in `.env`.

## 7. Tests

```powershell
.\.venv\Scripts\python -m pytest
$env:TEST_DATABASE_URL="postgresql+psycopg://medaudit:...@localhost:5432/medaudit_test"; .\.venv\Scripts\python -m pytest
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No encryption key configured` at startup | set `ENCRYPTION_KEYS` (or `ENCRYPTION_KEY`) in `.env` |
| `SESSION_SECRET must be set` | add a ≥32-character secret |
| `fe_sendauth: no password supplied` / auth failed | check `DATABASE_URL` user/password; service running (`sc query postgresql-x64-18`) |
| `Roles table is empty` | run `alembic upgrade head` before `create_admin.py` |
| Jobs stay `queued` | start the worker, or `WORKER_MODE=embedded` |
| Pages `needs_manual_review` with `no_ocr_provider_available` | install Tesseract (scanned images) |
| Ollama status `reachable: false` | start Ollama; `ollama pull llama3.2:3b`; processing still works deterministically |
| `LOCAL_ONLY_AI is enabled and OLLAMA_BASE_URL is not local` | point to localhost/LAN host or add it to `LOCAL_AI_ALLOWED_HOSTS` |
| Browser login works but requests are 401 | frontend must send cookies (`withCredentials: true`) and origin must be in `FRONTEND_ORIGINS` |
| 403 `CSRF_REJECTED` | send `X-CSRF-Token` (from login or `/auth/me`) on writes, and keep the request `Origin` in `ALLOWED_ORIGINS` |
| Analytics values are `null` | small-cell suppression (`ANALYTICS_MIN_CELL_SIZE`, default 5) |
| `ZoneInfoNotFoundError` | `pip install tzdata` (in requirements) |


## 8. Accounts quick start

```powershell
cd backend
.\.venv\Scripts\alembic upgrade head                      # applies 0002_accounts
.\.venv\Scripts\python scripts\create_admin.py --login-id ADM001 --email admin@hospital.local --name "System Administrator"
.\.venv\Scripts\uvicorn app.main:app --port 8000          # terminal 1 (WORKER_MODE=embedded runs the worker too)
cd ..
npm install
npm run dev                                                # terminal 2 → http://localhost:5173
```

Sign in at http://localhost:5173/login with `ADM001`. Add a doctor, give them the one-time code, and they
activate at `/activate`. Details: `authentication-architecture.md`.
