#!/usr/bin/env python3
"""
mem_pg.py — Postgres-backed moonshine memory CLI.

Drop-in replacement for core/mem.py when MOONSHINE_PG_DSN is set.

Implements the same CLI surface as mem.py:
  mem_pg add "Title" --type lesson --content "..." [--tags x,y] [--importance 3]
  mem_pg search "query" [--type X] [--limit 10] [--semantic]
  mem_pg list [--type X] [--since DATE] [--limit 20]
  mem_pg show ID
  mem_pg stats
  mem_pg entities [--type X]
  mem_pg entity NAME
  mem_pg neighbors ID [--depth N]
  mem_pg reindex

Environment:
  MOONSHINE_PG_DSN     PostgreSQL DSN (required)
  MOONSHINE_PG_SCHEMA  Schema name (default: moonshine)
  OLLAMA_URL           Ollama API URL (default: http://127.0.0.1:11434)
  EMBED_MODEL          Embedding model (default: nomic-embed-text)
"""

import argparse
import json
import os
import struct
import sys
from datetime import datetime
from typing import Optional

import requests

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG_VERSION = 2
except ImportError:
    try:
        import psycopg as psycopg2  # type: ignore[no-redef]
        import psycopg.rows as psycopg2_extras  # type: ignore[no-redef]
        _PSYCOPG_VERSION = 3
    except ImportError:
        print(
            "Error: psycopg2 or psycopg3 is required.\n"
            "  pip install psycopg2-binary\n"
            "or\n"
            "  pip install 'psycopg[binary]'",
            file=sys.stderr,
        )
        sys.exit(1)

# ============ Configuration ============

PG_DSN = os.environ.get("MOONSHINE_PG_DSN", "")
PG_SCHEMA = os.environ.get("MOONSHINE_PG_SCHEMA", "moonshine")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768

VALID_TYPES = [
    "event", "lesson", "person", "behavior", "project",
    "insight", "decision", "preference", "skill",
]


# ============ Database helpers ============

def get_conn():
    if not PG_DSN:
        print("Error: MOONSHINE_PG_DSN is not set.", file=sys.stderr)
        sys.exit(1)
    conn = psycopg2.connect(PG_DSN)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def _schema(table: str) -> str:
    """Return fully-qualified table name."""
    return f"{PG_SCHEMA}.{table}"


