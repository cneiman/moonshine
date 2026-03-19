# Agent Memory: Competitive Landscape

> **Last updated:** 2026-03-18
> **Purpose:** Competitive research for positioning `moonshine` in the market
> **Sources:** Product docs, GitHub repos, research papers, comparison articles, pricing pages

---

## Table of Contents

1. [Market Overview](#market-overview)
2. [System-by-System Analysis](#system-by-system-analysis)
   - [Mem0](#1-mem0)
   - [LangMem](#2-langmem)
   - [Letta (MemGPT)](#3-letta-memgpt)
   - [Zep / Graphiti](#4-zep--graphiti)
   - [Mastra (Observational Memory)](#5-mastra-observational-memory)
   - [Cognee](#6-cognee)
   - [ChromaDB / Weaviate (Vector-Only)](#7-chromadb--weaviate-vector-only)
   - [Claude's Built-in Memory](#8-claudes-built-in-memory)
   - [ChatGPT's Memory](#9-chatgpts-memory)
   - [Emerging Players](#10-emerging-players)
3. [Comparison Matrix](#comparison-matrix)
4. [Architecture Patterns](#architecture-patterns)
5. [Benchmarks & Evaluation](#benchmarks--evaluation)
6. [Recent Research & Trends](#recent-research--trends)
7. [Where moonshine Fits](#where-moonshine-fits)

---

## Market Overview

Agent memory has become **the** critical infrastructure challenge for 2026. The space has exploded from a handful of experiments to a crowded field with VC-backed startups, framework integrations, and even native memory features from Anthropic and OpenAI.

**The core problem:** LLMs are stateless. Every session starts from zero. Context windows are finite, expensive, and ephemeral. Agent memory systems bridge this gap by extracting, storing, and retrieving knowledge across sessions.

**Two categories of memory have emerged:**
1. **Personalization memory** — remembering user preferences, conversation history, behavioral patterns
2. **Institutional knowledge memory** — accumulated operational knowledge, learned corrections, domain patterns that compound over time

Most frameworks started with #1 and are stretching toward #2. The harder problem is #2.

**Key market signals:**
- Mem0 raised $24M Series A (Oct 2025)
- Cognee raised €7.5M seed (Feb 2026)
- Letta raised $10M seed ($70M post-money valuation)
- GitHub Copilot built its own cross-agent memory system (Jan 2026)
- Anthropic made Claude memory free for all users (Mar 2026)
- Two separate "agent memory" repos trended on GitHub in a single day (Jan 2026)
- Survey paper "Memory in the Age of AI Agents" (arxiv:2512.13564) has become the reference taxonomy
- Vectorize.io's comparison articles dominate SEO (their Hindsight product is positioned aggressively)

---

## System-by-System Analysis

### 1. Mem0

**Website:** [mem0.ai](https://mem0.ai) | **GitHub:** ~48K stars | **License:** Apache 2.0
**Funding:** $24M Series A (Oct 2025, Y Combinator, Basis Set Ventures)

#### Architecture
- **What it stores:** Atomic memory facts extracted from conversations, scoped to users/sessions/agents
- **How it retrieves:** Semantic search via vector embeddings; knowledge graph traversal on Pro tier
- **Storage backend:** Pluggable — supports Qdrant, ChromaDB, Milvus, pgvector, Redis as vector backends. Neo4j for graph (Pro tier)
- **Pipeline:** Conversation → extraction pipeline → atomic facts + embeddings → vector store (+ graph on Pro)

#### Search Capabilities
- **Free/Standard:** Semantic (vector) search only
- **Pro ($249/mo):** Vector + knowledge graph ("Mem0g") with entity extraction and relationship traversal

#### Auto-capture vs Manual
- **Passive extraction** — you call `add()` with conversation data, Mem0's pipeline decides what facts to store. Developer controls inputs, system handles decomposition. Predictable and token-efficient.

#### MCP Support
- Community-built MCP servers exist (`mem0-mcp-selfhosted`, `mem0mcp`). Self-hosted MCP with Qdrant + Neo4j + Ollama is documented.

#### Self-hosted vs Cloud
- **Self-hosted:** Free (Apache 2.0), all core features. Graph implementation differs from managed Pro.
- **Cloud:** Managed platform with SOC 2 and HIPAA compliance

#### Pricing
| Tier | Price | Memories | Features |
|------|-------|----------|----------|
| Free | $0 | 10K | Basic vector search |
| Standard | $19/mo | 50K | Higher limits, priority support |
| Pro | $249/mo | Unlimited | Knowledge graph, advanced retrieval, SOC 2/HIPAA |

#### Strengths
- Largest community and ecosystem (~48K GitHub stars, 5,500+ forks)
- Framework-agnostic — works with LangChain, CrewAI, LlamaIndex, anything
- Python AND JavaScript SDKs
- Fastest time-to-first-memory (minutes)
- SOC 2 / HIPAA on managed platform
- Simple API surface (`add()`, `search()`)

#### Weaknesses
- Graph features paywalled behind $249/mo Pro tier — steep jump from $19
- **49.0% on LongMemEval** (independent eval) — significantly below other systems
- Self-reported LOCOMO results disputed by competitors
- Too simplistic for institutional knowledge without Pro tier
- No multi-strategy retrieval, no cross-encoder reranking

#### How moonshine compares
Mem0 is the market leader by adoption but has clear retrieval quality gaps. moonshine's 3-tier architecture (MEMORY.md hot → CONTEXT.md warm → memories.db cold) with FTS5 + semantic + knowledge graph + spreading activation is architecturally more sophisticated than Mem0's free tier. moonshine is fully local/self-hosted with zero cloud dependency. Mem0's advantage is ecosystem breadth and managed compliance.

---

### 2. LangMem

**Website:** [langchain-ai.github.io/langmem](https://langchain-ai.github.io/langmem) | **GitHub:** ~1.3K stars | **License:** MIT
**Created by:** LangChain team

#### Architecture
- **What it stores:** Key-value memory items in LangGraph's `BaseStore`, with embeddings for semantic search
- **How it retrieves:** Semantic search over stored items via configurable embedding models
- **Storage backend:** LangGraph's `BaseStore` — in-memory (`InMemoryStore`) or persistent via LangGraph Platform backends (PostgreSQL). No standalone storage.
- **Memory types:**
  - **Semantic (episodic):** Store/retrieve facts about users and interactions
  - **Procedural:** Save learned procedures as updated system instructions (agent "learns" better prompts)

#### Search Capabilities
- Semantic vector search via configurable embedding model (default: OpenAI text-embedding-3-small)
- Namespaced storage for organizing memories by type/user/purpose
- No keyword search, no graph, no temporal filtering

#### Auto-capture vs Manual
- **Tool-based (agentic):** Agent decides what to remember via `create_manage_memory_tool()` and searches via `create_search_memory_tool()`. Model must choose to invoke the tools.
- Also supports **background memory managers** that process conversations after-the-fact

#### MCP Support
- No native MCP support. Deeply coupled to LangGraph ecosystem.

#### Self-hosted vs Cloud
- **Self-hosted:** Free (MIT), but requires LangGraph infrastructure
- **Cloud:** Via LangGraph Platform (LangSmith) — pricing is per-trace, not per-memory

#### Pricing
- Open source (MIT) — free to self-host
- Cloud via LangGraph Platform pricing (usage-based)

#### Strengths
- Tight integration with LangGraph/LangChain ecosystem
- Procedural memory concept is unique — agent can improve its own instructions
- Memory enrichment/consolidation to balance precision and recall
- Well-documented conceptual guide
- Backed by LangChain's ecosystem momentum

#### Weaknesses
- **Heavy LangGraph lock-in** — requires LangGraph's `BaseStore`, not standalone
- Python-only
- Smallest community among major players (~1.3K stars)
- No multi-strategy retrieval (vector-only)
- No managed memory-specific cloud offering
- No graph capabilities

#### How moonshine compares
LangMem is tightly coupled to LangGraph — it's not a standalone memory system. moonshine is framework-agnostic with its own MCP server, CLI, and SQLite backend. LangMem's procedural memory concept (updating system prompts from experience) is interesting and somewhat paralleled by moonshine's MEMORY.md updates. moonshine has richer retrieval (FTS5 + semantic + graph) vs LangMem's vector-only search.

---

### 3. Letta (MemGPT)

**Website:** [letta.com](https://www.letta.com) | **GitHub:** ~21K stars | **License:** Apache 2.0
**Funding:** $10M seed (Felicis Ventures, $70M post-money). Backed by Jeff Dean, Clem Delangue, Ion Stoica.

#### Architecture
- **What it stores:** Three tiers inspired by operating system memory hierarchy:
  - **Core Memory** — small blocks always in context window (like RAM). Agent reads/writes directly.
  - **Recall Memory** — searchable conversation history outside context (like disk cache)
  - **Archival Memory** — long-term storage queried via tool calls (like cold storage)
- **How it retrieves:** Agent self-queries via tool calls against each memory tier
- **Storage backend:** PostgreSQL (managed cloud) or SQLite (self-hosted). Supports multiple embedding providers.
- **Key insight:** Agents actively manage their own context, deciding what to keep in core vs. archive.

#### Search Capabilities
- Agentic retrieval — the agent decides how to search (tool calls against each tier)
- Conversation search, archival queries, core memory reads
- No dedicated multi-strategy retrieval engine — retrieval quality depends on model judgment

#### Auto-capture vs Manual
- **Self-editing (agentic):** Agent decides what to remember and writes it using memory tools. If the model fails to save something, it's gone. Every memory operation costs inference tokens.

#### MCP Support
- MCP server available. Letta agents can be exposed as MCP tools.

#### Self-hosted vs Cloud
- **Self-hosted:** Free (Apache 2.0), all features included
- **Cloud:** $20-200/mo managed cloud with ADE (Agent Development Environment)

#### Pricing
| Tier | Price | Notes |
|------|-------|-------|
| Self-hosted | Free | All features, Apache 2.0 |
| Cloud | $20-200/mo | Managed, includes ADE |

#### Strengths
- Innovative OS-inspired architecture backed by peer-reviewed research paper
- Agents manage their own memory (not just passive storage)
- ADE (Agent Development Environment) for visual debugging and memory inspection
- Model-agnostic (OpenAI, Anthropic, Ollama, Vertex AI)
- Conversations API for shared memory across parallel experiences (Jan 2026)
- Strong academic backing

#### Weaknesses
- **You're adopting a runtime, not a library** — heavy commitment, high lock-in
- Steeper learning curve (hours to set up, not minutes)
- Python-only SDK
- Memory quality entirely depends on model judgment — no guaranteed extraction
- Every memory operation costs inference tokens
- No published LongMemEval benchmarks
- Switching away means rewriting agent infrastructure, not just swapping a library

#### How moonshine compares
Letta is a fundamentally different scope — it's an agent runtime, not a memory layer. moonshine is a standalone memory system that any agent can use. Both use tiered memory (moonshine's hot/warm/cold mirrors Letta's core/recall/archival), but moonshine doesn't require agents to run inside it. moonshine's approach of background extraction + explicit save is more predictable than Letta's model-dependent self-editing. Letta's ADE is better tooling than moonshine currently offers.

---

### 4. Zep / Graphiti

**Website:** [getzep.com](https://www.getzep.com) | **GitHub:** ~24K stars (Zep + Graphiti) | **License:** Graphiti: open source
**Research:** Published paper — arxiv:2501.13956

#### Architecture
- **What it stores:** Episodes (text/JSON) decomposed into entities, edges, and temporal attributes in a knowledge graph
- **How it retrieves:** Graph traversal + vector search. Every fact carries validity windows (when it became true, when superseded).
- **Storage backend:** Neo4j, FalkorDB, or Kuzu (embedded) for graph. Vector search integrated.
- **Key insight:** Time is a first-class dimension. Temporal edges indexed using interval trees.

#### Search Capabilities
- **Temporal knowledge graph** — can answer "who was the project lead in January?" differently from "who is the project lead now?"
- Graph traversal + vector similarity combined
- Structured business data integration with conversational history
- **63.8% on LongMemEval** (GPT-4o), with strong temporal reasoning scores

#### Auto-capture vs Manual
- **Automatic decomposition** — episodes are ingested and automatically broken into entities, edges, and temporal facts. No model decision required.

#### MCP Support
- Graphiti has community MCP integrations. Zep Cloud has API access.

#### Self-hosted vs Cloud
- **Self-hosted:** Zep Community Edition **deprecated**. Must self-host via Graphiti library directly (requires Neo4j/FalkorDB/Kuzu).
- **Cloud:** Zep Cloud — credit-based pricing

#### Pricing
| Tier | Price | Credits |
|------|-------|---------|
| Free | $0 | 1K credits/mo |
| Flex | $25/mo | 20K credits |
| Enterprise | Custom | Custom |

#### Strengths
- **Best temporal awareness in the space** — nothing else tracks fact evolution this well
- Peer-reviewed architecture
- Graphiti is genuinely open source and capable
- Strong for domains where entities/relationships change over time (CRM, compliance, medical)
- Sub-200ms retrieval latency claimed

#### Weaknesses
- Zep Community Edition deprecated — self-hosting requires raw Graphiti (heavier setup, need graph DB)
- Credit-based pricing requires careful usage estimation
- Minimal free tier (1K credits barely enough to prototype)
- Steeper learning curve than simpler memory layers
- Requires Neo4j or FalkorDB for graph storage — adds infrastructure complexity

#### How moonshine compares
Zep/Graphiti's temporal knowledge graph is the most sophisticated graph approach in the market. moonshine's knowledge graph (engram-inspired, pattern-based entity extraction + spreading activation) is lighter-weight and requires zero external graph database — it runs in SQLite. Zep is stronger on temporal reasoning; moonshine is stronger on zero-dependency self-hosted simplicity. Zep's deprecation of Community Edition is a risk signal for self-hosters.

---

### 5. Mastra (Observational Memory)

**Website:** [mastra.ai](https://mastra.ai) | **GitHub:** Growing rapidly | **License:** Open source
**Created by:** Team behind Gatsby (acquired by Netlify)

#### Architecture
- **What it stores:** Three tiers generated by background agents:
  1. **Recent messages** — exact conversation history for current task
  2. **Observations** — dense notes created by Observer agent when history exceeds 30K tokens
  3. **Reflections** — condensed observations when observations exceed 40K tokens
- **How it retrieves:** Observations injected into context window (not retrieved on-demand). Stable prefix enables prompt caching.
- **Storage backend:** PostgreSQL (`@mastra/pg`), LibSQL (`@mastra/libsql`), or MongoDB (`@mastra/mongodb`)
- **Key insight:** Compression, not retrieval. Background Observer/Reflector agents compress history into dense observations, achieving 5-40× compression.

#### Search Capabilities
- Not a search-based system — observations are appended to context, not searched
- Also supports semantic search for traditional memory retrieval
- **95% on LongMemEval** (their research page claim)

#### Auto-capture vs Manual
- **Fully automatic** — Observer watches conversations in background, creates observations without agent intervention. Zero manual effort.

#### MCP Support
- Mastra framework has MCP support broadly, but Observational Memory is tied to the Mastra agent framework.

#### Self-hosted vs Cloud
- Open source, self-hosted. Part of the Mastra agent framework.

#### Pricing
- Free (open source, part of Mastra framework)

#### Strengths
- **Brilliant compression approach** — makes small context windows behave like large ones
- Prompt caching friendly (stable observation prefix)
- Zero context rot — agent sees relevant info instead of noisy tool calls
- Fully automatic — no agent tooling required
- 95% on LongMemEval (claimed)
- VentureBeat coverage: "cuts AI agent costs 10x"
- TypeScript-native (from the Gatsby team)

#### Weaknesses
- **Tied to Mastra framework** — not standalone, can't just bolt onto any agent
- Observations are append-only in context, not searchable/retrievable
- Best for long-running conversations, less suited for cross-session institutional knowledge
- Requires fast background models (Gemini 2.5 Flash recommended)
- Newer approach, less battle-tested than vector/graph systems
- Not really "memory" in the traditional sense — more like intelligent context compression

#### How moonshine compares
Mastra's OM is solving a different problem — context window management within sessions via compression. moonshine is solving cross-session persistence and retrieval. They're complementary rather than competitive. The Observer/Reflector pattern is interesting for moonshine's session-level memory management. moonshine's CONTEXT.md (auto-generated every 30 min) serves a somewhat similar "warm context compression" role, but through explicit extraction rather than LLM-powered observation.

---

### 6. Cognee

**Website:** [cognee.ai](https://www.cognee.ai) | **GitHub:** ~12K stars | **License:** Open core
**Funding:** €7.5M (~$8.1M) seed (Feb 2026)

#### Architecture
- **What it stores:** Knowledge graph built from documents via ECL pipeline (Extract, Cognify, Load)
- **How it retrieves:** Combined time filters, graph traversal, and vector similarity
- **Storage backend:** SQLite (relational), LanceDB (vector), Kuzu (graph) by default — no external services required. Optionally Neo4j, FalkorDB, NetworkX for graph.
- **Pipeline:** Data → chunking → embedding generation → graph extraction (subject-relation-object triplets) → knowledge graph + vector index built in parallel

#### Search Capabilities
- Knowledge graph traversal
- Vector similarity search
- Time filtering
- Combined queries across all three
- 30+ data source connectors

#### Auto-capture vs Manual
- **Pipeline-based** — you feed documents/data in, Cognee's pipeline automatically extracts entities, relationships, and builds the graph.

#### MCP Support
- Yes — Cognee has MCP server support, n8n integration

#### Self-hosted vs Cloud
- **Self-hosted:** Free, runs fully local with zero cloud dependency
- **Cloud:** Newer managed offering, less battle-tested

#### Pricing
- Open source core is free
- Managed cloud pricing not widely published

#### Strengths
- 30+ data source connectors out of the box (documents, images, audio)
- Multimodal support (text, images, audio transcriptions)
- Runs fully locally — zero cloud dependency
- "Memory in 6 lines of code"
- Self-improving — learns from feedback, updates concepts
- Graduated from GitHub's Secure Open Source program
- Default storage (SQLite + LanceDB + Kuzu) requires no external services

#### Weaknesses
- Python-only
- Smaller community than Mem0 or Zep
- Cloud offering is newer and less proven
- Documentation could be more comprehensive
- More focused on document/knowledge extraction than conversational memory

#### How moonshine compares
Cognee and moonshine share a lot philosophically — both use SQLite as a core backend, both build knowledge graphs from data without external services, both focus on self-hosted/local-first. Cognee is more focused on document/data ingestion (30+ sources), while moonshine is optimized for agent conversation and session memory. Cognee's graph is more sophisticated (triplet extraction via LLM), while moonshine uses pattern-based entity extraction (zero LLM cost). Cognee's multimodal support is a differentiator moonshine lacks.

---

### 7. ChromaDB / Weaviate (Vector-Only)

**These are vector databases, not memory systems.** Including them because they're commonly used as the storage layer in DIY memory setups.

#### ChromaDB
- **GitHub:** ~20K+ stars | **License:** Apache 2.0
- **Architecture:** Embedded vector database. Store embeddings + metadata, query by similarity.
- **Best for:** Quick local dev, prototyping, simple RAG
- **Strengths:** Dead simple API, embeds in your process, no external service needed
- **Weaknesses:** Vector-only search, no entity extraction, no knowledge graph, no temporal reasoning, no memory pipeline
- **Pricing:** Free (open source)

#### Weaviate
- **GitHub:** ~14K+ stars | **License:** BSD-3-Clause
- **Architecture:** Vector + hybrid search (BM25 + vector). RESTful and GraphQL APIs.
- **Best for:** Production-scale vector search with filtering
- **Strengths:** Hybrid search, multi-tenancy, production-grade, multiple deployment options
- **Weaknesses:** More complex to operate, no built-in memory extraction or knowledge graph
- **Pricing:** Open source self-hosted; Weaviate Cloud from $25/mo

#### How moonshine compares
Vector databases are plumbing, not solutions. moonshine uses Ollama embeddings (nomic-embed-text) for semantic search, FTS5 for keyword search, and a custom knowledge graph — all in SQLite. No external vector database needed. Teams using ChromaDB/Weaviate for agent memory are effectively building their own memory system on top of a vector store — moonshine provides the full stack out of the box.

---

### 8. Claude's Built-in Memory

**Provider:** Anthropic | **Launched:** Sept 2025 (Team/Enterprise), Oct 2025 (Pro/Max), Mar 2026 (Free)

#### Architecture
- **What it stores:** High-level user facts and preferences extracted from conversations. Essentially a `CLAUDE.md` context file that gets iterated on over time.
- **How it retrieves:** Injected into system prompt at conversation start
- **Storage:** Cloud-only, Anthropic-managed
- **Memory import:** Can import memories from ChatGPT, Gemini (launched Mar 2026)

#### Auto-capture vs Manual
- **Automatic** — Claude extracts memories from conversations without user action
- Users can also say "Remember that I..." for explicit saves
- Viewable and editable in settings

#### Claude Code Memory
- Separate system: `MEMORY.md` files (auto-generated) in Claude Code for project-level memory
- CLAUDE.md, AGENTS.md patterns for agent configuration
- This is essentially the same pattern moonshine uses

#### Strengths
- Zero setup — just works for all Claude users
- Free for all users (as of Mar 2026)
- Memory import tool for switching from competitors
- Deeply integrated with Claude's personality/behavior

#### Weaknesses
- **Not available via API** — only in Claude.ai chat interface
- No programmatic access for agent developers
- Cloud-only, no self-hosting
- Opaque — limited control over what's stored
- Not designed for agent frameworks — it's a consumer feature
- No search, no graph, no temporal reasoning
- Memory limited in scope ("high-level preferences and details")

#### How moonshine compares
Claude's built-in memory is a consumer feature, not an agent infrastructure tool. It's not accessible via API and can't be used in custom agent systems. moonshine provides the programmatic, self-hosted, searchable memory system that Claude's native memory explicitly doesn't offer. Interestingly, Claude Code's `MEMORY.md` pattern is essentially what moonshine's `MEMORY.md` hot tier already does.

---

### 9. ChatGPT's Memory

**Provider:** OpenAI | **Launched:** Feb 2024 (limited), Apr 2025 (comprehensive upgrade)

#### Architecture
- **What it stores:** Four-layer architecture:
  1. **Model Set Context** — system-level behavioral instructions
  2. **Saved Memories** — explicit user facts ("I'm vegetarian")
  3. **Conversation History** — references to all past conversations (Apr 2025 upgrade)
  4. **Chat Context** — current conversation
- **How it retrieves:** Injected into system prompt. Past conversations referenced for relevance.
- **Storage:** Cloud-only, OpenAI-managed

#### Auto-capture vs Manual
- **Hybrid** — automatically extracts memories + user can explicitly say "Remember that..."
- Apr 2025 upgrade: now references ALL past conversations, not just saved memories

#### Strengths
- Most comprehensive consumer memory implementation
- References entire conversation history for personalization
- Automatic + explicit memory capture
- Deep integration with web search, tools
- Works across GPT-4o, o1, etc.

#### Weaknesses
- **Not available via API** — ChatGPT API has no memory functionality
- Cloud-only, no self-hosting
- Consumer-only feature
- "Not for exact templates or large blocks of verbatim text"
- Privacy concerns with full conversation history access
- Sometimes hallucinates memories from other sections of system prompt

#### How moonshine compares
Same story as Claude's memory — consumer feature, not developer infrastructure. OpenAI's API explicitly doesn't include memory. Teams building on the API must use Mem0, Zep, or similar. moonshine fills this exact gap for self-hosted, API-accessible agent memory.

---

### 10. Emerging Players

#### Hindsight (Vectorize.io)
- **GitHub:** ~4K stars (growing fast) | **License:** MIT
- **Funding:** $3.5M (Apr 2024)
- **Architecture:** Multi-strategy retrieval (semantic + BM25 + entity graph + temporal filtering) with cross-encoder reranking. Embedded PostgreSQL + pgvector.
- **Key differentiator:** `reflect` operation — LLM synthesizes across memories instead of just retrieving
- **Benchmark:** 91.4% on LongMemEval (highest published)
- **SDKs:** Python, TypeScript, Go
- **MCP:** First-class MCP design
- **Supports Ollama** for fully local deployments
- **Pricing:** Free self-hosted (Docker), usage-based cloud
- **Note:** Vectorize.io dominates comparison article SEO — they wrote most of the comparison pages ranking for "agent memory" queries. Take their benchmark claims with appropriate skepticism, though the LongMemEval result is independently verifiable.

#### SuperMemory
- **Website:** [supermemory.ai](https://supermemory.ai)
- **Architecture:** All-in-one platform combining memory + RAG + user profiles + data connectors
- **License:** Closed source (enterprise only for self-hosting)
- **MCP:** Yes — MCP server available, OpenClaw integration exists
- **Pricing:** Managed cloud, enterprise agreements for self-hosting
- **Notable:** Has OpenClaw-specific integration for long-term memory

#### OpenMemory (CaviraOSS)
- **GitHub:** Growing | **License:** Open source
- **Architecture:** Local persistent memory store for LLM apps (Claude Desktop, GitHub Copilot, Codex, etc.)
- **Key feature:** Migration tool — import from Mem0, Zep, SuperMemory
- **SDKs:** Python
- **Storage:** Local, embeddings + vector database
- **Focus:** Privacy-first, local-only, works with coding tools

#### A-MEM (Research - NeurIPS 2025)
- **Paper:** arxiv:2502.12110
- **Architecture:** Zettelkasten-inspired self-organizing memory. Agent dynamically organizes memories using interconnected notes with tags and links.
- **Key insight:** Memory organization should be agentic — the system itself decides how to structure memories, not the developer
- **Status:** Research paper with code, not a production system

#### GitHub Copilot Memory
- **Blog post:** Jan 2026
- **Architecture:** Cross-agent memory with citation-based verification
- **Key innovation:** Just-in-time verification — memories stored with code citations, verified at read time against current branch
- **Agents share memory:** Code review → coding agent → CLI (knowledge transfer between agents)
- **Privacy:** Memories scoped to repository, only write-permission contributors can create, read-permission users can access
- **Status:** Rolling out across Copilot agents

#### MAGMA (Research - 2026)
- **Paper:** Multi-Graph based Agentic Memory Architecture
- **Status:** Research, listed in "Memory in the Age of AI Agents" survey

---

## Comparison Matrix

| System | Architecture | Storage | Search | Auto-capture | MCP | Self-host | Cloud | Pricing | Stars |
|--------|-------------|---------|--------|-------------|-----|-----------|-------|---------|-------|
| **Mem0** | Vector + Graph | Qdrant/Chroma/pgvector + Neo4j | Semantic; Graph (Pro) | Passive extraction | Community | ✅ Free | ✅ | Free → $19 → $249/mo | ~48K |
| **LangMem** | Flat KV + Vector | LangGraph BaseStore | Semantic only | Agent tool calls | ❌ | ✅ Free | Via LangGraph | Free (MIT) | ~1.3K |
| **Letta** | 3-tier (Core/Recall/Archival) | PostgreSQL/SQLite | Agent tool calls | Self-editing | ✅ | ✅ Free | ✅ | Free → $20-200/mo | ~21K |
| **Zep/Graphiti** | Temporal Knowledge Graph | Neo4j/FalkorDB/Kuzu | Graph + Vector + Temporal | Auto decomposition | Community | Graphiti only | ✅ | Free (1K) → $25/mo | ~24K |
| **Mastra OM** | Observer/Reflector compression | PG/LibSQL/MongoDB | Context injection (not search) | Fully automatic | Via Mastra | ✅ Free | — | Free (OSS) | Growing |
| **Cognee** | KG + Vector pipeline | SQLite + LanceDB + Kuzu | Graph + Vector + Time | Pipeline auto | ✅ | ✅ Free | ✅ (newer) | Free (OSS) + cloud | ~12K |
| **ChromaDB** | Vector store | Embedded | Semantic only | Manual | ❌ | ✅ Free | — | Free (OSS) | ~20K+ |
| **Weaviate** | Vector + hybrid | Standalone | Semantic + BM25 | Manual | ❌ | ✅ Free | ✅ | Free → $25/mo | ~14K+ |
| **Claude Memory** | Context file | Cloud (Anthropic) | Injected to prompt | Automatic | ❌ | ❌ | ✅ | Free (all users) | N/A |
| **ChatGPT Memory** | 4-layer + history | Cloud (OpenAI) | History reference | Hybrid | ❌ | ❌ | ✅ | Included w/ sub | N/A |
| **Hindsight** | Multi-strategy hybrid | Embedded PG + pgvector | Semantic+BM25+Graph+Temporal | Fact extraction | ✅ | ✅ Free | ✅ | Free → usage-based | ~4K |
| **SuperMemory** | Memory + RAG platform | Cloud | Combined | Auto | ✅ | Enterprise | ✅ | Cloud pricing | — |
| **OpenMemory** | Local vector store | Local | Semantic | Manual | ✅ | ✅ Free | ❌ | Free (OSS) | Growing |
| **moonshine** | 3-tier (hot/warm/cold) | SQLite + FTS5 + Ollama | FTS5+Semantic+Graph+Spreading | Hybrid (auto-extract + manual) | ✅ (9 tools) | ✅ Free | ❌ | Free | — |

---

## Architecture Patterns

### Pattern 1: Passive Extraction (Mem0, Cognee)
Conversation data flows through an extraction pipeline that identifies facts, entities, and relationships. Developer controls what goes in; system handles decomposition. Predictable and token-efficient.

### Pattern 2: Self-Editing Agent (Letta, LangMem)
The agent itself decides what to remember via tool calls. More adaptive but memory quality depends on model judgment. Every memory operation costs inference tokens.

### Pattern 3: Compression/Observation (Mastra OM)
Background agents compress conversation history into dense observations and reflections. Not "memory" in traditional sense — more like intelligent context management.

### Pattern 4: Temporal Knowledge Graph (Zep/Graphiti)
Everything is entities + edges + timestamps. Facts carry validity windows. Best for domains where relationships change over time.

### Pattern 5: Multi-Strategy Hybrid (Hindsight, moonshine)
Multiple retrieval strategies (semantic, keyword, graph, temporal) run in parallel with reranking. Write-heavy extraction; read-optimized retrieval.

### Pattern 6: File-Based Memory (Claude Code, CLAUDE.md, moonshine MEMORY.md)
Markdown files as the memory interface. Human-readable, version-controllable, zero infrastructure. Growing trend in coding agents — "files are all you need" debate (Mar 2026). GitHub Copilot, Claude Code, and moonshine all converge on this pattern for hot/working memory.

### The Emerging Consensus
A March 2026 New Stack article captures an emerging pattern: **filesystem interface for what agents see, database storage for what persists.** This is exactly moonshine's architecture: MEMORY.md (file interface) backed by memories.db (persistent database).

---

## Benchmarks & Evaluation

### LongMemEval (Primary benchmark)
Tests long-term memory retrieval across temporal, multi-hop, and knowledge-update scenarios.

| System | Score | Notes |
|--------|-------|-------|
| Hindsight | 91.4% | Highest published (arxiv 2512.12818) |
| Mastra OM | 95% | Self-reported on research page |
| Zep | 63.8% | GPT-4o, strong on temporal |
| Mem0 | 49.0% | Independent eval (arxiv 2603.04814) |
| Letta | Not published | — |
| moonshine | 100% internal | 16 promptfoo test cases (not LongMemEval) |

**Important caveats:**
- LongMemEval only tests conversational data — doesn't evaluate agent workflow memory
- Mastra's 95% is self-reported and uses a different approach (compression, not retrieval)
- The benchmark is "increasingly saturated" per Vectorize.io's own admission
- moonshine's 16-test eval suite measures different things (retrieval from its own format)

### LoCoMo
Another de facto standard for conversational memory evaluation. Similar scope limitations.

### What's Missing
No benchmark currently tests:
- Whether memory actually helps agents perform tasks better over time
- Institutional knowledge accumulation and retrieval
- Cross-session learning effectiveness
- Memory system performance in agent workflow contexts (vs. chat contexts)

---

## Recent Research & Trends

### Key Papers (2025-2026)
1. **"Memory in the Age of AI Agents"** (arxiv:2512.13564, Dec 2025) — Comprehensive survey, becoming the reference taxonomy. Distinguishes agent memory from RAG and context engineering.
2. **"A-MEM: Agentic Memory for LLM Agents"** (NeurIPS 2025, arxiv:2502.12110) — Zettelkasten-inspired self-organizing memory. Agent dynamically organizes memories.
3. **"Zep: A Temporal Knowledge Graph Architecture"** (arxiv:2501.13956, Jan 2025) — Temporal KG for agent memory, outperforms MemGPT on DMR benchmark.
4. **"Memory for Autonomous LLM Agents"** (arxiv:2603.07670, Mar 2026) — Newest survey covering work from 2022 through early 2026.
5. **"From Storage to Experience"** (Preprints, Jan 2026) — Evolution from basic storage to experiential memory strategies.
6. **"MAGMA: Multi-Graph based Agentic Memory Architecture"** (Jan 2026) — Multi-graph approach.

### Trend 1: Files as Memory Interface
The "files are all you need" debate (Jan-Mar 2026) argues that filesystems make good agent interfaces. Claude Code's MEMORY.md, GitHub Copilot's memory, Dust.tt's synthetic filesystems, and moonshine's MEMORY.md all converge on this pattern. The emerging consensus: **files for what agents see, databases for what persists.**

### Trend 2: Memory as Infrastructure (Not Feature)
Memory is shifting from "nice to have" to required infrastructure. GitHub built memory into Copilot. Anthropic made memory free for all users. Multiple VC-backed startups exist solely for this problem.

### Trend 3: Multi-Strategy Retrieval
Single-strategy retrieval (vector-only) is increasingly recognized as insufficient. The "Memory in the Age of AI Agents" survey identifies multi-strategy retrieval with cross-encoder reranking as critical. Systems that only do vector search are at a disadvantage.

### Trend 4: MCP as the Standard Interface
MCP (Model Context Protocol) is becoming the standard way to expose memory to agents. Hindsight, Mem0, Cognee, SuperMemory, OpenMemory, and moonshine all support or plan MCP servers.

### Trend 5: Local-First / Privacy-First
Growing demand for self-hosted, fully local memory systems. Hindsight + Ollama, Cognee's zero-dependency local mode, OpenMemory's local-only approach, and moonshine's SQLite + Ollama stack all cater to this.

### What Anthropic Says About Memory
- Sept 2025: Memory for Team/Enterprise users
- Oct 2025: Automatic memory for Pro/Max users
- Mar 2026: Memory free for all users, plus memory import tool (from ChatGPT, Gemini)
- Claude Code: Auto-memory via MEMORY.md files (Feb 2026)
- Positioning memory as a competitive weapon against ChatGPT

### What OpenAI Says About Memory
- Apr 2025: Major memory upgrade — now references ALL past conversations
- Memory is integral to ChatGPT-6 vision (stateful assistant)
- API still has NO memory functionality — you need external tools
- Positioning memory as the path from "session-bound tool" to "long-term collaborator"

---

## Where moonshine Fits

### What moonshine Already Does Well

**Architecture strengths:**
- **3-tier memory (hot/warm/cold)** — MEMORY.md (hot, always in context, ~8K chars), CONTEXT.md (warm, auto-generated, ~4-6K chars), memories.db (cold, searchable, unlimited). This mirrors the industry consensus.
- **Hybrid retrieval** — FTS5 keyword search (with acronym expansion + LIKE fallback) + semantic search (Ollama nomic-embed-text, 0.35 relevance floor) + knowledge graph (spreading activation). Multi-strategy is the direction the industry is moving.
- **Knowledge graph** — 91 entities, typed edges, spreading activation. Zero LLM cost (pattern-based extraction). Most competitors require LLM calls for entity extraction.
- **SQLite everything** — No external database dependencies. memories.db is the entire backend. Cognee is the closest competitor in this regard (SQLite + LanceDB + Kuzu).
- **MCP server with 9 tools** — Rich tool surface matching or exceeding most competitors.
- **Fully local** — Ollama embeddings, SQLite storage, no cloud dependency. Matches the privacy-first trend.
- **Auto-extraction** — Nightly cron scans daily files → imports to SQLite. Hybrid approach (auto + manual `mem add`).
- **Eval suite** — 16 promptfoo test cases, 100% pass rate, weekly cron. More than most competitors ship.

**Unique differentiators:**
1. **File-based hot tier** — MEMORY.md and CONTEXT.md as human-readable, git-trackable memory. This is the exact pattern Claude Code, GitHub Copilot, and the "files are all you need" movement are converging on. moonshine was doing this before it was trendy.
2. **Zero LLM cost for knowledge graph** — Pattern-based entity extraction vs. competitors' LLM-based extraction. Saves tokens.
3. **CONTEXT.md auto-generation** — Calendar, events, projects, git activity compiled every 30 min via LaunchAgent. Zero-cost contextual awareness.
4. **Single-user, personal agent optimized** — While competitors target multi-user SaaS, moonshine is optimized for a single agent serving one person. Less overhead, more depth.
5. **Git-integrated daily memory** — `memory/YYYY-MM-DD.md` daily logs with append-only safety rules. Version-controlled memory history.

### Where moonshine Has Gaps

| Gap | Competitors Doing This | Priority |
|-----|----------------------|----------|
| **No managed cloud offering** | Mem0, Zep, Hindsight, SuperMemory | Low (personal tool) |
| **No TypeScript SDK** | Mem0 (JS), Hindsight (TS/Go/Py), Mastra (TS) | Medium |
| **No temporal reasoning** | Zep/Graphiti (first-class temporal) | Medium |
| **No cross-encoder reranking** | Hindsight | Medium-High |
| **No LongMemEval benchmark** | Hindsight (91.4%), Zep (63.8%), Mem0 (49%) | Medium (for credibility) |
| **No multi-modal support** | Cognee (images, audio, 30+ sources) | Low |
| **No synthesis/reflect operation** | Hindsight (`reflect` operation) | Medium |
| **No visual debugging tooling** | Letta (ADE) | Low |
| **Limited community** | Mem0 (~48K stars), Letta (~21K) | Low (personal tool) |
| **Python-only CLI/MCP** | Various multi-language offerings | Low |

### Positioning Recommendations

**moonshine occupies a unique niche:** A personal, local-first, file-native memory system for a single agent serving one person. This is distinct from every major competitor, which targets multi-user SaaS or developer frameworks.

**Closest competitors by approach:**
1. **Cognee** — Similar SQLite-based, local-first philosophy, but focused on document ingestion vs. agent conversation
2. **OpenMemory** — Similar local-first, MCP-based approach, but less sophisticated retrieval
3. **Hindsight** — Strongest on retrieval quality, but designed for multi-user/multi-agent scenarios

**If packaging for others, emphasize:**
- Zero external dependencies (SQLite + Ollama, nothing else)
- File-native memory (MEMORY.md pattern) — the emerging industry standard
- Multi-strategy retrieval (FTS5 + semantic + graph) in a single SQLite file
- Zero-cost knowledge graph (no LLM needed for entity extraction)
- MCP server with 9 tools (richer than most competitors)
- Privacy-first, self-hosted by design

**If improving for personal use, priorities:**
1. Add cross-encoder reranking to improve retrieval quality
2. Add temporal metadata to memories (valid_from, valid_until)
3. Run against LongMemEval for credible benchmark score
4. Consider a `reflect` operation (synthesize across memories via LLM)
5. Improve knowledge graph with relationship type inference

---

*This document reflects the competitive landscape as of March 2026. The agent memory space is evolving rapidly — expect significant changes quarterly.*
