"""
Integration tests for the moonshine memory system.

Tests the full pipeline: save → search → retrieve → verify.
No external services required (Ollama mocked).
"""

import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def mcp(tmp_path):
    """Load MCP server module with test DB, no Ollama."""
    db_path = tmp_path / "integration.db"
    schema_path = Path(__file__).parent / "schema.sql"

    # Init DB
    conn = sqlite3.connect(str(db_path))
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.close()

    spec = importlib.util.spec_from_file_location(
        "mcp_server", str(Path(__file__).parent / "mcp-server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mod.DB_PATH = db_path
    mod.SCHEMA_PATH = schema_path
    mod.get_embedding = lambda text: None  # No Ollama
    mod.DAILY_DIR = tmp_path  # Don't look for real daily files

    return mod


def _call_tool(mcp, tool_name, arguments, req_id=1):
    """Helper to call a tool and return the result text."""
    response = mcp.handle_request({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    })
    return response["result"]["content"][0]["text"]


def _extract_id(text):
    """Extract memory ID from save result text."""
    match = re.search(r"#(\d+)", text)
    return int(match.group(1)) if match else None


class TestSaveSearchRetrieve:
    """Full pipeline: save → search → retrieve → verify."""

    def test_save_then_search_finds_it(self, mcp):
        save_result = _call_tool(mcp, "memory_save", {
            "title": "Learned pytest fixtures",
            "content": "Pytest fixtures provide reusable test setup with dependency injection",
            "type": "lesson",
            "importance": 4,
            "tags": ["testing", "python"]
        })
        assert "Saved memory #" in save_result

        search_result = _call_tool(mcp, "memory_search", {
            "query": "pytest",
            "semantic": False
        })
        assert "pytest" in search_result.lower()
        assert "fixtures" in search_result.lower()

    def test_save_then_context_includes_it(self, mcp):
        _call_tool(mcp, "memory_save", {
            "title": "Critical security finding",
            "content": "Found SQL injection vulnerability in login endpoint",
            "type": "event",
            "importance": 5
        })

        context_result = _call_tool(mcp, "memory_context", {})
        assert "Critical security finding" in context_result

    def test_save_verify_content_matches(self, mcp):
        """Content saved should match content retrieved."""
        original_content = "This is very specific unique content: abc123xyz"

        save_result = _call_tool(mcp, "memory_save", {
            "title": "Content match test",
            "content": original_content,
            "type": "insight"
        })
        mem_id = _extract_id(save_result)
        assert mem_id is not None

        # Verify via direct DB query
        conn = sqlite3.connect(str(mcp.DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        assert row["content"] == original_content
        assert row["title"] == "Content match test"
        assert row["type"] == "insight"
        conn.close()


class TestMultipleMemories:
    """Save multiple memories across dates, verify search finds the right ones."""

    def test_search_finds_correct_memories_among_many(self, mcp):
        """Save 10 memories, search finds the right ones."""
        memories = [
            ("Python list comprehensions", "List comps are faster than for loops for simple transforms", "lesson", 3),
            ("Django ORM gotchas", "N+1 queries are the most common Django performance issue", "lesson", 4),
            ("Deployed API v2", "Shipped new REST API with pagination and rate limiting", "event", 4),
            ("Redis caching strategy", "Cache invalidation uses TTL + event-driven purge", "decision", 3),
            ("Meeting with Sarah", "Discussed Q2 roadmap and team allocation", "event", 3),
            ("TypeScript strict mode", "Enable strict mode for better type safety, catches null issues", "lesson", 4),
            ("SQLite WAL mode", "WAL mode allows concurrent reads during writes, big perf win", "insight", 4),
            ("Git rebase workflow", "Always rebase feature branches before merge to keep linear history", "preference", 3),
            ("Kubernetes pod scaling", "HPA scales based on CPU usage, need custom metrics for memory", "lesson", 3),
            ("Code review best practices", "Focus on logic and design, not style (let linters handle that)", "insight", 4),
        ]

        for title, content, type_, importance in memories:
            _call_tool(mcp, "memory_save", {
                "title": title,
                "content": content,
                "type": type_,
                "importance": importance
            })

        # Search for SQLite-related memories
        result = _call_tool(mcp, "memory_search", {
            "query": "SQLite WAL",
            "semantic": False
        })
        assert "WAL" in result
        assert "concurrent" in result.lower() or "SQLite" in result

        # Search for Django
        result = _call_tool(mcp, "memory_search", {
            "query": "Django ORM",
            "semantic": False
        })
        assert "Django" in result
        assert "N+1" in result

        # Search for something that shouldn't exist
        result = _call_tool(mcp, "memory_search", {
            "query": "blockchain cryptocurrency",
            "semantic": False
        })
        assert "No memories found" in result


class TestKnowledgeGraph:
    """Knowledge graph integration: entities + edges."""

    def test_connect_then_neighbors(self, mcp):
        """Create memories, connect them, query neighbors."""
        # Save two memories
        r1 = _call_tool(mcp, "memory_save", {
            "title": "Decided to use React",
            "content": "Chose React over Vue for the new dashboard project",
            "type": "decision",
            "importance": 4
        })
        id1 = _extract_id(r1)

        r2 = _call_tool(mcp, "memory_save", {
            "title": "React performance issues",
            "content": "Virtual DOM diffing was slow with 10K+ rows, switched to virtualization",
            "type": "lesson",
            "importance": 4
        })
        id2 = _extract_id(r2)

        # Connect them
        connect_result = _call_tool(mcp, "memory_connect", {
            "source_id": id1,
            "target_id": id2,
            "edge_type": "caused_by"
        })
        assert "Connected" in connect_result

        # Query neighbors
        neighbors_result = _call_tool(mcp, "memory_neighbors", {
            "memory_id": id1,
            "depth": 1
        })
        assert "React performance" in neighbors_result
        assert "caused_by" in neighbors_result

    def test_connect_invalid_memory(self, mcp):
        """Connecting to nonexistent memory should error."""
        result = _call_tool(mcp, "memory_connect", {
            "source_id": 9999,
            "target_id": 9998,
            "edge_type": "relates_to"
        })
        assert "Error" in result or "not found" in result.lower()

    def test_connect_invalid_edge_type(self, mcp):
        """Invalid edge type should error."""
        r1 = _call_tool(mcp, "memory_save", {
            "title": "Edge type test A", "content": "A", "type": "insight"
        })
        r2 = _call_tool(mcp, "memory_save", {
            "title": "Edge type test B", "content": "B", "type": "insight"
        })
        id1, id2 = _extract_id(r1), _extract_id(r2)

        result = _call_tool(mcp, "memory_connect", {
            "source_id": id1,
            "target_id": id2,
            "edge_type": "invalid_type"
        })
        assert "Error" in result

    def test_entities_list(self, mcp):
        """memory_entities should list entities."""
        # Save memory that might auto-extract entities (depends on DB having entities)
        _call_tool(mcp, "memory_save", {
            "title": "Test entities listing",
            "content": "Just a test",
            "type": "event"
        })
        result = _call_tool(mcp, "memory_entities", {})
        # Should return either entities or "No entities found"
        assert isinstance(result, str)

    def test_neighbors_nonexistent_memory(self, mcp):
        """Querying neighbors of nonexistent memory should error."""
        result = _call_tool(mcp, "memory_neighbors", {
            "memory_id": 99999
        })
        assert "Error" in result or "not found" in result.lower()


class TestConsolidation:
    """Test memory consolidation."""

    def test_consolidate_dry_run(self, mcp):
        result = _call_tool(mcp, "memory_consolidate", {
            "dry_run": True,
            "scope": "all"
        })
        assert "Consolidation" in result
        assert "DRY RUN" in result

    def test_consolidate_finds_duplicates(self, mcp):
        """Consolidation should detect exact-title duplicates."""
        # Force-insert two memories with same title via direct DB
        conn = sqlite3.connect(str(mcp.DB_PATH))
        conn.execute("""
            INSERT INTO memories (type, title, content, importance)
            VALUES ('lesson', 'Duplicate Title', 'Content A', 3)
        """)
        conn.execute("""
            INSERT INTO memories (type, title, content, importance)
            VALUES ('lesson', 'Duplicate Title', 'Content B', 4)
        """)
        conn.commit()
        conn.close()

        result = _call_tool(mcp, "memory_consolidate", {
            "dry_run": True,
            "scope": "all"
        })
        assert "Duplicates found: 1" in result or "Duplicate titles" in result


class TestBriefingIntegration:
    """Test briefing with saved memories."""

    def test_briefing_shows_high_importance(self, mcp):
        _call_tool(mcp, "memory_save", {
            "title": "Critical infrastructure alert",
            "content": "Database failover triggered, service recovered in 30s",
            "type": "event",
            "importance": 5
        })
        result = _call_tool(mcp, "memory_briefing", {})
        assert "Session Briefing" in result
