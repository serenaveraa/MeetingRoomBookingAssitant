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

### Tests

With Docker Postgres running:

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
```

- Unit tests (`test_models.py`) use in-memory SQLite only for speed
- Integration tests (`test_postgres_init.py`) require Docker Postgres and skip if it is down

## 3. Frontend

```bash
cd frontend
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

## Configuration

See [`.env.example`](.env.example) for all variables. Architecture: [`architecture.md`](architecture.md).

For the LangGraph agent (live LLM calls), set `OPENAI_API_KEY` **or** Azure OpenAI vars (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`). Unit tests mock the model and do not call the network.

CI runs backend tests on pushes/PRs to `develop` and `main` (Postgres service + `pytest`).