def _has_pgvector(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        return cur.fetchone() is not None


# ============ Embeddings ============

def get_embedding(text: str) -> Optional[bytes]:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        vec = resp.json()["embedding"]
        return struct.pack(f"{len(vec)}f", *vec)
    except Exception:
        return None


def unpack_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


# ============ CLI Commands ============

def cmd_add(args) -> None:
    conn = get_conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_schema('memories')}
                    (type, title, content, tags, importance, source, source_date)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id
                """,
                (
                    args.type,
                    args.title,
                    args.content,
                    json.dumps([t.strip() for t in (args.tags or "").split(",") if t.strip()]),
                    args.importance,
                    args.source,
                    args.source_date,
                ),
            )
            memory_id = cur.fetchone()["id"]

        # Embed asynchronously (best-effort)
        embedding = get_embedding(f"{args.title}\n{args.content}")
        if embedding:
            use_vector = _has_pgvector(conn)
            if use_vector:
                # pgvector expects list, not bytes
                vec = unpack_embedding(embedding)
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {_schema('embeddings')} (memory_id, embedding, model)
                        VALUES (%s, %s::vector, %s)
                        """,
                        (memory_id, str(vec), EMBED_MODEL),
                    )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {_schema('embeddings')} (memory_id, embedding, model)
                        VALUES (%s, %s, %s)
                        """,
                        (memory_id, psycopg2.Binary(embedding), EMBED_MODEL),
                    )

    print(f"Added memory #{memory_id}: {args.title}")
    conn.close()


def cmd_search(args) -> None:
    conn = get_conn()
    results = []

    with conn.cursor() as cur:
        # Semantic search (falls back to FTS if Ollama unavailable)
        if args.semantic:
            embedding = get_embedding(args.query)
            if embedding and _has_pgvector(conn):
                vec = unpack_embedding(embedding)
                cur.execute(
                    f"""
                    SELECT m.id, m.type, m.title, m.content, m.importance,
                           m.created_at, m.tags,
                           e.embedding <=> %s::vector AS distance
                    FROM {_schema('memories')} m
                    JOIN {_schema('embeddings')} e ON e.memory_id = m.id
                    WHERE (%s::text IS NULL OR m.type = %s)
                    ORDER BY distance ASC
                    LIMIT %s
                    """,
                    (str(vec), args.type, args.type, args.limit),
                )
                results = [dict(r) for r in cur.fetchall()]
            elif embedding:
                # Bytea fallback: fetch all and rank in Python
                cur.execute(
                    f"""
                    SELECT m.id, m.type, m.title, m.content, m.importance,
                           m.created_at, m.tags, e.embedding
                    FROM {_schema('memories')} m
                    JOIN {_schema('embeddings')} e ON e.memory_id = m.id
                    WHERE (%s::text IS NULL OR m.type = %s)
                    """,
                    (args.type, args.type),
                )
                rows = cur.fetchall()
                query_vec = unpack_embedding(embedding)
                scored = []
                for row in rows:
                    stored_vec = unpack_embedding(bytes(row["embedding"]))
                    sim = cosine_similarity(query_vec, stored_vec)
                    r = dict(row)
                    r.pop("embedding")
                    r["similarity"] = sim
                    scored.append(r)
                scored.sort(key=lambda r: r["similarity"], reverse=True)
                results = scored[: args.limit]

        # FTS fallback
        if not results:
            cur.execute(
                f"""
                SELECT id, type, title, content, importance, created_at, tags,
                       ts_rank(search_vec, plainto_tsquery('english', %s)) AS rank
                FROM {_schema('memories')}
                WHERE search_vec @@ plainto_tsquery('english', %s)
                  AND (%s::text IS NULL OR type = %s)
                ORDER BY rank DESC, importance DESC
                LIMIT %s
                """,
                (args.query, args.query, args.type, args.type, args.limit),
            )
            results = [dict(r) for r in cur.fetchall()]

    if not results:
        print("No results found.")
    for r in results:
        print(f"\n[{r['id']}] {r['title']} ({r['type']}, importance={r['importance']})")
        print(f"  {r['content'][:200]}")
    conn.close()


def cmd_list(args) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        since_clause = "AND created_at >= %s" if args.since else ""
        params = [args.type, args.type]
        if args.since:
            params.append(args.since)
        params.append(args.limit)
        cur.execute(
            f"""
            SELECT id, type, title, importance, created_at, tags
            FROM {_schema('memories')}
            WHERE (%s::text IS NULL OR type = %s)
            {since_clause}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    for r in rows:
        tags = json.loads(r["tags"] or "[]") if isinstance(r["tags"], str) else (r["tags"] or [])
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        print(f"[{r['id']}] {r['title']} ({r['type']}, imp={r['importance']}){tag_str}")
    conn.close()


def cmd_show(args) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {_schema('memories')} WHERE id = %s",
            (args.id,),
        )
        row = cur.fetchone()
    if not row:
        print(f"Memory #{args.id} not found.")
        sys.exit(1)
    for k, v in dict(row).items():
        print(f"{k}: {v}")
    conn.close()


