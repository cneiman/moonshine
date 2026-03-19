#!/usr/bin/env python3
"""
search_helper.py — Full moonshine retrieval pipeline for the LongMemEval harness.

Replaces the inline FTS-only search in harness.js with:
  1. FTS5 keyword search
  2. Semantic search (Ollama nomic-embed-text embeddings + cosine similarity)
  3. Hybrid merge (deduplicate, union top results)
  4. Temporal filtering (date range from query parsing)
  5. Cross-encoder reranking (opt-in)

Usage:
  python3 search_helper.py <db_path> <query> [options]

Options:
  --search fts|semantic|hybrid   Search mode (default: hybrid)
  --temporal                     Enable temporal filtering
  --rerank                       Enable cross-encoder reranking
  --limit N                      Max results to return (default: 20)
  --question-date DATE           Reference date for temporal parsing (YYYY/MM/DD format)

Output: JSON array of {id, content, source_date, metadata, score} to stdout.

Standalone debugging:
  python3 search_helper.py /path/to/eval.db "what did we discuss last week?" --temporal
"""

import argparse
import json
import math
import os
import sqlite3
import struct
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# Add core/ to path so we can import temporal parser
CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"
sys.path.insert(0, str(CORE_DIR))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# ── Embedding helpers ────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float] | None:
    """Get embedding from Ollama. Returns None on failure."""
    try:
        payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("embedding")
    except Exception as e:
        print(f"[search_helper] embedding error: {e}", file=sys.stderr)
        return None


