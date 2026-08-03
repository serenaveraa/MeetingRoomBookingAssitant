# Architecture Plan: ODC Meeting Room Booking Assistant

## 1. Overview

A single shared ODC meeting room is managed by an AI agent that understands natural language, enforces non-overlapping bookings, sends vacate reminders, handles extensions/cancellations, and surfaces utilization insights.

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Streamlit  │────▶│  FastAPI Backend │────▶│  Postgres (Docker)│
│  Frontend   │◀────│                  │◀────│                 │
└─────────────┘     │  ┌────────────┐  │     └─────────────────┘
                    │  │ LangGraph  │  │
                    │  │ AI Agent   │──┼────▶ OpenAI / Azure OpenAI
                    │  └────────────┘  │
                    │  ┌────────────┐  │     ┌─────────────────┐
                    │  │ Scheduler  │──┼────▶│ Notification    │
                    │  └────────────┘  │     │ Service         │
                    └──────────────────┘     │  ├─ Brevo Email  │
                                             │  └─ Teams hook   │
                                             └─────────────────┘
```

## 2. Design Principles

| Principle | Rationale |
|---|---|
| Single source of truth | All availability and conflict logic lives in the booking service, not the LLM |
| Agent proposes, system decides | The LLM extracts intent and slots; deterministic services validate and persist |
| Idempotent mutations | Book / extend / cancel operations are safe to retry |
| Timezone-aware | Store UTC; display in ODC local time |
| Fail closed on conflicts | Prefer rejecting a booking over allowing an overlap |

## 3. Component Architecture

### 3.1 Frontend (Streamlit)

- Chat UI for natural-language interaction with the agent
- Calendar / day view of room occupancy
- Simple utilization dashboard (bookings per day, average duration, idle gaps)
- User identity context (associate name / email) passed with each request

### 3.2 API Layer (FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /agent/chat` | Primary NL entry point; runs LangGraph agent |
| `GET /bookings` | List bookings for a date range |
| `GET /bookings/availability` | Free slots for a given day / duration |
| `POST /bookings` | Direct booking (optional; mainly used by agent tools) |
| `PATCH /bookings/{id}/extend` | Extend an active booking |
| `DELETE /bookings/{id}` | Cancel a booking |
| `GET /insights/utilization` | Utilization metrics |
| `GET /health` | Liveness check |

### 3.3 AI Agent (LangChain / LangGraph)

The agent is a tool-calling graph. The LLM never writes to the DB directly.

**Tools (bound to booking services):**

| Tool | Maps to requirement |
|---|---|
| `check_availability` | Room availability check |
| `create_booking` | Natural language booking |
| `suggest_alternatives` | Conflict detection |
| `extend_booking` | Meeting extension |
| `cancel_booking` | Booking cancellation |
| `list_my_bookings` | Context for cancel/extend |
| `get_utilization_summary` | Room utilization insights |

**Agent graph (happy path):**

```mermaid
flowchart TD
    A[User message] --> B[Parse intent + extract entities]
    B --> C{Intent}
    C -->|availability| D[check_availability]
    C -->|book| E[create_booking]
    C -->|extend| F[extend_booking]
    C -->|cancel| G[cancel_booking]
    C -->|insights| H[get_utilization_summary]
    E -->|conflict| I[suggest_alternatives]
    D --> J[Compose natural-language reply]
    E --> J
    F --> J
    G --> J
    H --> J
    I --> J
    J --> K[Response to user]
```

**Entity extraction targets:** date, start time, end time (or duration), purpose, associate identity.

Ambiguous requests (e.g. “book room tomorrow for 30 minutes” without a start time) should trigger a clarifying question before calling mutation tools.

### 3.4 Booking Service (core domain)

Deterministic business rules:

1. **Overlap prevention** — reject if `[start, end)` intersects any active booking
2. **Alternatives** — when rejected, return nearest free windows of equal duration on the same day
3. **Extension** — approve only if the extended interval remains free; otherwise reject with the conflicting associate and start time
4. **Cancellation** — mark cancelled, free the slot, notify waitlist
5. **Waitlist** — optional queue of associates interested in a slot; notified on release

