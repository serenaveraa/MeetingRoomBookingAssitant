from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(
    title="ODC Meeting Room Booking Assistant",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "timezone": settings.odc_timezone,
    }
