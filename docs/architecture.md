# Architecture Deep Dive

This document explains why moonshine is structured the way it is — the 3-tier model, the observer pipeline, the knowledge graph, and how they interact during a session.

---

## Why 3 Tiers (Not 1, Not 5)

The naive approach to agent memory is a single file — MEMORY.md or CLAUDE.md — that the agent reads and writes. It's simple, human-readable, and breaks down in predictable ways:

1. **It doesn't scale.** A file that fits in a context window today won't fit in six months.
2. **Search is grep.** No semantic similarity, no relationships, no ranking.
3. **The model has to write things down.** And it doesn't always. This is the #1 failure mode.

The overcorrected approach is a full database stack — Postgres, pgvector, Redis, a separate embedding service. It works, but nobody will self-host that for a personal agent.

Three tiers hit the sweet spot:

| Tier | Content | Size | Injection | Cost |
|------|---------|------|-----------|------|
| 🔴 Hot | Stable identity, patterns, rules | ~6-8K chars | Every session, always | $0 |
| 🟡 Warm | Calendar, recent events, projects | ~4K chars | Every session, auto-generated | $0 |
| 🔵 Cold | All memories, entities, relationships | Unlimited | On-demand via MCP tools | $0 (search is local) |

Why not 5 tiers? Because each tier boundary adds cognitive overhead — both for the agent deciding where to look and for the human maintaining the system. Three maps cleanly to "always know this / know this today / can look this up." Adding more tiers didn't improve retrieval quality in practice; it just added configuration.

### The "Less Is More" Insight

This was the biggest counterintuitive finding during development. Early versions injected ~16K characters of memory into every prompt. Output quality was noticeably worse than with ~6-8K of curated context plus on-demand retrieval.

