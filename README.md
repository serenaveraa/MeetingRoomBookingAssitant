# Meeting Room Booking Assistant

AI Agent Case Study: ODC Meeting Room Booking Assistant

## Prerequisites

- Python 3.11+
- **Docker Desktop** (required — the app database is Postgres in Docker)

## 1. Start the database (Docker Postgres)

```bash
docker compose up -d db
docker compose ps
```

Wait until the `db` service shows **healthy**.

| Setting | Value |
|---|---|
| URL | `postgresql+psycopg://odc:odc@localhost:5432/meeting_room` |
| User / password | `odc` / `odc` |
| Database | `meeting_room` |
| Port | `5432` |

Optional: copy [`.env.example`](.env.example) to `.env` at the repo root (defaults already match Docker).

The backend uses `DATABASE_URL` as the single source of truth for database selection. For Postgres, the default value is:

```text
postgresql+psycopg://odc:odc@localhost:5432/meeting_room
```

On startup, Postgres initialization enables `btree_gist` and installs the confirmed-booking overlap exclusion constraint. To apply the same schema explicitly during a deployment, run the raw migration scripts in order:

```bash
docker compose exec -T db psql -U odc -d meeting_room < backend/migrations/001_add_vacate_reminder_claim.sql
docker compose exec -T db psql -U odc -d meeting_room < backend/migrations/002_add_waitlist_room.sql
docker compose exec -T db psql -U odc -d meeting_room < backend/migrations/003_add_booking_overlap_exclusion.sql
```

The exclusion constraint requires the PostgreSQL `btree_gist` extension. It applies only to `confirmed` bookings and treats time windows as half-open intervals `[start_at, end_at)`, so adjacent bookings are allowed. SQLite has no equivalent exclusion constraint; it continues to use the booking service's transactional application-level overlap check.

Inspect tables after the API has started once:

```bash
docker compose exec db psql -U odc -d meeting_room -c "SELECT id, name FROM rooms;"
```

Stop containers (keeps volume data unless you add `-v`):

```bash
docker compose down
```

## 2. Backend (uses Docker Postgres)

```bash
cd backend
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On startup the API runs `init_db()` against Postgres (creates tables + seeds **ODC Common Meeting Room**).

Health check: http://127.0.0.1:8000/health

Natural-language agent: `POST /agent/chat` with JSON `{ "message", "associate_email", "associate_name", "conversation_id?" }` (requires `GROQ_API_KEY` or other LLM settings for live calls).

### Tests

The CI-friendly command for the complete backend suite is:

```bash
cd backend
python -m pytest -q
```

The test suite uses in-memory SQLite for fast unit/API tests. Postgres integration tests are marked `postgres` and use the Docker database configured by `DATABASE_URL`:

```bash
python -m pytest -q -m postgres       # Postgres-only integration checks
python -m pytest -q -m "not postgres" # Fast SQLite-only checks
```

CI starts PostgreSQL 16 and sets `DATABASE_URL` before running the complete command. Locally, start Docker Postgres with `docker compose up -d db`; if it is unavailable, the marked integration tests skip while the SQLite suite still runs.

## 3. Frontend (Streamlit calendar)

Requires the backend API running (step 2). Use a **frontend venv** so Streamlit deps do not clash with other global packages.

```bash
cd frontend
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
# optional: set API_BASE_URL if the API is not on localhost:8000
set API_BASE_URL=http://127.0.0.1:8000
.venv/Scripts/python.exe -m streamlit run app.py
```

The calendar day view loads confirmed bookings from `GET /bookings`, shows free gaps within business hours (08:00–18:00 ODC time), and stores associate name/email in the Streamlit session (sidebar). The **Chat** tab talks to `POST /agent/chat` with that identity and keeps conversation history in the session.

## Configuration

See [`.env.example`](.env.example) for all variables. Architecture: [`architecture.md`](architecture.md).

- `API_BASE_URL` — FastAPI base URL for the Streamlit UI (default `http://127.0.0.1:8000`)
- For the LangGraph agent (live LLM calls), set `GROQ_API_KEY` (recommended free tier), or `OPENAI_API_KEY`, or Azure OpenAI vars (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`). Optional: `GROQ_MODEL` (default `llama-3.3-70b-versatile`). Unit tests mock the model and do not call the network.

CI runs backend tests on pushes/PRs to `develop` and `main` (Postgres service + `pytest`).
