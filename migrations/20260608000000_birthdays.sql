-- migrate:up

-- Manual/override birthday for a person. The normalizer only ever writes
-- display_name + merged_into on canonical.person, so this hand-entered value is
-- safe from canonical replay. Year may be a 1900 sentinel when only MM-DD known.
ALTER TABLE canonical.person ADD COLUMN IF NOT EXISTS birthday date;

-- Telegram-sourced birthday (UserFull.birthday, 2024+). Captured by the
-- enrich-bios full-user pass; only visible when the contact set one and their
-- privacy allows us to see it. Year 1900 = year withheld.
ALTER TABLE raw.telegram_user ADD COLUMN IF NOT EXISTS birthday date;

-- migrate:down

ALTER TABLE raw.telegram_user DROP COLUMN IF EXISTS birthday;
ALTER TABLE canonical.person DROP COLUMN IF EXISTS birthday;
