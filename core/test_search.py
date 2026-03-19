"""
Tests for FTS5 keyword search functionality.

Tests the search layer directly against SQLite (no Ollama dependency).
"""

import json
import sqlite3
from pathlib import Path

import pytest


class TestFTS5KeywordSearch:
    """FTS5 keyword search returns ranked results."""

    def test_single_word_search(self, db_with_data):
        rows = db_with_data.execute("""
            SELECT m.id, m.title, m.content, fts.rank
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'moonshine'
            ORDER BY fts.rank
        """).fetchall()
        assert len(rows) >= 1
        # All results should contain moonshine in title or content
        for r in rows:
            text = (r["title"] + " " + r["content"]).lower()
            assert "moonshine" in text

    def test_multi_word_query(self, db_with_data):
        """Multi-word queries should work with FTS5."""
        rows = db_with_data.execute("""
            SELECT m.id, m.title
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH '"semantic search"'
            ORDER BY fts.rank
        """).fetchall()
        # Phrase match: "semantic search" should match the insight
        assert len(rows) >= 1

    def test_or_query(self, db_with_data):
        """OR queries should return results matching either term."""
        rows = db_with_data.execute("""
            SELECT m.id, m.title
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH '"sqlite" OR "embeddings"'
            ORDER BY fts.rank
        """).fetchall()
        assert len(rows) >= 2

    def test_empty_query_returns_nothing(self, db_with_data):
        """An empty string match should raise or return nothing."""
        with pytest.raises(sqlite3.OperationalError):
            db_with_data.execute("""
                SELECT * FROM memories_fts WHERE memories_fts MATCH ''
            """).fetchall()

    def test_no_match_returns_empty(self, db_with_data):
        rows = db_with_data.execute("""
            SELECT m.id FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'xyznonexistentterm'
        """).fetchall()
        assert len(rows) == 0

    def test_special_characters_dont_crash(self, db_with_data):
        """Queries with special characters shouldn't crash the database."""
        # These might raise OperationalError for invalid FTS syntax, but shouldn't crash
        for query in ["hello-world", "test_underscore", "foo/bar"]:
            try:
                db_with_data.execute(
                    "SELECT * FROM memories_fts WHERE memories_fts MATCH ?",
                    (f'"{query}"',)
                ).fetchall()
            except sqlite3.OperationalError:
                pass  # Invalid FTS syntax is OK, crashing is not

    def test_results_have_expected_fields(self, db_with_data):
        """Search results should include all key fields."""
        rows = db_with_data.execute("""
            SELECT m.id, m.type, m.title, m.content, m.importance,
                   m.source_date, m.tags, fts.rank
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'SQLite'
            ORDER BY fts.rank
        """).fetchall()
        assert len(rows) >= 1
        row = rows[0]
        assert row["id"] is not None
        assert row["type"] is not None
        assert row["title"] is not None
        assert row["content"] is not None
        assert row["importance"] is not None
        assert row["rank"] is not None

    def test_search_with_type_filter(self, db_with_data):
        """Search results can be filtered by memory type."""
        rows = db_with_data.execute("""
            SELECT m.id, m.type, m.title
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'SQLite' AND m.type = 'decision'
        """).fetchall()
        assert len(rows) >= 1
        assert all(r["type"] == "decision" for r in rows)

    def test_search_with_date_filter(self, db_with_data):
        """Search results can be filtered by date."""
        rows = db_with_data.execute("""
            SELECT m.id, m.title, m.source_date
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'moonshine'
            AND m.source_date >= '2026-03-01'
        """).fetchall()
        assert len(rows) >= 1
        for r in rows:
            assert r["source_date"] >= "2026-03-01"

    def test_fts_rank_ordering(self, db_with_data):
        """Results should be rankable by FTS5 rank."""
        rows = db_with_data.execute("""
            SELECT m.id, m.title, fts.rank
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH 'memory'
            ORDER BY fts.rank
        """).fetchall()
        if len(rows) >= 2:
            # FTS5 rank is negative (lower = better match), so ordered ascending
            ranks = [r["rank"] for r in rows]
            assert ranks == sorted(ranks)


class TestSearchHelpers:
    """Test search helper functions from mem.py."""

    def test_fts_search_function(self, db_with_data):
        """_fts_search should return (score, row_dict) tuples."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from mem import _fts_search

        results = _fts_search(db_with_data, "moonshine", limit=5)
        assert len(results) >= 1
        for score, row in results:
            assert isinstance(row, dict)
            assert "id" in row
            assert "title" in row

    def test_fts_search_with_type_filter(self, db_with_data):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from mem import _fts_search

        results = _fts_search(db_with_data, "moonshine", type_filter="event", limit=5)
        for _, row in results:
            assert row["type"] == "event"

    def test_fts_search_empty_result(self, db_with_data):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from mem import _fts_search

        results = _fts_search(db_with_data, "xyznonexistent", limit=5)
        assert len(results) == 0

    def test_expand_acronyms(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from mem import expand_acronyms

        expanded = expand_acronyms("How does MCP work?")
        assert "model context protocol" in expanded.lower()

    def test_expand_acronyms_no_match(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from mem import expand_acronyms

        result = expand_acronyms("How does search work?")
        assert result == "How does search work?"
