# 🥃 moonshine

**Locally distilled memory for AI agents.**

A 3-tier memory system that gives AI agents durable, searchable, self-compressing memory.

Works with Claude Code, OpenClaw, Cursor, or anything MCP-compatible. SQLite-based, runs locally, no cloud dependency.

```
┌──────────────────────────────────────────────────────────────────┐
│  Session Start                                                   │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────┐  │
│  │  MEMORY.md     │  │  CONTEXT.md    │  │  memories.db       │  │
│  │  🔴 Hot        |  |  🟡 Warm        │  │  🔵 Cold           │  │
│  │  ~6-8K chars   │  │  ~4K chars     │  │  unlimited         │  │
│  │  always        │  │  auto-gen      │  │  on-demand         │  │
│  │  injected      │  │  zero LLM $    │  │  MCP tools         │  │
│  └───────┬────────┘  └───────┬────────┘  └─────────┬──────────┘  │
│          └──────────────┬────┘                     │             │
│                         ▼                          │             │
│                Context Preamble ◄──────────────────┘             │
│                         │                                        │
│                         ▼                                        │
│                   Agent Response                                 │
│                         │                                        │
│                         ▼                                        │
│                Observer Pipeline                                 │
│           (auto-compress, extract,                               │
│            build knowledge graph)                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## The Problem

AI agents forget everything between sessions. The solutions out there are either:

- **Too simple** — a single markdown file that the model sometimes remembers to update. Hope-based persistence.
- **Too complex** — Postgres + pgvector + Redis stacks that nobody will self-host for a personal agent.
- **Too proprietary** — locked into one platform with no portability.

moonshine is the middle ground. It gives your agent a real memory system — searchable, self-compressing, with a knowledge graph — that runs on SQLite and works with any MCP-compatible tool.

The key insight: **the model shouldn't have to choose to remember things.** Every conversation is captured automatically. An observer pipeline compresses it into structured observations. A knowledge graph tracks entities and relationships. The agent gets relevant context without replaying entire transcripts.

## Built From Real Usage

This isn't a weekend prototype. It was extracted from a system that's been running daily for 7+ weeks, powering an agent handling engineering work, team coordination, personal tasks, and project management. Hundreds of memories, hundreds of entities, thousands of conversation turns — compressed, indexed, and retrievable.

The architecture evolved through actual failure modes: context windows overflowing, keyword search missing semantic connections, important facts falling through the cracks because the model didn't write them down. Each component exists because something broke without it.

---

## Architecture: 3 Tiers

### 🔴 Hot — MEMORY.md (~6-8K chars)
Stable identity, patterns, rules, preferences. Injected into every session. Think of it as working memory — the things you'd want the agent to know even with zero search results. Hand-curated or agent-maintained.

### 🟡 Warm — CONTEXT.md (~4K chars, auto-generated)
Today's calendar, recent events, active projects, git activity. Regenerated on a timer by a shell script — zero LLM cost. Dynamic but deterministic. The stuff that changes daily but doesn't need AI to assemble.

### 🔵 Cold — memories.db (unlimited, searchable on demand)
SQLite with FTS5 full-text search, vector embeddings (via Ollama), and a knowledge graph. Queried through MCP tools or CLI. This is where hundreds of memories live, searchable by keyword, semantic similarity, or graph traversal.

**Why 3 tiers?** Because [research from Amazon shows](https://arxiv.org/abs/2501.13780) that agents perform *worse* with more context — not better. Cramming 16K characters of memory into every prompt degrades output quality. The sweet spot is ~6-8K of curated context plus on-demand retrieval for everything else. Three tiers hit that balance. One tier can't scale. Five tiers add complexity without proportional benefit.

### Observer Pipeline

The observer eliminates the "model forgot to write it down" failure mode entirely. Inspired by [Mastra's observational memory pattern](https://mastra.ai/blog/memory-in-agents).

```
Every message ──► stored permanently
                      │
                      ▼ (token threshold crossed)
                 ┌──────────┐
                 │ Observer │ ──► structured observations
                 │ (Haiku)  │     (priority, type, entities)
                 └────┬─────┘
                      ▼ (observations accumulate)
                 ┌──────────┐
                 │ Reflector│ ──► condensed observations
                 │ (Haiku)  │     (generation counter tracks durability)
                 └──────────┘
