-- agent-memory: SQLite schema for long-term AI agent memory
--
-- Tables:
--   memories      — core memory storage (events, lessons, decisions, etc.)
--   embeddings    — vector embeddings for semantic search
--   entities      — knowledge graph nodes (people, projects, tools, etc.)
--   memory_entities — links between memories and entities
--   memory_edges  — typed edges between memories (relates_to, contradicts, etc.)
--   memories_fts  — FTS5 virtual table for full-text keyword search
--
-- Triggers maintain FTS sync and auto-update timestamps.

-- ============================================================
-- Core memory table
-- ============================================================
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,                                         -- event, lesson, person, behavior, project, insight, decision, preference, skill
  title TEXT NOT NULL,                                        -- short, searchable title
  content TEXT NOT NULL,                                      -- full description (standalone, context-free)
  metadata TEXT DEFAULT '{}',                                 -- arbitrary JSON metadata
  tags TEXT DEFAULT '[]',                                     -- JSON array of tag strings
  importance INTEGER DEFAULT 3 CHECK (importance BETWEEN 1 AND 5), -- 1=trivial, 3=normal, 5=critical
  source TEXT,                                                -- origin (e.g., "session:2026-02-11")
  source_date DATE,                                           -- when the event/knowledge occurred
  archived_from TEXT,                                         -- if archived from another location
  related_ids TEXT DEFAULT '[]',                               -- JSON array of related memory IDs
  created_at TEXT DEFAULT (datetime('now', 'localtime')),
  updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_source_date ON memories(source_date DESC);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

-- ============================================================
-- Embeddings table (for semantic/vector search)
-- ============================================================
CREATE TABLE IF NOT EXISTS embeddings (
  memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL,                                    -- packed float32 array
  model TEXT NOT NULL DEFAULT 'nomic-embed-text',              -- embedding model used
  created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- ============================================================
-- FTS5 full-text search (synced via triggers)
-- ============================================================
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  title,
  content,
  tags,
  content='memories',
  content_rowid='id'
);

-- Keep FTS index in sync with memories table
CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, title, content, tags)
  VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
  VALUES ('delete', old.id, old.title, old.content, old.tags);
  INSERT INTO memories_fts(rowid, title, content, tags)
  VALUES (new.id, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
  VALUES ('delete', old.id, old.title, old.content, old.tags);
END;

-- Auto-update updated_at timestamp
CREATE TRIGGER IF NOT EXISTS memories_updated AFTER UPDATE ON memories BEGIN
  UPDATE memories SET updated_at = datetime('now', 'localtime') WHERE id = new.id;
END;

-- ============================================================
-- Knowledge graph: entities (nodes)
-- ============================================================
CREATE TABLE IF NOT EXISTS entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,                                         -- person, project, tool, concept, organization
  aliases TEXT DEFAULT '[]',                                  -- JSON array of alternate names
  description TEXT,
  first_seen TEXT,
  last_seen TEXT,
  memory_count INTEGER DEFAULT 0,
  metadata TEXT DEFAULT '{}',
  created_at TEXT DEFAULT (datetime('now', 'localtime')),
  updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_type ON entities(name, type);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

-- ============================================================
-- Knowledge graph: memory ↔ entity links
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  role TEXT DEFAULT 'mention',                                -- mention, subject, etc.
  confidence REAL DEFAULT 1.0,
  PRIMARY KEY (memory_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_me_entity ON memory_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_me_memory ON memory_entities(memory_id);

-- ============================================================
-- Knowledge graph: memory ↔ memory edges
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  edge_type TEXT NOT NULL,                                    -- relates_to, contradicts, supersedes, caused_by, follow_up
  weight REAL DEFAULT 1.0,                                    -- 0.0–1.0 edge strength
  created_at TEXT DEFAULT (datetime('now', 'localtime')),
  metadata TEXT DEFAULT '{}',
  UNIQUE(source_id, target_id, edge_type)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON memory_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON memory_edges(edge_type);
