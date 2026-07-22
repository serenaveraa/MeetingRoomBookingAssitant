-- Existing installations need the room associated with each waitlist request.
ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS room_id INTEGER;

UPDATE waitlist_entries
SET room_id = (SELECT id FROM rooms ORDER BY id LIMIT 1)
WHERE room_id IS NULL;

ALTER TABLE waitlist_entries ALTER COLUMN room_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_waitlist_entries_room'
    ) THEN
        ALTER TABLE waitlist_entries
            ADD CONSTRAINT fk_waitlist_entries_room
            FOREIGN KEY (room_id) REFERENCES rooms(id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_waitlist_active_window
    ON waitlist_entries (room_id, desired_start, desired_end)
    WHERE notified_at IS NULL;