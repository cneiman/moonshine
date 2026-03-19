# Claude Code Adapter

Connect moonshine to [Claude Code](https://docs.anthropic.com/en/docs/claude-code) via MCP.

## Setup

1. Copy the `.mcp.json` file to your project root:
   ```bash
   cp adapters/claude-code/.mcp.json /path/to/your-project/.mcp.json
   ```

2. Update paths in `.mcp.json` if moonshine is not in your project root:
   ```json
   {
     "mcpServers": {
       "moonshine": {
         "command": "python3",
         "args": ["/absolute/path/to/moonshine/core/mcp-server.py"],
         "env": {
           "MOONSHINE_DB": "/absolute/path/to/memories.db",
           "MOONSHINE_WORKSPACE": "/absolute/path/to/workspace/"
         }
       }
     }
   }
   ```

3. Start Claude Code in your project — the MCP server starts automatically.

## Available Tools

Once connected, Claude Code has access to 9 memory tools:

| Tool | Description |
|------|-------------|
| `memory_context` | Load relevant memories at session start |
| `memory_search` | Hybrid FTS5 + semantic + graph search |
| `memory_save` | Persist memories with auto-embedding |
| `memory_briefing` | Structured session briefing (no LLM cost) |
| `memory_surface` | Proactive memory surfacing via entity graph |
| `memory_entities` | List/query knowledge graph entities |
| `memory_connect` | Create typed edges between memories |
| `memory_neighbors` | Graph neighbor traversal |
| `memory_consolidate` | Find contradictions, merge duplicates |

## Requirements

- Python 3.8+
- `requests` library (`pip install requests`)
- [Ollama](https://ollama.ai/) running locally with `nomic-embed-text` model for semantic search
  ```bash
  ollama pull nomic-embed-text
  ```

## Tips

- Add a Claude Code hook for `SessionStart` that calls `memory_context` to load memory automatically
- The MCP server auto-creates the database if it doesn't exist
- Semantic search requires Ollama; keyword search (FTS5) works without it
