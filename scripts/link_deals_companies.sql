-- Link the 15 opportunities to company entities (idempotent, re-runnable).
-- The deals name their counterparty company in the TITLE, not the (empty)
-- free-text `company` column, so the seed's opp-linker never caught them.
-- Hand-mapped from titles; keyed on a distinctive title fragment (titles are
-- unique here) and guarded by `company_id IS NULL` so re-runs are no-ops and
-- any manual re-pick via the opp picker is preserved.
--
-- Reversible: UPDATE memory.opportunity SET company_id=NULL WHERE ...
-- 6 deals are intentionally left unlinked (no concrete counterparty company):
--   prediction market · two initiatives · adult industry · japanese listing ·
--   singapore token · JBW sponsorship.

BEGIN;

-- 1) Create the referenced exchanges/firms that aren't entities yet.
--    (Binance + Bybit already exist from the company seed.)
INSERT INTO memory.company (name, norm_name)
SELECT v.name, v.norm_name FROM (VALUES
  ('TRON',         'tron'),
  ('Bithumb',      'bithumb'),
  ('Upbit',        'upbit'),
  ('Mids Capital', 'mids capital'),
  ('ByteDance',    'bytedance')
) v(name, norm_name)
WHERE NOT EXISTS (
  SELECT 1 FROM memory.company c WHERE c.deleted_at IS NULL AND c.norm_name = v.norm_name
);

-- 2) Link each deal to its primary company by title fragment.
--    helper shape: match a unique title fragment → company by norm_name.
UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'binance'      AND o.title ILIKE 'Binance TRON Energy%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'bybit'        AND o.title ILIKE 'Bybit TRON Energy%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'tron'         AND o.title ILIKE 'Tron exchange partnership%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'bithumb'      AND o.title ILIKE 'Korea Blockchain Week partnership%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'bithumb'      AND o.title ILIKE 'ADI Token listing on Bithumb%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'upbit'        AND o.title ILIKE 'KBW listing and trading competition on Upbit%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'mids capital' AND o.title ILIKE 'Mids Capital%market-neutral%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'mids capital' AND o.title ILIKE 'Mids Capital referral partnership%';

UPDATE memory.opportunity o SET company_id = c.id
  FROM memory.company c
 WHERE c.deleted_at IS NULL AND o.deleted_at IS NULL AND o.company_id IS NULL
   AND c.norm_name = 'bytedance'    AND o.title ILIKE 'Sell company to ByteDance%';

COMMIT;
