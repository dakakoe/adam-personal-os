-- migrate:up

-- Phase 4 v2: Google Calendar events, feeding the daily planner.
--
-- Synced by the gmail fetcher's `calendar-sync` command (folded in rather
-- than a separate fetcher — it reuses the same raw.gmail_account refresh
-- token, now re-consented with calendar.readonly). The daily_planner reads
-- the plan-day window so the plan accounts for actual meetings (time
-- blocks, prep, "don't overload around the 3pm call").
--
-- Raw layer only — events are read directly by the planner, no normalizer
-- pass into canonical.* (a meeting isn't a person/interaction).

CREATE TABLE raw.gcal_event (
  id                  BIGSERIAL PRIMARY KEY,
  -- Which authed account's calendar this came from (no FK: mirrors
  -- raw.gmail_message — imports could precede the account row).
  account_email       TEXT NOT NULL,
  calendar_id         TEXT NOT NULL DEFAULT 'primary',
  event_id            TEXT NOT NULL,         -- Google event id (singleEvents-expanded)
  summary             TEXT,
  description         TEXT,
  location            TEXT,
  status              TEXT,                  -- confirmed | tentative | cancelled
  -- For timed events: the real start/end (tz-aware). For all-day events:
  -- midnight UTC of the date, with all_day=true so the reader formats it
  -- as a date, not a time.
  start_ts            TIMESTAMPTZ,
  end_ts              TIMESTAMPTZ,
  all_day             BOOLEAN NOT NULL DEFAULT false,
  organizer_email     TEXT,
  -- [{email, displayName, responseStatus, self}] from the API.
  attendees           JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- 'accepted'|'declined'|'tentative'|'needsAction'|null — the account's
  -- own RSVP, lifted out so the planner can skip declined events.
  self_response       TEXT,
  html_link           TEXT,
  recurring_event_id  TEXT,                  -- set on instances of a series
  updated_ts          TIMESTAMPTZ,           -- Google's last-modified
  ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT raw_gcal_event_unique UNIQUE (account_email, calendar_id, event_id)
);

CREATE INDEX raw_gcal_event_start_idx ON raw.gcal_event (start_ts);
CREATE INDEX raw_gcal_event_account_start_idx
  ON raw.gcal_event (account_email, start_ts);


-- migrate:down

DROP TABLE IF EXISTS raw.gcal_event;
