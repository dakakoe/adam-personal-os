-- migrate:up
-- Rspamd Bayes training ledger: which messages have been fed to /learnspam or
-- /learnham, so the hourly feeder never re-learns. Separate from mail_spam
-- because (a) upsert_mail_spam overwrites rows on rescan and (b) the spam
-- corpus is listed LIVE from Gmail's spam folder (in:spam) — those message_ids
-- are never ingested into raw.gmail_message (archive-only excludes SPAM).
CREATE TABLE memory.mail_bayes_learn (
    account_email TEXT NOT NULL,
    message_id    TEXT NOT NULL,
    learned_as    TEXT NOT NULL CHECK (learned_as IN ('spam', 'ham')),
    learned_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_email, message_id)
);
CREATE INDEX mail_bayes_learn_as_idx ON memory.mail_bayes_learn (learned_as);

-- migrate:down
DROP TABLE IF EXISTS memory.mail_bayes_learn;
