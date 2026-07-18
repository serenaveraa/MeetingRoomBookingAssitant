-- Existing installations created before the reminder scheduler need these
-- nullable columns. Fresh databases receive them via SQLAlchemy metadata.
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMPTZ NULL;
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_claimed_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_bookings_vacate_reminder_candidates
    ON bookings (status, end_at)
    WHERE reminder_sent_at IS NULL;
