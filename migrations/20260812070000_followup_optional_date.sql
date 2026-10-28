-- migrate:up

-- Make a follow-up's date optional: "I owe this person a conversation, no
-- particular day."
--
-- The date was required because a follow-up was conceived as a dated
-- commitment. In practice a good share of them are intentions without a day —
-- forcing a date makes you invent one, and an invented date then reads as
-- overdue and nags. A "someday" pile you work down when you have slack is the
-- honest shape.
--
-- Nothing to backfill: every existing row has a date and keeps it. This only
-- permits NULL going forward.
ALTER TABLE memory.followup ALTER COLUMN due_date DROP NOT NULL;

-- migrate:down

-- Dateless rows can't be represented once the column is NOT NULL again; park
-- them on the day the constraint is restored rather than deleting them.
UPDATE memory.followup SET due_date = CURRENT_DATE WHERE due_date IS NULL;

ALTER TABLE memory.followup ALTER COLUMN due_date SET NOT NULL;
