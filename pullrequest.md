## Summary
-

## Test plan
- [ ] `docker compose up -d db` and wait until healthy
- [ ] Backend boots against Docker Postgres (`uvicorn`); `GET /health` OK
- [ ] `cd backend && .venv/Scripts/python.exe -m pytest -q`

Closes #
