## Summary

- Add idempotent AWS CLI scripts under `infra/` to provision a Free Tier RDS PostgreSQL 16 instance (`db.t3.micro`, single-AZ, publicly accessible) in `sa-east-1`
- Create a dedicated security group with TCP 5432 scoped to admin `/32` and optional Lambda SG-to-SG ingress (no `0.0.0.0/0`)
- Store `DATABASE_URL` with `sslmode=require` in Secrets Manager (`odc-mrba/DATABASE_URL`); credentials are generated at create time and never committed
- Document cloud vs local paths in `infra/README.md` and root `README.md`

### Lambda access tradeoff

No Lambda resources exist in this repo yet. Initial provisioning allows **admin IP only** on port 5432. When the vacate-reminder Lambda (or VPC-attached agent) is added, set `LAMBDA_SECURITY_GROUP_ID` in `infra/config.env` and re-run `provision-rds.sh` to add SG-to-SG ingress — preferred over IP allowlists because Lambda egress IPs are not stable outside a VPC.

If Lambda remains **non-VPC-attached**, reviewers should weigh RDS Proxy, IAM DB auth, or a temporary IP allowlist as follow-up hardening (called out in `infra/README.md`).

## Test plan

- [ ] Copy `infra/config.env.example` → `infra/config.env`; set `ADMIN_CIDR`
- [ ] `./infra/provision-rds.sh` — RDS `available`, Postgres 16, `db.t3.micro`, single-AZ, public
- [ ] `./infra/init-rds-db.sh` — `init_db()` creates tables and seeds ODC room
- [ ] `./infra/verify-rds.sh` — `\conninfo` shows SSL; SG rules match admin (+ Lambda SG if set)
- [ ] Confirm connection from a non-allowed IP is rejected
- [ ] `cd backend && python -m pytest -q tests/test_db_config.py`
- [ ] Local path unchanged: `docker compose up -d db` + full pytest suite

Closes #43
