-- migrate:up

-- Mail auto-triage Phase C: content classification (newsletter | transactional |
-- personal) from the SetFit model in the mail_ml package. The model is trained on
-- weak-supervision labels (Gmail categories + keyword rules) and predicts a class +
-- confidence per message; the batch classify worker upserts verdicts here. merge_api
-- reads this table (it never loads torch) to surface a content badge/filter in /mail.
CREATE TABLE IF NOT EXISTS memory.mail_class (
  account_email TEXT NOT NULL,
  message_id    TEXT NOT NULL,      -- raw.gmail_message.message_id
  content_class TEXT NOT NULL,      -- newsletter | transactional | personal
  confidence    REAL,               -- model probability for the chosen class [0,1]
  model_version TEXT,               -- artifact tag, so a retrain can re-classify
  classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (account_email, message_id)
);
CREATE INDEX IF NOT EXISTS mail_class_class_idx ON memory.mail_class (account_email, content_class);

-- migrate:down

DROP TABLE IF EXISTS memory.mail_class;