```

Raw messages are stored permanently. When enough unobserved tokens accumulate (~3K), the observer fires — a cheap, fast model (Haiku) extracts structured observations with priority levels, types, and entity references. When observations pile up, the reflector condenses them further. A generation counter tracks how many rounds of compression each fact has survived — a rough proxy for importance.

The observer runs async, after the response is sent. Memory maintenance never adds latency to the conversation.

---

## Quick Start (5 minutes)

```bash
# 1. Clone
git clone https://github.com/cneiman/moonshine
cd moonshine

# 2. Install
./install.sh
# Creates memories.db, installs Python + Node deps,
# sets up Ollama embeddings (optional)

# 3. Connect to your tool

# Claude Code:
cp adapters/claude-code/.mcp.json ~/your-project/.mcp.json

# OpenClaw:
cp -r adapters/openclaw/hooks/ ~/your-workspace/hooks/
openclaw hooks enable conversation-observer

# Cursor:
# Add contents of adapters/cursor/mcp-config.json to Cursor settings

# 4. Go
# Your agent now has 9 memory tools via MCP.
# The observer captures conversations automatically.
# MEMORY.md is injected at session start.
```

See [docs/setup-guide.md](docs/setup-guide.md) for detailed platform instructions.

---

## MCP Tools (9)

The MCP server exposes these tools over stdio transport. Any MCP-compatible client can use them.

| Tool | What it does |
|------|-------------|
| `memory_context` | Load relevant memories at session start — recent, high-importance, project-specific |
| `memory_search` | Hybrid search: FTS5 keywords + semantic embeddings + knowledge graph traversal |
| `memory_save` | Persist a new memory with auto-embedding and entity extraction |
| `memory_briefing` | Structured session briefing assembled from stored data (zero LLM cost) |
| `memory_surface` | Proactive memory surfacing — find related memories via entity graph |
| `memory_entities` | List and query the knowledge graph (people, projects, tools, concepts) |
| `memory_connect` | Create typed edges between memories (supports, contradicts, follows, etc.) |
| `memory_neighbors` | Traverse graph neighbors of a memory — find what's connected |
| `memory_consolidate` | Find contradictions, merge duplicates, clean up drift |

Memory types: `event`, `lesson`, `person`, `behavior`, `project`, `insight`, `decision`, `preference`, `skill`

---

## CLI

```bash
cd moonshine/core

# Add a memory
./mem add "Switched to SQLite" --type decision --content "Moved from JSON to SQLite for FTS5 support" --importance 4

# Search (keyword)
./mem search "SQLite migration"

# Search (semantic — requires Ollama with nomic-embed-text)
./mem search "database decisions" --semantic

# List by type
./mem list --type lesson --since 2025-01-01

