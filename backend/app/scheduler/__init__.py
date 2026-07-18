"""Background scheduling for operational meeting-room tasks."""

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings
from app.scheduler.vacate_reminders import run_vacate_reminder_job


def create_scheduler(settings: Settings) -> BackgroundScheduler:
    """Create (but do not start) the application's in-process scheduler."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_vacate_reminder_job,
        trigger="interval",
        seconds=settings.reminder_poll_interval_seconds,
        id="vacate-reminders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
