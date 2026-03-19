# Generic Adapter — File Watcher

Connect moonshine to any tool that writes session logs by watching a directory for JSONL transcript files.

## How It Works

The watcher monitors a directory for `.jsonl` files. Each line should be a JSON object with at least `role` and `content` fields. When new lines appear, they're fed to the observer pipeline for automatic compression and memory extraction.

```
your-tool writes transcripts → watch dir → observer pipeline → memories.db
```

## Setup

1. Configure the watch directory and observer:
   ```bash
   export MOONSHINE_WATCH_DIR="/path/to/transcripts/"
   export OBSERVER_DB="/path/to/observations.db"
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

2. Run the watcher:
   ```bash
   node adapters/generic/watcher.js
   ```

3. Point your tool's transcript output to the watch directory.

## Expected Format

Each `.jsonl` file should have one JSON object per line:

```jsonl
{"role": "user", "content": "What's the status of project X?", "timestamp": "2026-03-19T08:00:00Z"}
{"role": "assistant", "content": "Project X is on track...", "timestamp": "2026-03-19T08:00:05Z"}
```

Required fields:
- `role` — `"user"` or `"assistant"`
- `content` — the message text

Optional fields:
- `timestamp` — ISO 8601 timestamp (defaults to file modification time)
- `session_id` — group messages by session (defaults to filename)

## MCP Server

For tools that support MCP directly, skip the file watcher and connect the MCP server:

```bash
python3 core/mcp-server.py
```

The MCP server communicates over stdio. Any MCP-compatible client can connect to it — see the [MCP spec](https://modelcontextprotocol.io/) for details.

## Dependencies

The watcher uses `better-sqlite3` from the observer's `node_modules/`. Make sure the observer dependencies are installed:

```bash
cd observer && npm install && cd ..
```

If the observer isn't installed, the watcher still runs — it logs messages to the console but won't persist them to the database or trigger the observer pipeline.

## Requirements

- Node.js 18+
- Python 3.8+ (for MCP server)
- [Ollama](https://ollama.ai/) with `nomic-embed-text` for semantic search (optional)