# Stats
./mem stats
```

---

## Knowledge Graph

Inspired by [Engram](https://github.com/tstockham96/engram) and neuroscience concepts of associative memory. Entities (people, projects, tools, concepts, organizations) are extracted from memories automatically using pattern-based extraction — zero LLM cost.

The graph enables retrieval by association. If you search for "Alice" and Alice is connected to Project X through the graph, memories about Project X surface even if they never mention Alice by name. This catches connections that keyword search and even semantic search miss.

Spreading activation traverses the graph from seed entities, decaying signal with each hop. The result: contextually relevant memories ranked by associative distance.

---

## How It Compares

| Feature | Vanilla MEMORY.md | Mem0 | LangMem | moonshine |
|---------|-------------------|------|---------|--------------|
| Storage | Markdown file | Cloud API | Postgres | SQLite (local) |
| Search | Keyword/grep | Semantic | Semantic | FTS5 + Semantic + Graph |
| Auto-capture | No (model must write) | Yes | Yes | Yes (observer) |
| Knowledge graph | No | No | No | Yes |
| Compression | No | No | No | Yes (observer + reflector) |
| MCP compatible | N/A | No | No | Yes (9 tools) |
| Self-hosted | Yes | No (cloud) | Requires infra | Yes |
| Eval suite | No | No | No | Yes (promptfoo) |
| Privacy | Local | Cloud | Depends | Local |
| Cost | $0 | $20+/mo | Infra cost | ~$3-9/mo (observer LLM calls only) |

---

## Platform Support

| Platform | Adapter | How it connects |
|----------|---------|----------------|
| **OpenClaw** | `adapters/openclaw/` | Hook handler for message events. Fire-and-forget capture. |
| **Claude Code** | `adapters/claude-code/` | SessionStart/PreCompact/PostToolUse hooks + `.mcp.json` |
| **Cursor** | `adapters/cursor/` | `.cursorrules` integration + MCP config |
| **Generic** | `adapters/generic/` | File watcher on session transcripts (JSONL). Works with any tool that writes logs. |

Adding a new adapter is straightforward — implement the hook interface for your platform's lifecycle events. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Project Structure

```
moonshine/
├── core/               # MCP server, CLI, SQLite schema
│   ├── mcp-server.py   # 9 MCP tools over stdio
│   ├── mem             # CLI for memory operations
│   └── schema.sql      # Database schema (FTS5, embeddings, knowledge graph)
├── observer/           # Conversation compression pipeline
│   ├── observe.js      # Extract observations from unobserved messages
│   ├── reflect.js      # Condense observations across generations
│   └── db.js           # Observer database access
├── context/            # Dynamic context generation
│   └── generate-context.sh  # Calendar + git + events → CONTEXT.md
├── adapters/           # Platform-specific integrations
│   ├── openclaw/
│   ├── claude-code/
│   ├── cursor/
│   └── generic/
├── templates/          # Starter files (MEMORY.md, SOUL.md, USER.md, AGENTS.md)
├── evals/              # promptfoo test suite for retrieval quality
└── docs/               # Architecture, setup, maintenance guides
```

---

## Eval Suite

Memory quality matters more than memory quantity. The eval suite uses [promptfoo](https://promptfoo.dev/) to test that searches return the right memories — not just that the system runs.

```bash
cd evals/
npx promptfoo eval    # Run retrieval tests
npx promptfoo view    # View results in browser
```

Test cases cover: people queries, project lookups, temporal questions, semantic similarity, acronym expansion, and graph traversal. Add your own by editing `promptfooconfig.yaml`.

---

## Limitations

Being honest about what this is and isn't:

- **Observer cost isn't zero.** The pipeline uses a cheap model (Haiku), but it's not free. Expect ~$3-9/month with active daily use. You can disable it and rely on manual memory saves if you prefer $0.
- **Embeddings need Ollama.** Semantic search requires a local embedding model running via Ollama. Without it, you get FTS5 keyword search only — still useful, but less powerful.
- **Single-user design.** This is built for one agent, one user. There's no multi-tenancy, no auth layer, no shared memory. It's a personal tool.
- **SQLite concurrency.** WAL mode handles most cases, but don't expect high-throughput concurrent writes. For a personal agent, this is a non-issue.
- **Knowledge graph is pattern-based.** Entity extraction uses regex patterns, not an LLM. It catches explicit mentions well but won't infer implicit entities. The tradeoff is zero LLM cost for graph construction.
- **No cloud sync.** Your memories live in local SQLite. Back them up however you back up your other files. See [docs/maintenance.md](docs/maintenance.md) for backup strategies.

---

## Design Philosophy

**Less is more.** The biggest counterintuitive finding: stuffing more context into prompts makes agents *worse*, not better. We went from 16K characters of injected memory down to ~6-8K with dynamic retrieval, and output quality improved measurably. This aligns with [Amazon's research on agent context](https://arxiv.org/abs/2501.13780) — agents with less but more relevant context outperform those drowning in information.

**Observe, don't depend on the model.** The #1 failure mode of "model writes things down" systems is that the model doesn't always write things down. The observer pipeline removes this dependency entirely. Every conversation is captured. Extraction is automatic.

**Local-first.** Your memories are yours. SQLite file on your disk. No cloud API, no telemetry, no data leaving your machine (except observer LLM calls, which you can route to a local model if you prefer).

**Neuroscience-inspired, not neuroscience-cosplaying.** The knowledge graph and spreading activation are inspired by how associative memory works in the brain. But we don't pretend this is a neural network. It's a pragmatic graph database that makes retrieval better.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add adapters, extend MCP tools, write tests, and report issues.

---

## License

MIT — do whatever you want with it.

---

*Built by someone who got tired of their AI forgetting everything. Extracted from a real system, not designed in a vacuum.*
