# SCHEMA.md

**Purpose:** Database schema for the personal contact-memory system. This document is the design intent; the SQL files in `migrations/` are its executable form. If the two disagree, the migrations win — fix this doc.

**Database:** `memory` on the droplet's Postgres 16 + pgvector.

---

## 1. Layered architecture

Four Postgres schemas, in dependency order:

```
raw  ───►  canonical  ───►  memory
                              ▲
            queue  ───────────┘
```

| Schema | Purpose | Owner | Mutability |
|---|---|---|---|
| `raw` | Verbatim ingest from each source. One table per source-shape. | Fetchers (Telethon, Gmail, …) write only. | **Append-only.** Never UPDATE, never DELETE. |
| `canonical` | Deduplicated people + normalized interactions. | Normalizer process derives from `raw`. | Mutable, but only via the normalizer. |
| `memory` | AI artifacts: per-person profile, atomic facts, embeddings. | Memory builder + MCP server. | Mutable. Regeneratable from `canonical`. |
| `queue` | Work queues (Whisper transcription, embedding backfill). | Workers consume; producers append. | Mutable state machines. |

The cardinal rule: **`raw` is the source of truth.** If a normalizer bug corrupts `canonical`, we truncate canonical and replay from raw. If we change the embedding model, we wipe `memory.*_embedding` and rebuild from canonical.

---

## 2. Identity resolution

A single human shows up under many identifiers — Telegram user_id, phone number, eventually Gmail addresses, LinkedIn URLs. `canonical.person` is the one row per real human; `canonical.identity` is the many-to-one mapping from `(source, source_id)` to a person.

**v1 strategy: manual-merge with auto-create per source.**

- The normalizer sees a new `(source='telegram', source_id='12345')` it hasn't tracked yet.
- It **always** creates a fresh `canonical.person` and an `canonical.identity` row pointing to it.
- This produces duplicates (the same human under multiple `person.id`s across sources).
- Duplicates are resolved by hand via the `canonical.person.merged_into` column: set `merged_into` on the loser to the winner's id. All queries `COALESCE(merged_into, id)` to canonicalize.
- No automatic phone/name matching. Risk of bad auto-merges outweighs the convenience for a personal corpus.

Later we may add an LLM-assisted merge tool (read merge candidates, decide, write). The schema doesn't need to change for that — only the tooling around `merged_into`.

