-- Labeling-rule tuning feed (triage iteration): what the user corrected vs what
-- the model said, grouped by sender domain — the highest-count rows are the
-- systematic misses worth turning into rules in mail_ml/labeling.py.
--
-- Run: docker exec memory-postgres-1 psql -U memory -d memory -f - < scripts/mail_class_corrections.sql
-- (user rows are written by POST /api/mail/thread/class — the badge menu in /mail)

WITH corrections AS (
  SELECT c.account_email, c.message_id, c.content_class AS user_class,
         m.from_address,
         lower(split_part(m.from_address, '@', 2)) AS sender_domain
    FROM memory.mail_class c
    JOIN raw.gmail_message m
      ON m.account_email = c.account_email AND m.message_id = c.message_id
   WHERE c.model_version = 'user'
),
model_verdicts AS (
  -- what the model says about OTHER mail from the same senders (excludes the
  -- corrected rows themselves — those are now 'user')
  SELECT lower(split_part(m.from_address, '@', 2)) AS sender_domain,
         c.content_class AS model_class, count(*) AS n
    FROM memory.mail_class c
    JOIN raw.gmail_message m
      ON m.account_email = c.account_email AND m.message_id = c.message_id
   WHERE c.model_version <> 'user'
   GROUP BY 1, 2
)
SELECT co.sender_domain,
       co.user_class,
       count(*)                                   AS corrections,
       array_agg(DISTINCT co.from_address)        AS senders,
       COALESCE(jsonb_object_agg(mv.model_class, mv.n)
                FILTER (WHERE mv.model_class IS NOT NULL), '{}'::jsonb)
                                                  AS model_still_says
  FROM corrections co
  LEFT JOIN model_verdicts mv ON mv.sender_domain = co.sender_domain
 GROUP BY co.sender_domain, co.user_class
 ORDER BY count(*) DESC, co.sender_domain;
