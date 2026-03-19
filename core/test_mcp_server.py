"""
Tests for the MCP server (mcp-server.py).

Tests the tool implementations and JSON-RPC protocol handler.
External services (Ollama) are mocked.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add core dir to path so we can import
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def mcp_server(db_with_data, tmp_path, monkeypatch):
    """Set up MCP server with a test database and mocked Ollama."""
    db_path = tmp_path / "test_mcp.db"

    # Copy data from db_with_data fixture into a file-based DB
    file_conn = sqlite3.connect(str(db_path))
    db_with_data.backup(file_conn)
    file_conn.close()

    # Patch the DB_PATH and embedding function before importing
    import importlib
    monkeypatch.setattr("mem.DB_PATH", db_path)

    # We need to patch get_embedding in the mcp-server module
    # Since mcp-server.py has a dash, we import it differently
    spec = importlib.util.spec_from_file_location(
        "mcp_server", str(Path(__file__).parent / "mcp-server.py")
    )
    mcp_module = importlib.util.module_from_spec(spec)

    # Patch paths before loading
    monkeypatch.setattr("builtins.__import__", lambda *a, **kw: __builtins__.__import__(*a, **kw))

    spec.loader.exec_module(mcp_module)

    # Patch DB_PATH and get_embedding on the loaded module
    mcp_module.DB_PATH = db_path
    mcp_module.SCHEMA_PATH = Path(__file__).parent / "schema.sql"
    original_get_embedding = mcp_module.get_embedding
    mcp_module.get_embedding = lambda text: None  # Mock: no embeddings

    return mcp_module


@pytest.fixture
def simple_mcp(tmp_path):
    """A simpler MCP server fixture that directly tests handle_request."""
    import importlib

    db_path = tmp_path / "simple_mcp.db"
    schema_path = Path(__file__).parent / "schema.sql"

    # Create DB with schema
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.close()

    # Load the module
    spec = importlib.util.spec_from_file_location(
        "mcp_server", str(Path(__file__).parent / "mcp-server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Override paths
    mod.DB_PATH = db_path
    mod.SCHEMA_PATH = schema_path
    mod.get_embedding = lambda text: None  # No Ollama needed

    return mod


class TestMCPProtocol:
    """Test MCP JSON-RPC protocol handling."""

    def test_initialize(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        })
        assert response["id"] == 1
        assert "result" in response
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert "serverInfo" in response["result"]
        assert response["result"]["serverInfo"]["name"] == "moonshine-memory"

    def test_tools_list(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })
        assert "result" in response
        tools = response["result"]["tools"]
        assert len(tools) == 9
        tool_names = [t["name"] for t in tools]
        assert "memory_save" in tool_names
        assert "memory_search" in tool_names
        assert "memory_context" in tool_names
        assert "memory_briefing" in tool_names
        assert "memory_surface" in tool_names
        assert "memory_entities" in tool_names
        assert "memory_connect" in tool_names
        assert "memory_neighbors" in tool_names
        assert "memory_consolidate" in tool_names

    def test_ping(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "ping"
        })
        assert response["id"] == 3
        assert response["result"] == {}

    def test_unknown_method(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "nonexistent/method"
        })
        assert "error" in response
        assert response["error"]["code"] == -32601

    def test_notification_returns_none(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        })
        assert response is None


class TestMemorySave:
    """Test memory_save tool."""

    def test_save_creates_memory(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "Test memory",
                    "content": "This is a test memory for the test suite",
                    "type": "lesson",
                    "importance": 4,
                    "tags": ["test", "suite"]
                }
            }
        })
        result_text = response["result"]["content"][0]["text"]
        assert "Saved memory #" in result_text
        assert "Test memory" in result_text

    def test_save_returns_id(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "ID check",
                    "content": "Should return an ID",
                    "type": "event"
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "#" in text
        # Extract ID
        import re
        match = re.search(r"#(\d+)", text)
        assert match is not None
        assert int(match.group(1)) > 0

    def test_save_requires_title(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "content": "No title provided"
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Error" in text or "required" in text.lower()

    def test_save_invalid_type(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "Bad type",
                    "content": "Invalid type test",
                    "type": "invalid_type"
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Error" in text

    def test_save_dedup_check(self, simple_mcp):
        # Save once
        simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "Unique fact",
                    "content": "First save"
                }
            }
        })
        # Save duplicate
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "Unique fact",
                    "content": "Duplicate attempt"
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Duplicate" in text or "not saved" in text.lower()


class TestMemorySearch:
    """Test memory_search tool."""

    def test_search_finds_saved_memories(self, simple_mcp):
        # Save a memory first
        simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "memory_save",
                "arguments": {
                    "title": "Kubernetes deployment",
                    "content": "Deployed app to k8s cluster with helm charts",
                    "type": "event"
                }
            }
        })
        # Search for it (FTS, since embeddings are mocked)
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "memory_search",
                "arguments": {
                    "query": "Kubernetes",
                    "semantic": False
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Kubernetes" in text

    def test_search_requires_query(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "memory_search",
                "arguments": {
                    "query": ""
                }
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Error" in text or "required" in text.lower()


class TestMemoryContext:
    """Test memory_context tool."""

    def test_context_returns_formatted_string(self, simple_mcp):
        # Save some memories first
        for i in range(3):
            simple_mcp.handle_request({
                "jsonrpc": "2.0",
                "id": 30 + i,
                "method": "tools/call",
                "params": {
                    "name": "memory_save",
                    "arguments": {
                        "title": f"Context test {i}",
                        "content": f"Content for context test {i}",
                        "importance": 4 + (i % 2)
                    }
                }
            })
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 40,
            "method": "tools/call",
            "params": {
                "name": "memory_context",
                "arguments": {}
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Memory Context" in text


class TestMemoryBriefing:
    """Test memory_briefing tool."""

    def test_briefing_returns_structured_output(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {
                "name": "memory_briefing",
                "arguments": {}
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Session Briefing" in text


class TestInvalidToolCall:
    """Test error handling for invalid tool calls."""

    def test_unknown_tool_name(self, simple_mcp):
        response = simple_mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 60,
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {}
            }
        })
        text = response["result"]["content"][0]["text"]
        assert "Unknown tool" in text or "unknown" in text.lower()
