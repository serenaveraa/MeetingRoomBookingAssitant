# AWS infrastructure — Meeting Room Booking Assistant

Cloud environments use **Amazon RDS PostgreSQL** as the only database. Local development continues to use Docker Postgres via [`docker-compose.yml`](../docker-compose.yml); Docker Postgres is **not** part of the AWS deployment path.

## Resources (issue #43)

| Setting | Value |
|---|---|
| Region | `sa-east-1` (default; override in `config.env`) |
| Instance identifier | `odc-mrba-postgres` |
| Engine | PostgreSQL 16 (matches `postgres:16-alpine`) |
| Instance class | `db.t3.micro` (Free Tier) |
| Storage | 20 GB `gp3`, single-AZ |
| Public access | Yes (required for Lambda demo path) |
| Secret | `odc-mrba/DATABASE_URL` in Secrets Manager |
| Security group | `odc-mrba-rds-sg` |

Downstream issues (backend deploy, Lambda vacate-reminder scheduler) should target the same region, instance identifier, and secret name unless you intentionally migrate.

## Prerequisites

- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws sts get-caller-identity`)
- `openssl`, `curl`, `python3`, `psql` (PostgreSQL client)
- IAM permissions for RDS, EC2 (VPC/SG), and Secrets Manager in the chosen region

## First-time setup

```bash
cp infra/config.env.example infra/config.env
# Edit infra/config.env — set ADMIN_CIDR and LAMBDA_SECURITY_GROUP_ID when known
chmod +x infra/*.sh
./infra/provision-rds.sh
./infra/init-rds-db.sh
./infra/verify-rds.sh
```

`provision-rds.sh` is idempotent: re-running it reports existing resources instead of duplicating them.

### Configuration

Copy [`config.env.example`](config.env.example) to `config.env` (gitignored). Important variables:

- **`AWS_REGION`** — defaults to `sa-east-1` (ODC timezone). Must match where Lambda/EC2 will run.
- **`ADMIN_CIDR`** — your public IP as `/32` for `psql` and migrations. Auto-detected from `checkip.amazonaws.com` if unset.
- **`LAMBDA_SECURITY_GROUP_ID`** — when the vacate-reminder Lambda exists in a VPC, set this so RDS allows **SG-to-SG** ingress on port 5432. If unset, only the admin CIDR can reach Postgres until Lambda is wired.

### Lambda access tradeoff

| Approach | When to use | Tradeoff |
|---|---|---|
| **SG-to-SG** (preferred) | Lambda runs inside a VPC with a known security group | Tightest scope; no dependency on changing egress IPs |
| **Admin CIDR only** (initial bootstrap) | Lambda not provisioned yet | Safe for provisioning/migrations; Lambda cannot connect until its SG is added |
| **IP allowlist for non-VPC Lambda** | Lambda outside a VPC | Fragile — AWS egress IPs change; consider RDS Proxy or VPC-attached Lambda later |

This repo does not yet define Lambda resources. `provision-rds.sh` accepts `LAMBDA_SECURITY_GROUP_ID` when available; re-run after setting it to add the SG ingress rule without recreating RDS.

## Connection string

The secret stores JSON:

```json
{"DATABASE_URL": "postgresql+psycopg://USER:PASSWORD@HOST:5432/meeting_room?sslmode=require"}
```

Fetch at runtime (never commit the value):

```bash
aws secretsmanager get-secret-value \
  --secret-id odc-mrba/DATABASE_URL \
  --region sa-east-1 \
  --query SecretString --output text
```

The backend reads `DATABASE_URL` from the environment ([`backend/app/config.py`](../backend/app/config.py)). SQLAlchemy passes query parameters (including `sslmode=require`) through to psycopg3 unchanged — no app override.

## Scripts

| Script | Purpose |
|---|---|
| [`provision-rds.sh`](provision-rds.sh) | Create/reuse SG, subnet group, RDS instance, Secrets Manager secret |
| [`init-rds-db.sh`](init-rds-db.sh) | Run `init_db()` — tables, `btree_gist`, overlap constraint, ODC room seed |
| [`verify-rds.sh`](verify-rds.sh) | Confirm `available` status, SSL via `\conninfo`, list SG rules |
| [`rotate-rds-password.sh`](rotate-rds-password.sh) | Rotate master password and update the secret |

## Password rotation

```bash
./infra/rotate-rds-password.sh
```

This generates a new password, applies it with `modify-db-instance`, and updates `odc-mrba/DATABASE_URL`. Restart compute (API, Lambda) that reads the secret afterward.

## Re-provisioning

Safe to re-run `./infra/provision-rds.sh` — it skips creation when the instance identifier already exists.

To tear down (manual, destructive):

```bash
aws rds delete-db-instance \
  --db-instance-identifier odc-mrba-postgres \
  --skip-final-snapshot \
  --region sa-east-1
aws secretsmanager delete-secret \
  --secret-id odc-mrba/DATABASE_URL \
  --force-delete-without-recovery \
  --region sa-east-1
# Remove SG and subnet group via console/CLI if no longer needed
```

## Verification checklist

- [ ] RDS console shows `odc-mrba-postgres` as **available**, Postgres 16, `db.t3.micro`, single-AZ, publicly accessible
- [ ] SG inbound: TCP 5432 from admin `/32` (+ Lambda SG if set) — **not** `0.0.0.0/0`
- [ ] `./infra/verify-rds.sh` — `\conninfo` shows SSL
- [ ] `./infra/init-rds-db.sh` — `SELECT id, name FROM rooms;` returns ODC Common Meeting Room
- [ ] Connection from a non-allowed IP fails

## Future hardening (out of scope)

- RDS Proxy for connection pooling and IAM auth
- Multi-AZ / read replicas
- VPC-only RDS with no public accessibility (requires NAT for admin access)
