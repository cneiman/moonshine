# moonshine — Postgres Adapter

A drop-in Postgres backend for moonshine's warm and cold memory tiers,
contributed by Sean Campbell / rudi193-cmd.

## Why

moonshine's default SQLite backend is excellent for single-user local use.
When you want to:

- Share memory across multiple machines or containers
- Run moonshine as a multi-agent system with concurrent writers
- Use Postgres features like row-level security, logical replication, or `pg_vector` embeddings
- Store memories in an existing Postgres instance alongside other data

...SQLite hits limits. This adapter provides the same schema and interface
backed by Postgres (psycopg2, compatible with psycopg3 in connection-string
mode).

## Requirements

```
psycopg2-binary>=2.9
```

Or `psycopg[binary]>=3.1` (both work).

## Quick start

```bash
# Set env vars instead of MOONSHINE_DB
export MOONSHINE_PG_DSN="postgresql://user:pass@localhost/moonshine"

# Run migrations
python adapters/postgres/migrate.py

# Use the adapter
python adapters/postgres/mem_pg.py add "Title" --type lesson --content "..."
python adapters/postgres/mem_pg.py search "query" --semantic
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MOONSHINE_PG_DSN` | (required) | Full Postgres DSN |
| `MOONSHINE_PG_SCHEMA` | `moonshine` | Postgres schema (namespace) |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama API URL for embeddings |
| `EMBED_MODEL` | `nomic-embed-text` | Embedding model name |

## Schema

The Postgres schema mirrors the SQLite schema exactly:
- `memories` — core memory records
- `embeddings` — pgvector embeddings (falls back to bytea if pgvector not installed)
- `entities` — named entities (people, projects, tools)
- `memory_entities` — junction table
- `memory_edges` — knowledge graph edges

Full-text search uses `tsvector` + `GIN` index instead of SQLite's `fts5`.

## Differences from SQLite backend

| Feature | SQLite | Postgres |
|---|---|---|
| Full-text search | `fts5` virtual table | `tsvector` + `GIN` index |
| Vector similarity | Manual cosine via Python | `<=>` operator if pgvector installed |
| Concurrency | WAL mode, single writer | Full MVCC, many writers |
| Schema | Flat file | Namespaced under `moonshine` schema |
| Auto-increment | `AUTOINCREMENT` | `BIGSERIAL` |
