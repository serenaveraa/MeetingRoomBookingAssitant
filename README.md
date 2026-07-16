# Meeting Room Booking Assistant

AI Agent Case Study: ODC Meeting Room Booking Assistant

## Prerequisites

- Python 3.11+
- **Docker Desktop** (required for the database; start Docker Desktop before `docker compose`)

The project is designed to run the **Postgres database in Docker**. SQLite remains available only for a quick offline scaffold demo until models land.

## Database (Docker)

Start Postgres:

```bash
docker compose up -d db
```

Check it is healthy:

```bash
docker compose ps
```

Connection URL (also in `.env.example`):

```text
DATABASE_URL=postgresql+psycopg://odc:odc@localhost:5432/meeting_room
```

Stop / remove containers (keeps volume data unless you add `-v`):

```bash
docker compose down
```

## Backend (local)

```bash
cd backend
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
# optional: copy ../.env.example to .env and set DATABASE_URL to the Docker Postgres URL
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: http://127.0.0.1:8000/health

## Frontend (local)

```bash
cd frontend
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

## Configuration

See [`.env.example`](.env.example) for all variables. Architecture notes: [`architecture.md`](architecture.md).