def cmd_stats(args) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM {_schema('memories')}")
        total = cur.fetchone()["total"]
        cur.execute(
            f"SELECT type, COUNT(*) AS n FROM {_schema('memories')} GROUP BY type ORDER BY n DESC"
        )
        by_type = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) AS n FROM {_schema('embeddings')}")
        embeddings = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) AS n FROM {_schema('entities')}")
        entities = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) AS n FROM {_schema('memory_edges')}")
        edges = cur.fetchone()["n"]

    print(f"Total memories:  {total}")
    print(f"With embeddings: {embeddings}")
    print(f"Entities:        {entities}")
    print(f"Graph edges:     {edges}")
    print("\nBy type:")
    for r in by_type:
        print(f"  {r['type']:15s} {r['n']}")
    conn.close()


def cmd_entities(args) -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT name, type, memory_count, description
            FROM {_schema('entities')}
            WHERE (%s::text IS NULL OR type = %s)
            ORDER BY memory_count DESC
            LIMIT 50
            """,
            (args.type, args.type),
        )
        rows = cur.fetchall()
    for r in rows:
        desc = f" — {r['description'][:60]}" if r.get("description") else ""
        print(f"{r['name']} ({r['type']}, {r['memory_count']} memories){desc}")
    conn.close()


def cmd_reindex(args) -> None:
    """Rebuild all embeddings (re-embeds memories that lack embeddings)."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.id, m.title, m.content
            FROM {_schema('memories')} m
            LEFT JOIN {_schema('embeddings')} e ON e.memory_id = m.id
            WHERE e.memory_id IS NULL
            ORDER BY m.id
            """
        )
        rows = cur.fetchall()

    use_vector = _has_pgvector(conn)
    count = 0
    for row in rows:
        blob = get_embedding(f"{row['title']}\n{row['content']}")
        if not blob:
            continue
        try:
            with conn:
                with conn.cursor() as cur:
                    if use_vector:
                        vec = unpack_embedding(blob)
                        cur.execute(
                            f"""
                            INSERT INTO {_schema('embeddings')} (memory_id, embedding, model)
                            VALUES (%s, %s::vector, %s)
                            ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding
                            """,
                            (row["id"], str(vec), EMBED_MODEL),
                        )
                    else:
                        cur.execute(
                            f"""
                            INSERT INTO {_schema('embeddings')} (memory_id, embedding, model)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding
                            """,
                            (row["id"], psycopg2.Binary(blob), EMBED_MODEL),
                        )
            count += 1
            print(f"  Embedded #{row['id']}: {row['title'][:50]}", flush=True)
        except Exception as exc:
            print(f"  Error embedding #{row['id']}: {exc}", file=sys.stderr)

    print(f"\nReindexed {count} / {len(rows)} memories.")
    conn.close()


# ============ CLI wiring ============

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="moonshine memory CLI — Postgres backend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # add
    a = sub.add_parser("add", help="Add a new memory")
    a.add_argument("title")
    a.add_argument("--type", choices=VALID_TYPES, default="lesson")
    a.add_argument("--content", default="")
    a.add_argument("--tags", default="")
    a.add_argument("--importance", type=int, default=3)
    a.add_argument("--source")
    a.add_argument("--source-date", dest="source_date")

    # search
    s = sub.add_parser("search", help="Search memories")
    s.add_argument("query")
    s.add_argument("--type", choices=VALID_TYPES)
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--semantic", action="store_true")

    # list
    l = sub.add_parser("list", help="List memories")
    l.add_argument("--type", choices=VALID_TYPES)
    l.add_argument("--since")
    l.add_argument("--limit", type=int, default=20)

    # show
    sh = sub.add_parser("show", help="Show a memory")
    sh.add_argument("id", type=int)

    # stats
    sub.add_parser("stats", help="Database statistics")

    # entities
    en = sub.add_parser("entities", help="List entities")
    en.add_argument("--type")

    # reindex
    sub.add_parser("reindex", help="Rebuild embeddings for unindexed memories")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "add": cmd_add,
        "search": cmd_search,
        "list": cmd_list,
        "show": cmd_show,
        "stats": cmd_stats,
        "entities": cmd_entities,
        "reindex": cmd_reindex,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
