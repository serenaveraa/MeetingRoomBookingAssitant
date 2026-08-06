# AWS Free Tier deploy (CloudFormation)

Fully cloud stack from [`architecture-aws.md`](../architecture-aws.md):

| Resource | Role |
|---|---|
| EC2 `t3.micro` | Streamlit UI (`:8501`) |
| Lambda (container) + HTTP API | FastAPI + LangGraph agent |
| Lambda + EventBridge `rate(5 minutes)` | Vacate reminders |
| RDS `db.t3.micro` Postgres | App database |
| ECR | Lambda image |

## Live links (current `odc-meeting` stack)

| What | Value |
|---|---|
| Region | `us-east-2` |
| Streamlit | http://18.222.179.17:8501 |
| API | https://pzjiegjp46.execute-api.us-east-2.amazonaws.com |
| Health | https://pzjiegjp46.execute-api.us-east-2.amazonaws.com/health |
| RDS endpoint | `odc-meeting-room.cfc04mss8inc.us-east-2.rds.amazonaws.com` |

After any stack replace, refresh from:

```bash
aws cloudformation describe-stacks --stack-name odc-meeting --region us-east-2 \
  --query 'Stacks[0].Outputs' --output table
# or: cat infra/local.env
```

Also mirrored at the top of [`README.md`](../README.md).

## Prerequisites

- AWS CLI + credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`)
- Docker (to build/push the Lambda image)
- An EC2 key pair in `us-east-2` (for SSH)
- Your public IP as `AllowedSshCidr` (e.g. `203.0.113.10/32`)

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-2
```

Console username/password cannot drive the CLI — use an IAM access key.

### Existing hand-provisioned RDS

If you previously ran [`provision_rds.py`](provision_rds.py) and already have
`odc-meeting-room` / `odc-meeting-subnet-group` / `odc-meeting-rds-sg`, **delete
those resources first** so CloudFormation can create them without name conflicts
(and so you are not billed for two micros).

`provision_rds.py` is **superseded** by this stack; keep it only as a reference.

## Parameters

```bash
cp infra/parameters.example.json infra/parameters.json
# edit KeyName, AllowedSshCidr, DbPassword, GroqApiKey, Brevo*, …
```

`infra/parameters.json` is gitignored when you put secrets there — or pass secrets
via env vars (`DB_PASSWORD`, `GROQ_API_KEY`, `KEY_NAME`, `ALLOWED_SSH_CIDR`, …).

`DbPassword` must be **alphanumeric only** (it is embedded in `DATABASE_URL`).

### Brevo transactional templates (required for email)

The Lambdas send mail via Brevo **template IDs**, not free-form HTML. If
`BrevoTemplateBookingConfirmed` (and the other four) are empty, bookings still
succeed but every email fails with `Missing Brevo template ID` in CloudWatch.

With `BrevoApiKey` + `BrevoSenderEmail` set in `parameters.json` (or `.env`):

```bash
python infra/scripts/create_brevo_templates.py
# → writes infra/.deploy/brevo_templates.json and prints IDs
```

Paste the IDs into `infra/parameters.json`:

| Parameter | Event |
|---|---|
| `BrevoTemplateBookingConfirmed` | `booking.confirmed` |
| `BrevoTemplateBookingExtended` | `booking.extended` |
| `BrevoTemplateBookingCancelled` | `booking.cancelled` |
| `BrevoTemplateVacateReminder` | `booking.vacate_reminder` |
| `BrevoTemplateWaitlistAvailable` | `waitlist.slot_available` |

Then redeploy so Lambda env vars pick them up:

```bash
./infra/scripts/deploy.sh
```

Templates use `{{ params.recipient_name }}`, `{{ params.room_name }}`,
`{{ params.start_at }}`, `{{ params.end_at }}`, `{{ params.purpose }}`, etc.
(see `backend/app/notifications/brevo.py`).

## Deploy (two-phase)

Lambdas need an image in ECR before they can be created:

```bash
# 1) ECR + RDS + security groups + IAM (no Lambdas/EC2 yet)
BootstrapMode=true ./infra/scripts/deploy.sh

# Wait until RDS is available (often 5–15 minutes on first create).

# 2) Build & push Lambda image (linux/amd64)
./infra/scripts/build_and_push.sh

# 3) Full stack: API Lambda, reminder Lambda, HTTP API, EventBridge, EC2
./infra/scripts/deploy.sh

# 4) Cold-start init_db (creates tables + seeds room)
set -a && source infra/local.env && set +a
curl -sS "$API_URL/health"

# 5) Apply SQL migrations (exclusion constraint, etc.)
cd backend && . .venv/bin/activate   # needs psycopg
python ../infra/migrate_rds.py
```

