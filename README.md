# Meeting Room Booking Assistant

AI Agent Case Study: ODC Meeting Room Booking Assistant

## Live AWS environment (Free Tier)

Region **`us-east-2`**, CloudFormation stack **`odc-meeting`**.

| What | URL |
|---|---|
| **Streamlit UI** | http://18.220.50.230:8501 |
| **API (HTTP API Gateway)** | https://pzjiegjp46.execute-api.us-east-2.amazonaws.com |
| **Health** | https://pzjiegjp46.execute-api.us-east-2.amazonaws.com/health |

RDS host: `odc-meeting-room.cfc04mss8inc.us-east-2.rds.amazonaws.com` (credentials in gitignored `infra/local.env`).

Anyone can open the Streamlit link in a browser — no local install required. The EC2 public IP can change if the instance is replaced; refresh URLs from stack outputs or `infra/local.env` after redeploy.

Deploy / update / tear-down: [`infra/README.md`](infra/README.md). Architecture: [`architecture-aws.md`](architecture-aws.md).

**Schedule rule:** the room can only be booked **Monday–Friday** (ODC local time). Weekends are rejected by the API, agent tools, and UI.

## Prerequisites

- Python 3.11+
- **Docker Desktop** (optional — for local Postgres or building the Lambda image)
- **AWS CLI** (for cloud deploy / RDS provisioning — see [`infra/README.md`](infra/README.md))

## 1. Database

### AWS RDS (preferred)

After CloudFormation (`infra/scripts/deploy.sh`), use the URL in gitignored [`infra/local.env`](infra/local.env) as `DATABASE_URL` in `.env`. Include `?sslmode=require`.

Alternative shell provisioner (issue #43, Secrets Manager `odc-mrba/DATABASE_URL`):

```bash
cp infra/config.env.example infra/config.env
chmod +x infra/*.sh
./infra/provision-rds.sh
./infra/init-rds-db.sh
./infra/verify-rds.sh
```

### Optional: Docker Postgres (offline only)

```bash
docker compose up -d db
# DATABASE_URL=postgresql+psycopg://odc:odc@localhost:5432/meeting_room
```

On startup, Postgres initialization enables `btree_gist` and installs the confirmed-booking overlap exclusion constraint. Raw migrations live under `backend/migrations/`. SQLite is used only in unit tests.

## 2. Backend (uses `DATABASE_URL` — RDS or Docker)

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

Requires the backend API running (step 2), **or** set `API_BASE_URL` to the live API Gateway URL above. Use a **frontend venv** so Streamlit deps do not clash with other global packages.

```bash
cd frontend
py -3 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
# optional: set API_BASE_URL if the API is not on localhost:8000
set API_BASE_URL=http://127.0.0.1:8000
.venv/Scripts/python.exe -m streamlit run app.py
```

The calendar day view loads confirmed bookings from `GET /bookings`, shows free gaps within business hours (08:00–18:00 ODC time) on **weekdays only**, and stores associate name/email in the Streamlit session (sidebar). The **Chat** tab talks to `POST /agent/chat` with that identity and keeps conversation history in the session.

## Configuration

See [`.env.example`](.env.example) for all variables. Architecture: [`architecture.md`](architecture.md). Fully cloud AWS Free Tier deployment (EC2 Streamlit + Lambda API + RDS): [`architecture-aws.md`](architecture-aws.md).

### Deploy to AWS (CloudFormation)

Free Tier stack (EC2 + Lambda/API Gateway + RDS + EventBridge): see [`infra/README.md`](infra/README.md) and [`infra/cloudformation/odc-stack.yaml`](infra/cloudformation/odc-stack.yaml).

```bash
BootstrapMode=true ./infra/scripts/deploy.sh
./infra/scripts/build_and_push.sh
./infra/scripts/deploy.sh
```

Lambda handlers (no deploy required to unit-test): `app.lambda_handlers.api_handler` and `app.lambda_handlers.reminder_handler`. Set `RUNNING_IN_LAMBDA=true` on AWS so APScheduler stays off and DB uses short-lived connections. Locally keep it false and run `uvicorn` as usual.

- `API_BASE_URL` — FastAPI base URL for the Streamlit UI (default `http://127.0.0.1:8000`)
- For the LangGraph agent (live LLM calls), set `GROQ_API_KEY` (recommended free tier), or `OPENAI_API_KEY`, or Azure OpenAI vars (`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`). Optional: `GROQ_MODEL` (default `llama-3.3-70b-versatile`). Unit tests mock the model and do not call the network.

CI runs backend tests on pushes/PRs to `develop` and `main` (Postgres service + `pytest`).
