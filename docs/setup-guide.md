# Setup Guide

Get agent-memory running with your platform in ~5 minutes.

---

## Prerequisites

**Required:**
- Python 3.9+ (MCP server, CLI)
- Node.js 18+ (observer pipeline)
- SQLite 3.35+ (FTS5 support — most systems ship this)

**Optional but recommended:**
- [Ollama](https://ollama.com/) with `nomic-embed-text` (274MB) — enables semantic search. Without it, you get keyword search only.

```bash
# Install Ollama (macOS/Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull the embedding model
ollama pull nomic-embed-text
```

---

## Install

```bash
# Clone
git clone https://github.com/cneiman/agent-memory
cd agent-memory

# Run installer
./install.sh
```

The installer:
1. Creates `core/memories.db` from `core/schema.sql`
2. Installs Python dependencies (`requests` for Ollama communication)
3. Installs Node.js dependencies for the observer (`cd observer && npm install`)
4. Creates starter files from templates (MEMORY.md, etc.) if they don't exist
5. Makes the `mem` CLI executable

If you prefer to do it manually:

```bash
# Create database
cd core
sqlite3 memories.db < schema.sql

# Install Python deps
pip3 install requests

# Install observer deps
cd ../observer
npm install

# Make CLI executable
chmod +x ../core/mem
```

---

## Platform Setup

### OpenClaw

OpenClaw connects via hooks that fire on message events.

**1. Copy the adapter:**

```bash
cp -r adapters/openclaw/hooks/conversation-observer ~/your-workspace/hooks/
```

**2. Enable the hook:**

```bash
openclaw hooks enable conversation-observer
```

**3. Register the MCP server:**

Add to your OpenClaw config (`~/.openclaw/openclaw.json`) or workspace `.mcp.json`:

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "python3",
      "args": ["/path/to/agent-memory/core/mcp-server.py"],
      "transport": "stdio"
    }
  }
}
```

**4. Set up context injection:**

Add MEMORY.md to your workspace's injected files in the OpenClaw config. CONTEXT.md too, if you set up the context generator.

**5. (Optional) Set up the context generator:**

```bash
# Copy and customize the context script
cp context/generate-context.sh ~/your-workspace/scripts/