Overlap checks must run inside a DB transaction (and with a uniqueness / exclusion constraint where the DB supports it).

### 3.5 Notification Service

Notifications are sent through a thin adapter so channels can be enabled independently.

| Channel | Provider | Use |
|---|---|---|
| Email | **Brevo** (Transactional Email API) | Booking confirmations, extensions, cancellations, waitlist alerts, vacate reminders |
| Microsoft Teams | Incoming Webhook or Microsoft Graph (app-owned) | Same events when Teams delivery is enabled |

**Can Brevo send Teams messages?**

No — not as a native API for our booking events. Brevo’s product surface is email, SMS, and WhatsApp. Any “Brevo ↔ Teams” listings (Zapier, Make, Zoho Flow) are marketing/automation bridges for Brevo account events (new contact, campaign status), not a way for our backend to push vacate/booking messages into Teams.

**Recommended split for this project:**

1. **Brevo** — primary channel; all associate-facing email via `POST https://api.brevo.com/v3/smtp/email`
2. **Teams** — optional second channel from our notification service (Incoming Webhook to a room/channel, or Graph chat message to an associate)

Do not route booking notifications through Zapier solely to reach Teams; keep delivery in-process for reliability and simpler ops.

**Brevo email flow**

```
Booking event / reminder
        │
        ▼
NotificationService.notify(event, associate)
        │
        ├──▶ BrevoTransactionalClient.send_template(...)
        └──▶ TeamsClient.send(...)   # optional, separate from Brevo
```

| Event | Brevo template (examples) |
|---|---|
| `booking.confirmed` | Room booked — date, time, purpose |
| `booking.extended` | New end time |
| `booking.cancelled` | Slot released |
| `booking.vacate_reminder` | Ends in 15 min; next meeting waiting |
| `waitlist.slot_available` | Requested window is free |

**15-minute vacate job**

- Background scheduler (APScheduler or Celery beat) polls upcoming end times
- At `end_time - 15 min`, if a next booking starts at or near `end_time`, send vacate reminder (Brevo email ± Teams)
- Track `reminder_sent_at` to avoid duplicates

### 3.6 Persistence

**Recommended entities:**

```
Associate
  id, name, email, teams_id

Booking
  id, room_id, associate_id, purpose
  start_at, end_at          -- UTC timestamptz
  status                    -- confirmed | cancelled | completed
  reminder_sent_at
  created_at, updated_at

WaitlistEntry
  id, associate_id, desired_start, desired_end, created_at, notified_at

Room
  id, name                  -- single row for ODC common room
```

**Constraints:**

- `CHECK (end_at > start_at)`
- No overlapping `confirmed` bookings for the same room (PostgreSQL `EXCLUDE USING gist` in phase 5; app-level checks until then)

**DB choice:** **PostgreSQL in Docker** is required for local development and runtime (`docker compose up -d db`). In-memory SQLite is used only in fast unit tests, not as an app runtime database.

## 4. Request Flows

### 4.1 Availability + book

1. User: “Can I book the meeting room today from 2 PM to 3 PM?”
2. Agent extracts `{date: today, start: 14:00, end: 15:00}`
3. `check_availability` → free → agent asks to confirm (or books if intent is clear)
4. `create_booking` → persist → confirmation via Brevo (± Teams)

### 4.2 Conflict + alternatives

1. Slot taken → `create_booking` returns conflict
2. Agent calls `suggest_alternatives` (same duration, same day)
3. Reply lists conflicting window and free alternatives

### 4.3 Vacate reminder

1. Scheduler finds booking ending in 15 minutes with a back-to-back next booking
2. Notification service sends vacate email via Brevo (± Teams message)
3. `reminder_sent_at` updated

### 4.4 Extend

