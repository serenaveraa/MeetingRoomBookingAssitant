from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agent import router as agent_router
from app.api.bookings import router as bookings_router
from app.config import get_settings
from app.db import init_db
from app.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler = create_scheduler(get_settings())
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="ODC Meeting Room Booking Assistant",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(bookings_router)
app.include_router(agent_router)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "timezone": settings.odc_timezone,
    }