def embedding_to_blob(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary blob (float32)."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def blob_to_embedding(blob: bytes) -> list[float]:
    """Unpack a binary blob back to a float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Ensure embeddings exist ─────────────────────────────────────────────────

def ensure_embeddings_table(conn: sqlite3.Connection):
    """Create the embeddings table if it doesn't exist (for eval DBs that lack it)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            memory_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            model TEXT NOT NULL DEFAULT 'nomic-embed-text',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()


def ingest_embeddings(conn: sqlite3.Connection):
    """Generate and store embeddings for all memories that don't have one yet."""
    ensure_embeddings_table(conn)

    # Find memories without embeddings
    rows = conn.execute("""
        SELECT m.id, m.content
        FROM memories m
        LEFT JOIN embeddings e ON e.memory_id = m.id
        WHERE e.memory_id IS NULL
    """).fetchall()

    if not rows:
        return 0

    count = 0
    for memory_id, content in rows:
        # Truncate very long content for embedding (nomic-embed-text context ~8k tokens)
        text = content[:4000] if content else ""
        emb = get_embedding(text)
        if emb:
            blob = embedding_to_blob(emb)
            conn.execute(
                "INSERT OR REPLACE INTO embeddings (memory_id, embedding, model) VALUES (?, ?, ?)",
                (memory_id, blob, EMBED_MODEL),
            )
            count += 1

    conn.commit()
    return count


# ── Search functions ─────────────────────────────────────────────────────────

def search_fts(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """FTS5 keyword search, same logic as harness.js."""
    sanitized = sanitize_fts_query(query)
    if not sanitized:
        return search_like(conn, query, limit)

    try:
        rows = conn.execute("""
            SELECT m.id, m.content, m.source_date, m.metadata, f.rank as score
            FROM memories_fts f
            JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH ?
            ORDER BY f.rank
            LIMIT ?
        """, (sanitized, limit)).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return search_like(conn, query, limit)


def search_like(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """LIKE fallback when FTS fails."""
    words = [w for w in query.lower().split() if len(w) > 3][:8]
    if not words:
        return []

    conditions = " OR ".join(f"LOWER(content) LIKE ?" for _ in words)
    params = [f"%{w}%" for w in words] + [limit]

    rows = conn.execute(f"""
        SELECT id, content, source_date, metadata, 0.0 as score
        FROM memories
        WHERE {conditions}
        ORDER BY source_date DESC
        LIMIT ?
    """, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_semantic(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Semantic search using Ollama embeddings + cosine similarity."""
    query_emb = get_embedding(query)
    if not query_emb:
        print("[search_helper] failed to embed query, falling back to FTS", file=sys.stderr)
        return search_fts(conn, query, limit)

    # Load all embeddings
    rows = conn.execute("""
        SELECT e.memory_id, e.embedding, m.content, m.source_date, m.metadata
        FROM embeddings e
        JOIN memories m ON m.id = e.memory_id
    """).fetchall()

    scored = []
    for memory_id, emb_blob, content, source_date, metadata in rows:
        mem_emb = blob_to_embedding(emb_blob)
        sim = cosine_similarity(query_emb, mem_emb)
        scored.append({
            "id": memory_id,
            "content": content,
            "source_date": source_date,
            "metadata": metadata,
            "score": round(sim, 4),
        })

    # Sort by similarity descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def search_hybrid(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Hybrid: run FTS5 + semantic, merge and deduplicate."""
    fts_results = search_fts(conn, query, limit)
    semantic_results = search_semantic(conn, query, limit)

    # Merge: use memory id to deduplicate
    seen_ids = set()
    merged = []

    # Add FTS results first (they have good keyword matches)
    for r in fts_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            merged.append(r)

    # Add semantic results that aren't already included
    for r in semantic_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            merged.append(r)

    # If we have semantic scores, boost FTS results that also appeared semantically
    semantic_scores = {r["id"]: r["score"] for r in semantic_results}
    for r in merged:
        if r["id"] in semantic_scores and r.get("score", 0) <= 0:
            # FTS result also found semantically — use semantic score
            r["score"] = semantic_scores[r["id"]]

    # Sort merged by score (semantic scores are positive, FTS ranks are negative)
    # Normalize: positive = better
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:limit]


def sanitize_fts_query(query: str) -> str | None:
    """Clean query for FTS5 MATCH."""
    import re
    words = re.sub(r"[^\w\s]", " ", query).split()
    words = [w for w in words if len(w) > 2]
    if not words:
        return None
    return " OR ".join(words)


# ── Temporal filtering ───────────────────────────────────────────────────────

def apply_temporal_filter(
    results: list[dict],
    query: str,
    question_date: str | None = None,
) -> list[dict]:
    """Filter results by temporal expressions parsed from the query."""
    try:
        from temporal import parse_temporal
    except ImportError:
        print("[search_helper] temporal.py not found, skipping temporal filter", file=sys.stderr)
        return results

    # Use question_date as reference time if provided
    ref_time = None
    if question_date:
        try:
            # Parse "2023/04/10 (Mon) 23:07" or "2023-04-10"
            cleaned = question_date.split("(")[0].strip().replace("/", "-")
            ref_time = datetime.strptime(cleaned, "%Y-%m-%d")
        except ValueError:
            try:
                ref_time = datetime.strptime(question_date[:10].replace("/", "-"), "%Y-%m-%d")
            except ValueError:
                pass

    parsed = parse_temporal(query, reference_time=ref_time)

    after = parsed.get("after")
    before = parsed.get("before")

    if not after and not before:
        return results

    print(f"[search_helper] temporal filter: after={after}, before={before}, expr={parsed.get('temporal_expr')}", file=sys.stderr)

    filtered = []
    for r in results:
        sd = r.get("source_date")
        if not sd:
            continue
        # Compare date strings (ISO format, lexicographic comparison works)
        if after and sd < after:
            continue
        if before and sd >= before:
            continue
        filtered.append(r)

    # If filtering removed everything, return unfiltered (better to have something)
    if not filtered:
        print("[search_helper] temporal filter removed all results, returning unfiltered", file=sys.stderr)
        return results

    return filtered


# ── Cross-encoder reranking ──────────────────────────────────────────────────

def apply_reranking(results: list[dict], query: str) -> list[dict]:
    """Rerank results using the cross-encoder from core/reranker.py."""
    # Add venv to path
    venv_sp = CORE_DIR / ".venv"
    if venv_sp.exists():
        for sp in venv_sp.glob("lib/python*/site-packages"):
            if str(sp) not in sys.path:
                sys.path.insert(0, str(sp))

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        print("[search_helper] sentence-transformers not available, skipping rerank", file=sys.stderr)
        return results

    model_name = os.environ.get("MOONSHINE_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    try:
        model = CrossEncoder(model_name)
        pairs = []
        for r in results:
            doc = r.get("content", "")[:1000]
            pairs.append([query, doc])

        scores = model.predict(pairs)

        for i, r in enumerate(results):
            r["rerank_score"] = round(float(scores[i]), 4)

        results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return results

    except Exception as e:
        print(f"[search_helper] reranking failed: {e}", file=sys.stderr)
        return results


# ── Helpers ──────────────────────────────────────────────────────────────────

def _row_to_dict(row: tuple) -> dict:
    """Convert a sqlite3 row tuple to a dict."""
    return {
        "id": row[0],
        "content": row[1],
        "source_date": row[2],
        "metadata": row[3],
        "score": row[4] if len(row) > 4 else 0.0,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Moonshine search pipeline for LongMemEval")
    parser.add_argument("db_path", help="Path to SQLite database")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--search", choices=["fts", "semantic", "hybrid"], default="hybrid",
                        help="Search mode (default: hybrid)")
    parser.add_argument("--temporal", action="store_true", help="Enable temporal filtering")
    parser.add_argument("--rerank", action="store_true", help="Enable cross-encoder reranking")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--question-date", help="Reference date for temporal parsing (YYYY/MM/DD...)")

    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = None  # use tuples

    # Step 1: Ensure embeddings if doing semantic/hybrid
    if args.search in ("semantic", "hybrid"):
        count = ingest_embeddings(conn)
        if count > 0:
            print(f"[search_helper] generated {count} embeddings", file=sys.stderr)

    # Step 2: Search
    if args.search == "fts":
        results = search_fts(conn, args.query, args.limit)
    elif args.search == "semantic":
        results = search_semantic(conn, args.query, args.limit)
    else:  # hybrid
        results = search_hybrid(conn, args.query, args.limit)

    # Step 3: Temporal filter
    if args.temporal:
        results = apply_temporal_filter(results, args.query, args.question_date)

    # Step 4: Rerank
    if args.rerank:
        results = apply_reranking(results, args.query)

    # Step 5: Trim to limit
    results = results[:args.limit]

    conn.close()

    # Output as JSON to stdout
    print(json.dumps(results))


if __name__ == "__main__":
    main()
