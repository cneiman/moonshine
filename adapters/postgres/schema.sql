-- moonshine Postgres Schema
-- Mirrors the SQLite schema (core/schema.sql) for Postgres backends.
-- Run via: psql $MOONSHINE_PG_DSN -f adapters/postgres/schema.sql
--
-- Requires: Postgres 14+
-- Optional: pgvector extension for native vector similarity search

-- Schema namespace so moonshine tables don't pollute public
CREATE SCHEMA IF NOT EXISTS moonshine;

SET search_path TO moonshine, public;

-- ============ pgvector (optional) ============
-- If the pgvector extension is installed, vector columns are used for
-- embeddings. If not, embeddings fall back to bytea (stored as raw floats,
-- queried in Python). The adapter detects which mode is active at startup.
--
-- To enable: CREATE EXTENSION IF NOT EXISTS vector;

-- ============ Core Memories ============

CREATE TABLE IF NOT EXISTS memories (
    id          BIGSERIAL PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN (
                    'event', 'lesson', 'person', 'behavior', 'project',
                    'insight', 'decision', 'preference', 'skill'
                )),
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    tags        JSONB NOT NULL DEFAULT '[]',
    importance  INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    source      TEXT,
    source_date DATE,
    archived_from TEXT,
    related_ids JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memories_type       ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_source_date ON memories(source_date DESC);
CREATE INDEX IF NOT EXISTS idx_memories_created    ON memories(created_at DESC);

-- Trigger: keep updated_at current
CREATE OR REPLACE FUNCTION moonshine.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS memories_updated ON memories;
CREATE TRIGGER memories_updated
    BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION moonshine.touch_updated_at();

-- ============ Full-Text Search ============
-- Uses tsvector instead of SQLite fts5.

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS search_vec TSVECTOR
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(content, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(tags::text, '')), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_memories_fts ON memories USING GIN(search_vec);

-- ============ Embeddings ============
-- If pgvector is installed: uses VECTOR(768) column for native <=> search.
-- Otherwise: BYTEA column, cosine similarity computed in Python.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        -- pgvector path
        CREATE TABLE IF NOT EXISTS embeddings (
            memory_id   BIGINT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            embedding   VECTOR(768) NOT NULL,
            model       TEXT NOT NULL DEFAULT 'nomic-embed-text',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        -- IVFFlat index for approximate nearest-neighbor (build after bulk load)
        -- CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
    ELSE
        -- bytea fallback path
        CREATE TABLE IF NOT EXISTS embeddings (
            memory_id   BIGINT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            embedding   BYTEA NOT NULL,
            model       TEXT NOT NULL DEFAULT 'nomic-embed-text',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;
EXCEPTION WHEN duplicate_table THEN NULL;
END;
$$;

-- ============ Entity System ============

CREATE TABLE IF NOT EXISTS entities (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    aliases     JSONB NOT NULL DEFAULT '[]',
    description TEXT,
    first_seen  TIMESTAMPTZ,
    last_seen   TIMESTAMPTZ,
    memory_count INTEGER NOT NULL DEFAULT 0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type ON entities(name, type);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

DROP TRIGGER IF EXISTS entities_updated ON entities;
CREATE TRIGGER entities_updated
    BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION moonshine.touch_updated_at();

-- Junction table: which entities appear in which memories
CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id   BIGINT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    entity_id   BIGINT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'mention',
    confidence  REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (memory_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_me_entity ON memory_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_me_memory ON memory_entities(memory_id);

-- ============ Memory Graph (Edges) ============

CREATE TABLE IF NOT EXISTS memory_edges (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id   BIGINT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{}',
    UNIQUE(source_id, target_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type   ON memory_edges(edge_type);

-- ============ Observer Tables ============
-- Mirrors observer/db.js for use with the Python observer pipeline.

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent_id        TEXT NOT NULL DEFAULT 'main',
    channel         TEXT,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    content_type    TEXT NOT NULL DEFAULT 'text',
    ts              TIMESTAMPTZ NOT NULL,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    token_estimate  INTEGER,
    observed_at     TIMESTAMPTZ,
    metadata        JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_session    ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_unobserved ON messages(session_id, observed_at)
    WHERE observed_at IS NULL;

CREATE TABLE IF NOT EXISTS observations (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    agent_id            TEXT NOT NULL DEFAULT 'main',
    content             TEXT NOT NULL,
    priority            TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low')),
    observation_type    TEXT CHECK (observation_type IN (
                            'event', 'decision', 'lesson', 'insight', 'preference', 'behavior'
                        )),
    observation_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    generation          INTEGER NOT NULL DEFAULT 0,
    superseded_at       TIMESTAMPTZ,
    source_message_ids  JSONB NOT NULL DEFAULT '[]',
    token_count         INTEGER,
    entities            JSONB NOT NULL DEFAULT '[]',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_obs_active   ON observations(session_id, superseded_at, token_count)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_obs_priority ON observations(priority, created_at DESC)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_obs_date     ON observations(observation_date DESC);
CREATE INDEX IF NOT EXISTS idx_obs_session  ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_obs_gen      ON observations(generation, session_id)
    WHERE superseded_at IS NULL;
