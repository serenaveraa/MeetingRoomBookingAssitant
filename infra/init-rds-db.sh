#!/usr/bin/env bash
# Run backend init_db() against the cloud RDS instance (creates tables + ODC room seed).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

load_config
require_aws

if ! rds_instance_exists; then
  echo "error: RDS instance ${DB_INSTANCE_IDENTIFIER} not found" >&2
  exit 1
fi

STATUS="$(get_rds_status)"
if [[ "${STATUS}" != "available" ]]; then
  echo "error: RDS status is ${STATUS}; wait until available" >&2
  exit 1
fi

export DATABASE_URL
DATABASE_URL="$(fetch_database_url_from_secret)"

echo "running init_db() against ${DB_INSTANCE_IDENTIFIER}..."
(
  cd "${REPO_ROOT}/backend"
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null || true
  fi
  python3 - <<'PY'
from sqlalchemy import select, text

from app.config import get_settings
from app.db import get_engine, get_session_factory, init_db, reset_engine
from app.models import ODC_COMMON_ROOM_NAME, Room

get_settings.cache_clear()
reset_engine()
init_db()

engine = get_engine()
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))

with get_session_factory()() as session:
    rooms = session.scalars(select(Room)).all()
    print("rooms:")
    for room in rooms:
        print(f"  id={room.id} name={room.name!r}")
    assert any(r.name == ODC_COMMON_ROOM_NAME for r in rooms), "ODC room seed missing"
print("init_db() completed successfully.")
PY
)

unset DATABASE_URL

echo ""
echo "Verify with: ./infra/verify-rds.sh"