Outputs are written to:

- `infra/local.env` (gitignored) — `DATABASE_URL`, `API_URL`, `STREAMLIT_URL`, …
- `infra/outputs.json` (gitignored)

Open Streamlit at `STREAMLIT_URL` (e.g. `http://x.x.x.x:8501`).

### Updating code on an existing stack

```bash
# Backend / agent (Lambda)
./infra/scripts/build_and_push.sh
aws lambda update-function-code --function-name odc-meeting-api \
  --image-uri "$(grep ECR_URI infra/local.env | cut -d= -f2):latest" --region us-east-2
aws lambda update-function-code --function-name odc-meeting-reminder \
  --image-uri "$(grep ECR_URI infra/local.env | cut -d= -f2):latest" --region us-east-2

# Frontend (Streamlit on EC2) — the systemd unit pulls origin/<RepoBranch> on
# every start (ExecStartPre). After pushing to that branch:
#   ssh … 'sudo systemctl restart odc-streamlit.service'
# Also sets ARROW_DEFAULT_MEMORY_POOL=system to avoid a mimalloc segfault on rerun.
```

## Scripts (CloudFormation stack)

| Script | Purpose |
|---|---|
| [`scripts/deploy.sh`](scripts/deploy.sh) | `aws cloudformation deploy` + write `local.env` |
| [`scripts/build_and_push.sh`](scripts/build_and_push.sh) | Docker build → ECR |
| [`scripts/userdata.sh`](scripts/userdata.sh) | Reference EC2 bootstrap (embedded in the template) |
| [`scripts/create_brevo_templates.py`](scripts/create_brevo_templates.py) | Create/reuse Brevo transactional templates; print IDs |
| [`migrate_rds.py`](migrate_rds.py) | Run `backend/migrations/*.sql` against RDS |
| [`cloudformation/odc-stack.yaml`](cloudformation/odc-stack.yaml) | Stack template |

## Tear down

Delete EC2 + RDS before month 13 of Free Tier (or when the case study ends):

```bash
aws cloudformation delete-stack --stack-name odc-meeting --region us-east-2
aws cloudformation wait stack-delete-complete --stack-name odc-meeting --region us-east-2
# Optionally delete remaining ECR images/repo if the stack did not retain them
```

## Local vs AWS

| Mode | How |
|---|---|
| Local API | `uvicorn app.main:app` — `RUNNING_IN_LAMBDA` unset/false |
| AWS API Lambda | Handler `app.lambda_handlers.api_handler`, `RUNNING_IN_LAMBDA=true` |
| AWS reminder | Handler `app.lambda_handlers.reminder_handler`, EventBridge every 5 min |

---

# Alternative: shell RDS provisioner (issue #43)

Standalone scripts that create an RDS instance + Secrets Manager secret without the full CloudFormation stack. Prefer the CloudFormation path above for the live Free Tier demo (`us-east-2` / `odc-meeting`).

| Setting | Value |
|---|---|
| Region | `sa-east-1` (default; override in `config.env`) |
| Instance identifier | `odc-mrba-postgres` |
| Engine | PostgreSQL 16 |
| Instance class | `db.t3.micro` (Free Tier) |
| Secret | `odc-mrba/DATABASE_URL` in Secrets Manager |
| Security group | `odc-mrba-rds-sg` |

```bash
cp infra/config.env.example infra/config.env
# Edit ADMIN_CIDR / LAMBDA_SECURITY_GROUP_ID when known
chmod +x infra/*.sh
./infra/provision-rds.sh
./infra/init-rds-db.sh
./infra/verify-rds.sh
```

| Script | Purpose |
|---|---|
| [`provision-rds.sh`](provision-rds.sh) | Create/reuse SG, subnet group, RDS instance, Secrets Manager secret |
| [`init-rds-db.sh`](init-rds-db.sh) | Run `init_db()` — tables, `btree_gist`, overlap constraint, ODC room seed |
| [`verify-rds.sh`](verify-rds.sh) | Confirm `available` status, SSL via `\conninfo`, list SG rules |
| [`rotate-rds-password.sh`](rotate-rds-password.sh) | Rotate master password and update the secret |