# Edit to point to your data sources
# Then add to cron or launchd (runs every 30 min)
*/30 * * * * /path/to/generate-context.sh
```

---

### Claude Code

Claude Code connects via its hooks system and MCP registration.

**1. Register the MCP server:**

Add to your project's `.mcp.json` (or global `~/.claude/.mcp.json`):

```json
{
  "mcpServers": {
    "agent-memory": {
      "command": "python3",
      "args": ["/path/to/agent-memory/core/mcp-server.py"],
      "transport": "stdio"
    }
  }
}
```

**2. Copy hooks (optional but recommended):**

```bash
# Copy adapter hooks to your Claude Code hooks directory
cp adapters/claude-code/hooks/* ~/.claude/hooks/
```

The hooks provide:
- **SessionStart** — loads context from memory at the start of each session
- **PreCompact** — flushes pending observations before context compaction
- **PostToolUse** — captures tool interactions for the observer

**3. Set up MEMORY.md:**

Copy the template to your project root or `~/.claude/`:

```bash
cp templates/MEMORY.md ~/your-project/MEMORY.md
```

Claude Code automatically injects `MEMORY.md` from the project root or `CLAUDE.md` — rename as needed.

---

### Cursor

Cursor connects via its MCP configuration.

**1. Add MCP config:**

Open Cursor Settings → MCP Servers, and add:

```json
{
  "agent-memory": {
    "command": "python3",
    "args": ["/path/to/agent-memory/core/mcp-server.py"],
    "transport": "stdio"
  }
}
```

Or add to your project's `.cursor/mcp.json`.

**2. Set up .cursorrules:**

Add memory-related rules to your `.cursorrules` file:

```
# Memory
At session start, call memory_context to load relevant memories.
When you learn something important, use memory_save to persist it.
Use memory_search when you need to recall past decisions or context.
```

You can start with the template at `adapters/cursor/cursorrules-snippet.md`.

**3. MEMORY.md:**

```bash
cp templates/MEMORY.md ~/your-project/MEMORY.md
```

---

### Generic (Any MCP-Compatible Tool)

If your tool supports MCP (Model Context Protocol), you can connect agent-memory.

**1. Register the MCP server** using whatever mechanism your tool provides:

```
Command: python3 /path/to/agent-memory/core/mcp-server.py
Transport: stdio
```

**2. Set up the file watcher (optional):**

The generic adapter watches a directory for JSONL session transcripts and feeds them to the observer:

```bash
# Configure the watch directory
export AGENT_MEMORY_WATCH_DIR=/path/to/session/logs

# Start the watcher
node adapters/generic/watcher.js
```

Any tool that writes session logs as JSONL (one JSON object per line with `role` and `content` fields) will work.

**3. MEMORY.md and CONTEXT.md:**

Set up your tool to inject these files into the system prompt. The mechanism varies by platform — check your tool's docs for "system prompt" or "context injection."

---

## Verifying It Works

### Test the CLI

```bash
cd agent-memory/core

# Add a test memory
./mem add "Test memory" --type lesson --content "Verifying the setup works" --importance 3

# Search for it
./mem search "test"

# Check stats
./mem stats
```

You should see the memory in search results and a count of 1 in stats.

### Test the MCP Server

```bash
# The MCP server communicates over stdio (JSON-RPC).
# Quick smoke test — send a tools/list request:

echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 core/mcp-server.py
```

You should see a JSON response listing all 9 tools.

### Test Semantic Search (if Ollama is running)

```bash
# Verify Ollama is running
curl http://127.0.0.1:11434/api/tags

# Add a memory and search semantically
./mem add "Decided to use SQLite for storage" --type decision --content "Chose SQLite over Postgres for portability"
./mem search "database choice" --semantic
```

The semantic search should return the SQLite memory even though "database choice" doesn't appear in the text.

### Test the Observer

```bash
cd agent-memory/observer

# Run against a test conversation (included in the repo)
node observe.js test

# Check that observations were created
sqlite3 ../observer/observations.db "SELECT * FROM observations LIMIT 5"
```

---

## Configuring the Observer

The observer needs an API key for the LLM that extracts observations.

### API Key

Set one of these (checked in order):

1. **Environment variable:** `export ANTHROPIC_API_KEY=sk-ant-...`
2. **File:** `~/.env.anthropic` containing `ANTHROPIC_API_KEY=sk-ant-...`
3. **OpenClaw config:** `~/.openclaw/openclaw.json` → `auth.anthropic`

### Model Choice

The default is Claude Haiku (fast, cheap, good at structured extraction). You can change this in the observer config:

```bash
# In observer/observe.js and observer/reflect.js
# Change the MODEL constant:
const MODEL = 'claude-haiku-4-5-20251001';  # default
```

Any model that handles structured JSON extraction works. Haiku-class models are recommended for cost reasons — the observer fires frequently.

### Thresholds

```javascript
// observer/observe.js
const TOKEN_THRESHOLD = 3000;   // Min unobserved tokens before observer fires
const MAX_OBSERVATIONS = 10;     // Max observations per observer run

// observer/reflect.js
const TOKEN_THRESHOLD = 4000;   // Min observation tokens before reflector fires
```

Higher thresholds = fewer LLM calls = lower cost, but coarser observation granularity. The defaults work well for active daily use.

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `AGENT_MEMORY_DB` | Path to memories.db | `./core/memories.db` |
| `AGENT_MEMORY_OBSERVER_DB` | Path to observations.db | `./observer/observations.db` |
| `ANTHROPIC_API_KEY` | API key for observer LLM calls | (none — required for observer) |
| `OLLAMA_URL` | Ollama API endpoint | `http://127.0.0.1:11434` |
| `OLLAMA_EMBED_MODEL` | Embedding model name | `nomic-embed-text` |
| `AGENT_MEMORY_WATCH_DIR` | Directory for generic file watcher | (none) |

---

## Next Steps

- [Architecture](architecture.md) — understand the 3-tier model in depth
- [Maintenance](maintenance.md) — keep memory healthy over time
- [CONTRIBUTING.md](../CONTRIBUTING.md) — add adapters, extend tools, contribute