1. User: “Extend my meeting by 30 minutes”
2. Resolve “my meeting” via associate + current/next booking
3. `extend_booking` checks free window
4. Approve and update `end_at`, or reject with conflicting booker info

### 4.5 Cancel + waitlist

1. `cancel_booking` → status `cancelled`
2. Find matching waitlist entries → notify → clear/update waitlist

## 5. Project Layout

```
MeetingRoomBookingAssitant/
├── problem.md
├── architecture.md
├── README.md
├── docker-compose.yml           # Postgres (required for shared/dev DB)
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry
│   │   ├── config.py
│   │   ├── api/                 # routers
│   │   ├── agent/               # LangGraph graph, tools, prompts
│   │   ├── services/            # booking, availability, insights
│   │   ├── models/              # SQLAlchemy / Pydantic
│   │   ├── notifications/       # Brevo email, Teams webhook/Graph
│   │   └── scheduler/           # vacate reminders
│   ├── requirements.txt
│   └── tests/
├── frontend/
│   └── app.py                   # Streamlit
└── .env.example
```

### 5.1 Runtime: Docker for the database

- **Postgres runs in Docker** via `docker compose up -d db` (see `docker-compose.yml`). Start this before the API.
- Backend and Streamlit run on the host for local development; full app containerization can be added later if needed.
- Default compose credentials: user/password `odc`, database `meeting_room`, port `5432`.
- Required `DATABASE_URL`: `postgresql+psycopg://odc:odc@localhost:5432/meeting_room`
- On API startup, `init_db()` creates tables and seeds the single ODC common room.
- In-memory SQLite appears only in unit tests (`tests/test_models.py`); integration tests hit Docker Postgres (`tests/test_postgres_init.py`).

## 6. Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Docker Postgres URL (required for running the app) |
| `OPENAI_API_KEY` / Azure OpenAI settings | LLM access |
| `ODC_TIMEZONE` | e.g. `America/Montevideo` |
| `BREVO_API_KEY` | Brevo transactional email API key |
| `BREVO_SENDER_EMAIL` / `BREVO_SENDER_NAME` | Verified sender identity |
| `BREVO_TEMPLATE_*` | **Required** numeric template IDs per event (create via `infra/scripts/create_brevo_templates.py`) |
| `TEAMS_WEBHOOK_URL` | Optional Incoming Webhook (channel posts) |
| `TEAMS_GRAPH_*` | Optional Graph creds for 1:1 associate messages |
| `REMINDER_LEAD_MINUTES` | Default `15` |

## 7. Non-Functional Considerations

| Concern | Approach |
|---|---|
| Concurrency | Transactional booking + DB exclusion / row lock |
| Observability | Structured logs for tool calls, booking outcomes, reminder sends |
| Security | Associate identity from session; no cross-user cancel/extend without ownership check |
| Testing | Unit tests for overlap/extend rules; agent tool mocks; API integration tests |
| Cost control | Prefer structured tool schemas; keep prompts short; cache availability for a request turn |

## 8. Implementation Phases

| Phase | Scope |
|---|---|
| **1 – Core booking** | Models, overlap logic, REST CRUD, Streamlit calendar |
| **2 – AI agent** | LangGraph + tools wired to booking service; chat UI |
| **3 – Notifications** | Brevo transactional email + optional Teams; 15-minute vacate scheduler |
| **4 – Waitlist & insights** | Cancellation waitlist notify; utilization dashboard |
| **5 – Hardening** | Postgres exclusion constraints (on Docker DB), tests, auth polish |

## 9. Out of Scope (initial)

- Multi-room support (schema allows `room_id`, product is single room)
- Recurring bookings
- Full Microsoft Graph calendar sync (Teams is notification-only, separate from Brevo)
- Using Zapier/Make as the Brevo→Teams bridge for booking events
- Mobile-native clients

## 10. AWS Free Tier Deployment

For a fully cloud Free Tier deploy (Streamlit on EC2, API on Lambda, Postgres on RDS, reminders on EventBridge), see [`architecture-aws.md`](architecture-aws.md).
