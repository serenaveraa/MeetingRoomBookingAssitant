#!/usr/bin/env python3
"""Apply backend SQL migrations to the provisioned RDS instance.

Prefer DATABASE_URL from the environment, then infra/local.env
(written by infra/scripts/deploy.sh after CloudFormation deploy).

Usage:
  # after full stack deploy
  set -a && source infra/local.env && set +a
  python infra/migrate_rds.py

  # or
  DATABASE_URL='postgresql+psycopg://...' python infra/migrate_rds.py

Note: tables are created by the API Lambda lifespan (init_db) on first
invoke. Hit GET /health once before running this script if rooms is missing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = Path(__file__).resolve().parent / "local.env"
MIGRATIONS = [
    ROOT / "backend/migrations/001_add_vacate_reminder_claim.sql",
    ROOT / "backend/migrations/002_add_waitlist_room.sql",
    ROOT / "backend/migrations/003_add_booking_overlap_exclusion.sql",
]


def _load_local_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not LOCAL_ENV.exists():
        return env
    for line in LOCAL_ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        local = _load_local_env()
        url = local.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "DATABASE_URL missing. Deploy the stack (infra/scripts/deploy.sh) "
            f"or set DATABASE_URL / create {LOCAL_ENV}."
        )
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url.removeprefix("postgresql+psycopg://")
    return url


def main() -> int:
    import psycopg

    url = _database_url()
    local = _load_local_env()
    endpoint = local.get("DB_ENDPOINT") or os.environ.get("DB_ENDPOINT", "?")
    print(f"Connecting to {endpoint} …")
    with psycopg.connect(url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        conn.commit()
        # Ensure base tables exist (created by Lambda init_db on first /health)
        tables = conn.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'rooms'
            """
        ).fetchone()
        if not tables or tables[0] == 0:
            raise SystemExit(
                "Table 'rooms' not found. Invoke the API once first:\n"
                "  curl \"$API_URL/health\"\n"
                "Then re-run this script."
            )
        for path in MIGRATIONS:
            sql = path.read_text()
            print(f"Applying {path.name} …")
            conn.execute(sql)
            conn.commit()
        row = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()
        if row and row[0] == 0:
            conn.execute(
                "INSERT INTO rooms (name) VALUES (%s)",
                ("ODC Common Meeting Room",),
            )
            conn.commit()
            print("Seeded ODC Common Meeting Room")
        else:
            print(f"rooms already present (count={row[0] if row else '?'})")
    print("Migrations complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
