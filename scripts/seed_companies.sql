-- Company seed/enrichment (idempotent, re-runnable). Builds person↔company
-- links from LLM profiles + LinkedIn (URL-vanity + email joins). Focused:
-- only creates companies with >=2 distinct linked people, de-noised. Use the
-- /companies merge action to fold remaining name dupes (e.g. ACME/Acme).
--   psql: \i scripts/seed_companies.sql   (or pipe via docker exec)

DROP TABLE IF EXISTS pc;
CREATE TEMP TABLE pc AS
SELECT s.person_id, lower(btrim(s.company)) AS key, btrim(s.company) AS company, s.role
FROM (
  SELECT p.person_id, btrim(p.structured->>'current_company') AS company, p.structured->>'current_role' AS role
    FROM memory.profile p WHERE nullif(btrim(p.structured->>'current_company'),'') IS NOT NULL
  UNION ALL
  SELECT i.person_id, btrim(lc.company), btrim(lc.position)
    FROM raw.linkedin_connection lc
    JOIN canonical.identity i ON i.source='linkedin'
         AND lower(split_part(rtrim(lc.url,'/'),'/in/',2)) = lower(i.source_id)
   WHERE nullif(btrim(lc.company),'') IS NOT NULL AND nullif(lc.url,'') IS NOT NULL
  UNION ALL
  SELECT i.person_id, btrim(lc.company), btrim(lc.position)
    FROM raw.linkedin_connection lc
    JOIN canonical.identity i ON i.source='email' AND lower(i.source_id)=lower(btrim(lc.email))
   WHERE nullif(btrim(lc.company),'') IS NOT NULL AND nullif(btrim(lc.email),'') IS NOT NULL
) s
JOIN canonical.person cp ON cp.id=s.person_id AND cp.merged_into IS NULL AND cp.deleted_at IS NULL
WHERE lower(s.company) NOT IN (
  'self-employed','self employed','selfemployed','self employed.','freelance','freelancer',
  'stealth','stealth startup','stealth mode','stealth ventures','nda','n/a','na','none','none.',
  'retired','student','unemployed','various','independent','independent consultant','consultant',
  'consulting','-','.','tbd','crypto','web3','blockchain','private','—','various companies',
  'looking for opportunities','open to work','sabbatical','upwork','fiverr'
) AND length(s.company) >= 2;

INSERT INTO memory.company (name, norm_name)
SELECT display, key FROM (
  SELECT key, mode() WITHIN GROUP (ORDER BY company) AS display, count(DISTINCT person_id) AS ppl
  FROM pc GROUP BY key HAVING count(DISTINCT person_id) >= 2
) k
WHERE NOT EXISTS (SELECT 1 FROM memory.company c WHERE c.deleted_at IS NULL AND c.norm_name=k.key);

INSERT INTO memory.company_person (company_id, person_id, role, is_current)
SELECT DISTINCT ON (c.id, pc.person_id) c.id, pc.person_id, nullif(btrim(pc.role),''), true
FROM pc JOIN memory.company c ON c.norm_name = pc.key AND c.deleted_at IS NULL
ORDER BY c.id, pc.person_id, (pc.role IS NOT NULL) DESC
ON CONFLICT (company_id, person_id) DO NOTHING;

-- Opportunity free-text company → entity + company_id
INSERT INTO memory.company (name, norm_name)
SELECT DISTINCT ON (lower(btrim(company))) btrim(company), lower(btrim(company))
FROM memory.opportunity o
WHERE o.deleted_at IS NULL AND nullif(btrim(o.company),'') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM memory.company c WHERE c.deleted_at IS NULL AND c.norm_name=lower(btrim(o.company)));
UPDATE memory.opportunity o SET company_id=c.id
FROM memory.company c
WHERE o.deleted_at IS NULL AND o.company_id IS NULL AND nullif(btrim(o.company),'') IS NOT NULL
  AND c.deleted_at IS NULL AND c.norm_name=lower(btrim(o.company));
