# Contributing to moonshine

Thanks for considering a contribution. Here's how the project is structured and how to add to it.

---

## Project Layout

```
moonshine/
├── core/               # Python — MCP server, CLI, schema
├── observer/           # Node.js — conversation compression pipeline
├── context/            # Shell — dynamic context generation
├── adapters/           # TypeScript/JS — platform integrations
│   ├── openclaw/
│   ├── claude-code/
│   ├── cursor/
│   └── generic/
├── templates/          # Markdown — starter files
├── evals/              # YAML + Python — promptfoo test suite
└── docs/               # Markdown — guides and architecture
```

### Language Choices

- **Core (MCP server, CLI):** Python. The MCP ecosystem is Python-heavy, and the server needs to be lightweight with no heavy framework dependencies.
- **Observer pipeline:** Node.js. It handles async I/O (API calls, DB writes) well and the prompts are easier to template in JS.
- **Adapters:** Whatever the target platform uses. OpenClaw hooks are JS/TS. Claude Code hooks are shell + JS. Cursor is JSON config.
- **Context generator:** Shell script. It orchestrates CLI tools and writes a file. No framework needed.

---

## Adding a New Adapter

Adapters are the most useful contribution. Each adapter connects moonshine to a platform's lifecycle events.

### What an Adapter Needs

1. **Message capture** — hook into the platform's message events and write them to the observer database.
2. **Context injection** — load MEMORY.md and CONTEXT.md into the platform's system prompt at session start.
3. **MCP registration** — configure the platform to connect to the MCP server.
4. **README** — how to install and configure the adapter.

### Structure

```
adapters/your-platform/
├── README.md           # Installation and configuration
├── hooks/              # Platform-specific hook handlers (if applicable)
├── config/             # Config files to copy (.mcp.json, etc.)
└── examples/           # Example configurations
```

### Hook Interface

At minimum, an adapter should handle:

```
on_message_received(message) → store in observer DB
on_message_sent(message) → store in observer DB
on_session_start() → trigger memory_context, inject MEMORY.md
on_session_end() → flush pending observations (optional)
```

The exact mechanism depends on the platform. OpenClaw uses hook files. Claude Code uses shell hooks. Some platforms might use file watchers or event streams.

### Testing

Include at least:
- A smoke test: does the adapter load without errors?
- A round-trip test: send a message, verify it's captured in the observer DB.
- Configuration validation: does it fail clearly if misconfigured?

### Submitting

1. Create a directory under `adapters/`
2. Implement the hook interface for your platform
3. Add a README with setup instructions
4. Test it end-to-end
5. Open a PR with a description of what platform it supports and how you tested it

---

## Adding MCP Tools

The MCP server lives at `core/mcp-server.py`. It follows a simple pattern:

### Tool Registration

Each tool is registered in the `TOOLS` list:

```python
TOOLS = [
    {
        "name": "memory_your_tool",
        "description": "One-line description of what it does",
        "inputSchema": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "What this param does"},
            },
            "required": ["param1"]
        }
    },
    # ... existing tools
]
```

### Tool Implementation

Add a function that handles the tool call:

```python
def tool_memory_your_tool(params: dict) -> str:
    """Implement the tool logic. Return a string result."""
    conn = get_db()
    # Do work...
    return json.dumps({"result": "whatever"})
```

### Wire It Up

In the `handle_tool_call` dispatcher:

```python
elif name == "memory_your_tool":
    result = tool_memory_your_tool(params)
```

### Guidelines

- **Return JSON strings.** The MCP protocol expects string content. JSON is structured and parseable.
- **Handle missing params gracefully.** Use `.get()` with defaults.
- **Don't block.** If your tool does heavy work, consider making it async or adding a timeout.
- **Add it to the README.** Update the MCP tools table in the root README.
- **Add eval test cases.** If your tool affects search or retrieval, add test cases to `evals/promptfooconfig.yaml`.

---

## Code Style

### Python (core/)
- Standard library where possible. Minimize dependencies.
- Type hints for function signatures.
- Docstrings for public functions.
- f-strings for formatting.
- No frameworks — the MCP server is a bare JSON-RPC handler over stdio.

### Node.js (observer/)
- ESM modules (`import`/`export`).
- No TypeScript in the observer — it's simple enough that JS is fine and avoids a build step.
- `node:` prefix for built-in modules.
- Minimal dependencies. Currently just `better-sqlite3` for DB access.

### Shell (context/)
- `set -euo pipefail` at the top.
- Comments explaining non-obvious commands.
- Portable where reasonable (macOS + Linux).

### General
- No linting config is enforced yet. Keep it readable.
- Commit messages: imperative mood, one line, specific. "Add Windsurf adapter" not "Updated some stuff."

---

## Testing

### Eval Suite (Retrieval Quality)

The primary test suite is the promptfoo eval in `evals/`. It tests whether searches return the right memories.

```bash
cd evals/
npx promptfoo eval
```

To add test cases, edit `promptfooconfig.yaml`. See [docs/maintenance.md](docs/maintenance.md#the-eval-suite) for the format.

### Observer Tests

The observer has its own test suite:

```bash
cd observer/
node test.js
```

Tests cover: observation extraction from sample conversations, reflector condensation, generation counter behavior, edge cases (empty conversations, single messages, malformed responses).

### Manual Testing

For adapter work, manual end-to-end testing is expected:

1. Set up the adapter with your platform
2. Have a short conversation
3. Verify messages appear in the observer DB
4. Verify `memory_search` returns relevant results
5. Verify context injection works at session start

Document your test steps in the adapter's README or PR description.

---

## Reporting Issues

### Good Issue Reports Include

- **What you expected** vs **what happened**
- **Platform and adapter** you're using
- **Steps to reproduce**
- **Relevant logs** (observer output, MCP server stderr, etc.)
- **Database state** if relevant (`./mem stats` output)

### Common Categories

- **Search quality** — "searched for X, expected Y, got Z." Include the query and expected results.
- **Observer behavior** — "observer didn't fire" or "observations are too vague." Include the conversation input and observer output.
- **Platform integration** — "adapter doesn't work with [platform version]." Include platform version and config.
- **Schema issues** — "migration failed" or "unexpected column." Include SQLite version and error message.

---

## What Needs Work

Areas where contributions are especially welcome:

- **New adapters** — Windsurf, Zed, VS Code + Continue, Cody, etc.
- **Observer model support** — adapting prompts for non-Anthropic models (OpenAI, local models via Ollama)
- **Embedding alternatives** — support for OpenAI embeddings, Cohere, or other providers alongside Ollama
- **Schema migrations** — as the schema evolves, we need a migration strategy
- **Better entity extraction** — the current regex approach is decent but could be smarter without adding LLM cost
- **Documentation** — especially platform-specific guides with screenshots and real-world examples
- **Windows support** — the shell scripts assume Unix. PowerShell equivalents would broaden the audience.

---

## License

MIT. Your contributions will be released under the same license.
