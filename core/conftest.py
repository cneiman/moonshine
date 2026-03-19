"""
Shared fixtures for moonshine core tests.
"""

import json
import sqlite3
import os
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like SQLite DB in tmp_path with schema applied."""
    db_path = tmp_path / "test_memories.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    yield conn
    conn.close()


@pytest.fixture
def db_with_data(db):
    """DB pre-populated with sample memories, entities, and edges."""
    memories = [
        ("event", "Deployed moonshine v1", "First production deployment of the moonshine memory system", '["moonshine","deploy"]', 5, "session:2026-03-01", "2026-03-01"),
        ("lesson", "FTS5 needs triggers for sync", "Discovered that FTS5 virtual tables require explicit sync triggers on INSERT/UPDATE/DELETE", '["sqlite","fts5"]', 4, "session:2026-02-15", "2026-02-15"),
        ("person", "Christopher Neiman", "Senior engineer, moonshine creator. Uses Claude Code extensively.", '["engineer","ai"]', 5, "manual", "2026-01-01"),
        ("insight", "Semantic search needs relevance floor", "Without a minimum cosine similarity threshold, semantic search returns noise. 0.35 works well for nomic-embed-text.", '["search","embeddings"]', 4, "session:2026-02-19", "2026-02-19"),
        ("decision", "Use SQLite over Postgres", "Chose SQLite for moonshine because it's zero-dependency and file-based, perfect for single-agent use.", '["architecture","database"]', 4, "session:2026-02-10", "2026-02-10"),
        ("project", "Moonshine memory system", "3-tier memory architecture: MEMORY.md (hot), CONTEXT.md (warm), memories.db (cold archive).", '["moonshine","memory"]', 5, "manual", "2026-02-11"),
        ("skill", "Python SQLite patterns", "Using sqlite3.Row for dict-like access, parameterized queries, WAL mode for concurrency.", '["python","sqlite"]', 3, "session:2026-02-12", "2026-02-12"),
        ("behavior", "Christopher prefers direct communication", "No corporate fluff. Action over asking permission for internal stuff.", '["preferences"]', 4, "manual", "2026-01-15"),
        ("preference", "Trash over rm", "Always use trash command instead of rm for recoverability.", '["safety","cli"]', 3, "manual", "2026-01-20"),
        ("insight", "Cross-encoder reranking improves precision", "Adding a cross-encoder reranking step after initial retrieval significantly improves result quality.", '["search","reranker"]', 4, "session:2026-03-15", "2026-03-15"),
    ]

    for mem in memories:
        db.execute("""
            INSERT INTO memories (type, title, content, tags, importance, source, source_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, mem)

    # Add entities
    entities = [
        ("Christopher Neiman", "person", '["christopher", "neiman"]', "2026-01-01", "2026-03-15"),
        ("Moonshine", "project", '["moonshine"]', "2026-02-11", "2026-03-15"),
        ("SQLite", "tool", '["sqlite", "sqlite3"]', "2026-02-10", "2026-03-01"),
        ("Claude Code", "tool", '["claude-code", "claude"]', "2026-01-01", "2026-03-10"),
        ("Ollama", "tool", '["ollama"]', "2026-02-11", "2026-03-01"),
    ]

    for ent in entities:
        db.execute("""
            INSERT INTO entities (name, type, aliases, first_seen, last_seen, memory_count)
            VALUES (?, ?, ?, ?, ?, 0)
        """, ent)

    # Link some entities to memories
    links = [
        (1, 2, "subject", 1.0),   # Deployed moonshine -> Moonshine
        (3, 1, "subject", 1.0),   # Christopher -> Christopher Neiman
        (4, 3, "mention", 0.9),   # Semantic search -> SQLite (mention)
        (5, 3, "subject", 1.0),   # Use SQLite -> SQLite
        (6, 2, "subject", 1.0),   # Moonshine project -> Moonshine
    ]
    for link in links:
        db.execute("""
            INSERT INTO memory_entities (memory_id, entity_id, role, confidence)
            VALUES (?, ?, ?, ?)
        """, link)

    # Update entity memory counts
    db.execute("""
        UPDATE entities SET memory_count = (
            SELECT COUNT(*) FROM memory_entities WHERE entity_id = entities.id
        )
    """)

    # Add edges
    edges = [
        (1, 6, "relates_to", 0.8),   # Deployed moonshine <-> Moonshine project
        (4, 5, "caused_by", 0.9),     # Semantic search insight <-> SQLite decision
    ]
    for edge in edges:
        db.execute("""
            INSERT INTO memory_edges (source_id, target_id, edge_type, weight)
            VALUES (?, ?, ?, ?)
        """, edge)

    db.commit()
    return db
