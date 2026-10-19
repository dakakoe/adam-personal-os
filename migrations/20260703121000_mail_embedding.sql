-- migrate:up
-- Local semantic search over mail (Ollama expansion): e5-base vectors for
-- raw.gmail_message bodies, drained by the embedding_worker in its idle gaps.
-- FK on the BIGSERIAL id (account_email+message_id is only UNIQUE on raw).
CREATE TABLE memory.mail_embedding (
    gmail_message_id BIGINT PRIMARY KEY
        REFERENCES raw.gmail_message(id) ON DELETE CASCADE,
    embedding        vector(768) NOT NULL,
    embedding_model  TEXT NOT NULL DEFAULT 'intfloat/multilingual-e5-base',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memory_mail_embedding_hnsw
    ON memory.mail_embedding USING hnsw (embedding vector_cosine_ops);

-- migrate:down
DROP TABLE IF EXISTS memory.mail_embedding;