**Hard merge** (collapsing the loser's rows to the winner) is deferred. v1 keeps both rows; reads canonicalize via the `merged_into` chain.

---

## 3. Schemas, in detail

### 3.1 `raw`

One table per source-shape. Telegram is the only source in v1. Adding Gmail later means a new `raw.gmail_message` with whatever shape Gmail's API gives us — no need to retrofit a "common" raw shape.

**`raw.telegram_message`**

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | Internal surrogate |
| `chat_id` | `BIGINT NOT NULL` | Telegram chat id |
| `source_message_id` | `BIGINT NOT NULL` | Telegram message id (unique within chat) |
| `sender_id` | `BIGINT` | Telegram user id; NULL for channel posts |
| `message_date` | `TIMESTAMPTZ NOT NULL` | What Telegram says |
| `ingested_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | When **we** saw it |
| `kind` | `TEXT NOT NULL` | `'text'`, `'voice'`, `'photo'`, `'document'`, … |
| `text` | `TEXT` | Convenience: extracted body, NULL for voice-only |
| `voice_file_path` | `TEXT` | Local path under `/srv/memory/data/voice/`, NULL if not voice |
| `payload` | `JSONB NOT NULL` | The full Telethon message object, verbatim |

Unique: `(chat_id, source_message_id)`. Idempotent re-ingest.
Indexes: `(sender_id, message_date DESC)`, `(ingested_at)`.

**`raw.telegram_user`** and **`raw.telegram_chat`**: directory tables, one row per Telegram user / chat we've ever seen. `payload JSONB` holds the most recent observation; `first_seen` / `last_seen` track lifetime. Upserted by the fetcher.

### 3.2 `canonical`

**`canonical.person`**

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK DEFAULT gen_random_uuid()` | |
| `display_name` | `TEXT NOT NULL` | Best guess; manual override allowed |
| `notes` | `TEXT` | Free-form human notes |
| `merged_into` | `UUID REFERENCES canonical.person(id)` | NULL unless this row has been merged away |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Trigger-maintained |

**`canonical.identity`**

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `person_id` | `UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE` | |
| `source` | `TEXT NOT NULL` | `'telegram'`, `'phone'`, `'email'`, `'gmail'`, … |
| `source_id` | `TEXT NOT NULL` | The source's stable identifier (cast to text) |
| `evidence` | `JSONB` | Why we believe this is the person (raw user object snapshot, etc.) |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Unique: `(source, source_id)`. A given Telegram user_id maps to exactly one person.

**`canonical.interaction`**

The normalized event log. Whether it's a Telegram text, a transcribed voice note, or a future Gmail thread — each interaction is one row here.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK DEFAULT gen_random_uuid()` | |
| `person_id` | `UUID REFERENCES canonical.person(id) ON DELETE SET NULL` | Counterparty; NULL if unknown |
| `channel` | `TEXT NOT NULL` | `'telegram_text'`, `'telegram_voice'`, `'gmail'`, … |
| `direction` | `TEXT NOT NULL CHECK (direction IN ('inbound','outbound'))` | |
| `occurred_at` | `TIMESTAMPTZ NOT NULL` | Source's claimed timestamp |
| `body` | `TEXT` | Normalized text content (transcript for voice) |
| `raw_source` | `TEXT NOT NULL` | e.g. `'raw.telegram_message'` |
| `raw_id` | `BIGINT NOT NULL` | PK in the raw table |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Unique: `(raw_source, raw_id)`. One canonical row per raw row.
Indexes: `(person_id, occurred_at DESC)`, `(occurred_at DESC)`, `(channel)`.

### 3.3 `memory`

**`memory.profile`** — one row per `canonical.person`, AI-generated overview.

| Column | Type | Notes |
|---|---|---|
| `person_id` | `UUID PK REFERENCES canonical.person(id) ON DELETE CASCADE` | |
| `summary` | `TEXT` | Short paragraph: who is this, how do we know them |
| `embedding` | `vector(1536)` | Embedding of `summary` (OpenAI text-embedding-3-small) |
| `embedding_model` | `TEXT DEFAULT 'text-embedding-3-small'` | For future migration |
| `source_interaction_count` | `INT NOT NULL DEFAULT 0` | How many interactions went into this profile |
| `last_built_at` | `TIMESTAMPTZ` | When the memory builder last refreshed it |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

**`memory.fact`** — atomic, time-bound facts.

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK DEFAULT gen_random_uuid()` | |
| `person_id` | `UUID NOT NULL REFERENCES canonical.person(id) ON DELETE CASCADE` | |
| `fact` | `TEXT NOT NULL` | "Works at Acme as VP Eng" |
| `source_interaction_id` | `UUID REFERENCES canonical.interaction(id) ON DELETE SET NULL` | What gave us this |
| `confidence` | `REAL NOT NULL DEFAULT 1.0` | 0..1 |
| `valid_from` | `TIMESTAMPTZ` | NULL = unknown / always |
| `valid_until` | `TIMESTAMPTZ` | NULL = still true |
| `embedding` | `vector(1536)` | |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

**`memory.interaction_embedding`** — per-interaction embedding for semantic search.

| Column | Type | Notes |
|---|---|---|
| `interaction_id` | `UUID PK REFERENCES canonical.interaction(id) ON DELETE CASCADE` | |
| `embedding` | `vector(1536) NOT NULL` | |
| `embedding_model` | `TEXT NOT NULL DEFAULT 'text-embedding-3-small'` | |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |

Split from `canonical.interaction` so the embedding column doesn't bloat the hot path of interaction reads. Vector index here only.

**`memory.source_status`** — per-data-source health flag for the Sources page.

| Column | Type | Notes |
|---|---|---|
| `source_key` | `TEXT PK` | `'telegram'` \| `'granola'` (matches the SOURCES keys in `merge_api`) |
| `status` | `TEXT NOT NULL DEFAULT 'ok'` | `'ok'` \| `'needs_attention'` (free-form, like `raw.gmail_account.status`) |
| `reason` | `TEXT` | short human-readable reason, nullable |
| `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | writer-stamped on upsert |

Generic per-source attention flag for sources with no status column of their own. The Telegram worker writes it directly (it has a DB pool); the DB-less Granola worker reports via `POST /api/sources/granola/status`. `GET /api/sources` reads it into each source's `needs_reconnect` + `reconnect_reason`. (Gmail/GCal use `raw.gmail_account.status` instead.)

**Vector indexes:** HNSW with cosine distance on each `embedding` column. HNSW requires pgvector ≥ 0.5.0 (our `pgvector/pgvector:pg16` image satisfies this).

### 3.4 `queue`

Lightweight work queues. Pattern: `SELECT … FOR UPDATE SKIP LOCKED` is the consumer.

**`queue.whisper_job`**

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PK DEFAULT gen_random_uuid()` | |
| `raw_message_id` | `BIGINT NOT NULL` | `raw.telegram_message.id` |
| `voice_file_path` | `TEXT NOT NULL` | Local path |
| `status` | `TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','done','failed'))` | |
| `attempts` | `INT NOT NULL DEFAULT 0` | |
| `error` | `TEXT` | Last error if failed |
| `transcript` | `TEXT` | Result, on success |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `started_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

Unique: `(raw_message_id)`. No duplicate work.
Index: `(status, created_at)` for the worker poll.

**`queue.embedding_job`** — same shape, parameterized by `(entity_type, entity_id)`. Used to backfill embeddings after schema changes or new interactions.

---

## 4. Conventions

- **All timestamps are `TIMESTAMPTZ`.** Postgres stores UTC; clients convert on display.
- **All surrogate keys in `canonical` and `memory` are UUIDs.** `raw` uses BIGSERIAL because it's never exposed.
- **`updated_at` is trigger-maintained.** One shared trigger function `_touch_updated_at()` in the public schema.
- **No `ON DELETE CASCADE` from `raw`.** If you need to delete from raw, you've already decided to nuke the dependent rows.
- **Embedding dimensions are hardcoded to 1536** (OpenAI text-embedding-3-small). To switch models, add a new column (`embedding_v2 vector(N)`), backfill, then drop the old column in a follow-up migration. The `embedding_model` column records what we used.
- **No row-level security, no roles beyond `memory`.** Single-tenant, single-user system.

---

## 5. Flow examples

**Inbound Telegram text from a known contact:**

1. Telethon fetcher writes `raw.telegram_message` (kind=`'text'`).
2. Normalizer sees the new raw row. Looks up `canonical.identity` by `(source='telegram', source_id=sender_id)`. Found → has `person_id`.
3. Normalizer inserts into `canonical.interaction` with that `person_id`, `channel='telegram_text'`, `direction='inbound'`, `body=text`.
4. Embedding worker picks up the new interaction (via `queue.embedding_job` or a tail-the-table loop) and writes to `memory.interaction_embedding`.
5. Memory builder, periodically: refreshes `memory.profile` for that person if N new interactions since `last_built_at`.

**Inbound Telegram voice from an unknown contact:**

1. Telethon writes `raw.telegram_message` (kind=`'voice'`, `voice_file_path` set) and enqueues `queue.whisper_job`.
2. Normalizer creates a new `canonical.person` ("Unknown — Telegram 12345") and a `canonical.identity` row. Writes a `canonical.interaction` with `body = NULL` (transcript not ready) and `channel='telegram_voice'`.
3. Whisper worker transcribes, updates `queue.whisper_job.transcript`, then patches `canonical.interaction.body`.
4. Embedding flow proceeds as above once `body` is populated.

**Manual merge:**

1. You notice `Bob (Telegram 12345)` and `Bob (Gmail bob@example.com)` are the same human.
2. `UPDATE canonical.person SET merged_into = '<bob-gmail-id>' WHERE id = '<bob-telegram-id>';`
3. Application reads always `COALESCE(merged_into, id)` to resolve.
4. `memory.profile` for the merged-away person is left in place; the MCP server reads only the winner's profile.

---

## 6. What's deliberately NOT in v1

- **Hard-merge / row consolidation.** Soft-merge via `merged_into` only.
- **Multi-model embeddings in the same table.** One model at a time.
- **Cross-source dedup heuristics.** Manual merge only.
- **Soft delete on raw.** Raw is append-only; nothing to soft-delete.
- **Audit log.** `created_at` / `updated_at` are enough for a single-user system.

When any of these matter, they get their own migration and an update to this doc.