This aligns with [research from Amazon](https://arxiv.org/abs/2501.13780) showing that agents with less context outperform agents with more context, as long as the right retrieval mechanisms exist. The hypothesis: large context preambles dilute the model's attention, making it harder to focus on what matters for the current task.

The practical takeaway: MEMORY.md should be tight. Put the essentials in hot memory. Let everything else live in cold storage where it can be retrieved when relevant.

---

## 🔴 Hot Tier: MEMORY.md

### What Goes Here
- **Identity:** Who the agent is, its role, its voice
- **Behavioral patterns:** Communication style, decision-making preferences
- **Hard rules:** Things the agent must always do or never do
- **Active projects:** What's currently in flight (brief, not detailed)
- **Key relationships:** Important people and their context

### What Doesn't Go Here
- **Ephemeral events:** "Had a meeting on Tuesday" — this belongs in cold storage
- **Detailed project notes:** Keep it to one-liners. Details go in memories.db.
- **Temporary states:** "Waiting for API key" — this will be stale in a week
- **Raw conversation logs:** That's what the observer pipeline is for

### Size Limits and Why

Target: **6-8K characters.** Hard ceiling: **~12K characters.**

Below 4K, the agent lacks enough context to be useful across sessions. Above 12K, you start seeing the context dilution effect — the agent "knows" a lot but struggles to apply any of it well.

The sweet spot varies by model. Larger context windows don't mean larger memory files are better. The constraint is attention, not capacity.

When MEMORY.md approaches the ceiling, prune old items to cold storage. See [maintenance.md](maintenance.md) for the archival workflow.

---

## 🟡 Warm Tier: CONTEXT.md

### How Auto-Generation Works

CONTEXT.md is rebuilt on a timer (cron, launchd, whatever scheduler you prefer) by `context/generate-context.sh`. It's a pure shell script — no LLM calls, no API costs.

```
┌──────────────────────────────────┐
│      generate-context.sh         │
│                                  │
│  Calendar API ──► Today's events │
│  Git log ──────► Recent commits  │
│  memories.db ──► Recent events   │
│  memories.db ──► Active lessons  │
│  MEMORY.md ───► Active projects  │
└──────────────────────────────────┘
            │
            ▼
       CONTEXT.md
```

### Data Sources

The default script pulls from:

1. **Calendar** — Today's schedule (via any calendar CLI or API)
2. **Git activity** — Recent commits across configured repos
3. **Recent events** — Last 7 days of events from memories.db (via the `mem` CLI)
4. **Active lessons** — High-importance lessons from memories.db
5. **Active projects** — Extracted from the MEMORY.md projects section

Each source is pluggable. Comment out what you don't use, add what you need. The script is designed to be forked and customized.

### Why Zero LLM Cost Matters

CONTEXT.md regenerates frequently — every 30 minutes in the reference implementation. If each regeneration cost even $0.01 in LLM calls, that's $14.40/month for something that can be done deterministically. The data sources are structured (calendar events, git logs, database queries). There's no summarization needed, just assembly.

---

## 🔵 Cold Tier: memories.db

### Schema Overview

SQLite with four key table groups:

**Memories** — The core records.
```sql
memories (
  id, type, title, content, metadata, tags,
  importance (1-5), source, source_date,
  archived_from, related_ids,
  created_at, updated_at
)
```

Types: `event`, `lesson`, `person`, `behavior`, `project`, `insight`, `decision`, `preference`, `skill`

**Full-Text Search** — FTS5 virtual table indexed on title, content, and tags. Supports phrase queries, boolean operators, and prefix matching. Triggers keep the FTS index in sync with the memories table automatically.

```sql
memories_fts USING fts5(title, content, tags, content='memories', content_rowid='id')
```

**Embeddings** — Vector embeddings stored as packed float arrays. Generated via Ollama's `nomic-embed-text` model (runs locally, 274MB). Used for semantic similarity search via cosine distance.

```sql
embeddings (
  memory_id REFERENCES memories(id),
  embedding BLOB,  -- packed float32 array
  model TEXT       -- which model generated it
)
```

**Knowledge Graph** — Entities (nodes) and relationships (edges) extracted from memories.

```sql
entities (
  id, name, type, aliases, description,
  first_seen, last_seen, memory_count
)

memory_entities (
  memory_id, entity_id, role, confidence
)

memory_edges (
  source_id, target_id, edge_type, weight
)
```

Entity types: `person`, `project`, `tool`, `concept`, `organization`
Edge types: `supports`, `contradicts`, `follows`, `related_to`, `caused_by`, etc.

### Search: Three Layers

Search uses a waterfall of three retrieval methods, each catching what the others miss:

1. **FTS5 keyword search** — Fast exact-match on title, content, tags. Includes acronym expansion (configurable mappings) and content LIKE fallback for partial matches.

2. **Semantic search** — Cosine similarity over Ollama embeddings. Catches "restaurants" → "dining spots" even without shared vocabulary. Relevance floor at 0.35 to filter noise.

3. **Graph traversal (spreading activation)** — Starting from entities mentioned in the query, walk the knowledge graph with decaying signal strength. "Alice" → connects to → "Project X" → surfaces memories about Project X that never mention Alice.

Results are ranked and deduplicated across all three layers.

---

## Observer Pipeline

### The Core Problem It Solves

In "model writes things down" systems, the model doesn't always write things down. It's busy doing the task. It forgets. It decides something isn't important enough. It writes a vague summary instead of the specific detail you need later.

The observer pipeline removes this dependency entirely. Inspired by [Mastra's observational memory pattern](https://mastra.ai/blog/memory-in-agents), it captures every conversation turn and extracts structured observations automatically.

### How It Works

```
Message In ──► Stored in messages table (permanent)
                    │
                    ▼
              Response sent (no latency added)
                    │
                    ▼ (async, post-response)
            Token count check
                    │
            ┌───────┴────────┐
            │ < threshold    │ ≥ threshold (~3K tokens)
            │ (do nothing)   │
            │                ▼
            │          ┌──────────┐
            │          │ Observer  │
            │          │ (Haiku)   │
            │          └────┬─────┘
            │               │
            │               ▼
            │     Structured observations:
            │     - content (one sentence, ≤200 chars)
            │     - priority (high/medium/low)
            │     - type (event/decision/lesson/preference/insight)
            │     - entities (extracted names)
            │     - observation_date
            │               │
            │               ▼
            │     Observation count check
            │          ┌────┴─────┐
            │          │ < thresh │ ≥ threshold (~4K tokens)
            │          │          │
            │          │          ▼
            │          │    ┌──────────┐
            │          │    │ Reflector │
            │          │    └────┬─────┘
            │          │         │
            │          │         ▼
            │          │   Condensed observations
            │          │   (generation counter incremented)
            │          │   Superseded observations tombstoned
            └──────────┴─────────┘
```

### Why Haiku (or Any Cheap Fast Model)

The observer and reflector prompts are designed for small, fast models. They don't need deep reasoning — they need to extract structured data from conversation logs. This is a classification and extraction task, not a creative one.

Using a cheap model matters because the observer fires frequently. With an expensive model, the cost of memory maintenance could exceed the cost of the conversations themselves. Haiku-class models (~$0.25/M input tokens) keep the total observer cost at roughly $3-9/month for active daily use.

The prompts are hardened with few-shot examples and strict output format requirements (JSON only, no markdown, no explanation). This compensates for the smaller model's tendency to be chatty.

### Generation Counter

Each observation has a generation number. Fresh observations from the observer are generation 0. After the reflector condenses them, the resulting observations are generation N+1 (where N is the highest generation among the inputs).

The generation counter is a rough proxy for durability. A generation-3 observation has survived three rounds of compression — it's been deemed important enough to keep each time. Higher-generation observations get priority in context assembly when space is constrained.

### Observer Database

Observations live in a separate `observations.db` (WAL mode for concurrent access), not in `memories.db`. This separation keeps the observer pipeline independent from the core memory system. You can disable the observer entirely and still have a fully functional memory system via manual saves.

---

## How It All Interacts: A Session Lifecycle

```
1. SESSION START
   ├── Load MEMORY.md (hot tier — always)
   ├── Load CONTEXT.md (warm tier — auto-generated)
   ├── Load active observations (recent compressed context)
   ├── Run memory_context (cold tier — recent + high-importance)
   └── Assemble context preamble

2. CONVERSATION
   ├── Each message stored permanently
   ├── Agent has MCP tools for active retrieval:
   │   memory_search, memory_surface, memory_entities, etc.
   └── Agent responds

3. POST-RESPONSE (async)
   ├── Observer checks token threshold
   ├── If threshold crossed → extract observations
   ├── Reflector checks observation count
   └── If threshold crossed → condense observations

4. SESSION END / CONTEXT COMPACTION
   ├── Flush pending observations
   ├── Agent may update MEMORY.md (if patterns changed)
   └── CONTEXT.md regenerates on next timer tick
```

The key design property: **steps 1 and 2 are synchronous and fast. Step 3 is async and never blocks the conversation.** Memory maintenance happens in the background. The user never waits for it.

---

## Design Decisions and Tradeoffs

### Why SQLite, Not Postgres
Portability. A SQLite database is a single file. You can copy it, back it up, move it between machines. No server process, no connection strings, no Docker containers. For a single-user personal agent, Postgres is overkill.

FTS5 gives good-enough full-text search. The embedding search is cosine similarity computed in Python — not as fast as pgvector, but fast enough for hundreds of memories. If you have tens of thousands, you might want to upgrade. We'll cross that bridge when someone gets there.

### Why Local Embeddings (Ollama), Not an API
Privacy and cost. Embedding every memory through an API means your memory content leaves your machine. With Ollama and `nomic-embed-text` (274MB), embeddings are generated locally in ~100ms each. No API calls, no cost, no data exfiltration.

The tradeoff: you need Ollama running. If that's not an option, the system falls back to FTS5-only search — still useful, just less powerful on semantic queries.

### Why Pattern-Based Entity Extraction, Not LLM
Cost and speed. Entity extraction runs on every `memory_save` call. Using an LLM would add latency and cost to every write. Regex patterns catch explicit mentions reliably (names, project titles, tool names). They miss implicit references ("he" → "John"), but the tradeoff is zero LLM cost for graph construction.

If you want LLM-powered extraction, the architecture supports it — swap the extraction function. But the default is fast, free, and good enough.

---

## Further Reading

- [Setup Guide](setup-guide.md) — Platform-specific installation
- [Maintenance Guide](maintenance.md) — Keeping memory healthy over time
- [Amazon research on agent context](https://arxiv.org/abs/2501.13780) — Why less context can mean better performance
- [Mastra's observational memory pattern](https://mastra.ai/blog/memory-in-agents) — Inspiration for the observer pipeline
- [Engram](https://github.com/tstockham96/engram) — Neuroscience-inspired knowledge graph patterns
