-- migrate:up

-- Which zERC20 alerts have already gone out, so a 15-minute checker does not
-- email the same transaction (or the same busy hour) on every pass. Keyed by
-- the thing being alerted on, not by time, because the check window overlaps.
CREATE TABLE zerc20.alert_sent (
  kind     TEXT        NOT NULL,   -- 'large_tx' | 'burst'
  key      TEXT        NOT NULL,   -- large_tx: chain:tx_hash:log_index · burst: the clock hour
  sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  detail   JSONB,                  -- what was sent, for after-the-fact questions
  PRIMARY KEY (kind, key)
);
CREATE INDEX alert_sent_sent_at_idx ON zerc20.alert_sent (sent_at DESC);

-- v_operation gains block_time: the alert checker needs "in the last N minutes",
-- which a DATE cannot answer. Columns are appended, so dependent views are fine.
CREATE OR REPLACE VIEW zerc20.v_operation AS
SELECT
  t.chain,
  t.token,
  t.day,
  t.tx_hash,
  t.log_index,
  CASE
    WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
         AND tp.tx_hash IS NOT NULL                                      THEN 'Redeem'
    WHEN t.from_addr = '0x0000000000000000000000000000000000000000'      THEN 'Wrap'
    ELSE                                                                      'Unwrap'
  END AS tx_type,
  CASE WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
       THEN t.to_addr ELSE t.from_addr END AS wallet,
  t.amount,
  t.amount * p.usd AS usd_value,
  (x.address IS NULL) AS is_organic,
  t.block_time
FROM zerc20.transfer t
LEFT JOIN zerc20.teleport_tx tp
       ON tp.chain = t.chain AND tp.tx_hash = t.tx_hash
LEFT JOIN zerc20.token_price p ON p.token = t.token
LEFT JOIN zerc20.excluded_wallet x
       ON x.address = CASE WHEN t.from_addr = '0x0000000000000000000000000000000000000000'
                           THEN t.to_addr ELSE t.from_addr END
WHERE t.from_addr = '0x0000000000000000000000000000000000000000'
   OR t.to_addr   = '0x0000000000000000000000000000000000000000';

-- migrate:down
DROP TABLE IF EXISTS zerc20.alert_sent;
