-- PostgreSQL-only production guarantee for confirmed room bookings.
-- SQLite skips this migration and keeps the application-level overlap check.
CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ex_bookings_confirmed_room_time'
    ) THEN
        ALTER TABLE bookings
        ADD CONSTRAINT ex_bookings_confirmed_room_time
        EXCLUDE USING gist (
            room_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (status = 'confirmed');
    END IF;
END $$;