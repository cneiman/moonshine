#!/usr/bin/env python3
"""
mem.py — Long-term memory CLI with semantic search via Ollama embeddings.

Part of moonshine: a 3-tier memory system for AI agents.
https://github.com/cneiman/moonshine

Usage:
    mem add "Title" --type lesson --content "..." [--tags x,y] [--importance 3]
    mem search "query" [--type X] [--limit 10] [--semantic]
    mem list [--type X] [--since DATE] [--limit 20]
    mem show ID
    mem edit ID [--title X] [--content X] [--importance N] [--add-tag X]
    mem delete ID
    mem link ID1 ID2
    mem related ID
    mem stats
    mem export [--format json|md]
    mem reindex  # rebuild all embeddings
    mem entities [--type X]
    mem entity NAME
    mem connect ID1 ID2 [--type edge_type]
    mem neighbors ID [--depth N]

Environment:
    MOONSHINE_DB       Path to memories.db (default: ./memories.db)
    OLLAMA_URL            Ollama API URL (default: http://127.0.0.1:11434)
    EMBED_MODEL           Embedding model (default: nomic-embed-text)
"""

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ============ Configuration ============

DB_PATH = Path(os.environ.get("MOONSHINE_DB", "./memories.db"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text dimension

VALID_TYPES = ['event', 'lesson', 'person', 'behavior', 'project', 'insight', 'decision', 'preference', 'skill']


def get_db() -> sqlite3.Connection:
    """Get database connection, initialize if needed."""
    db_exists = DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if not db_exists:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        print(f"Initialized database at {DB_PATH}", file=sys.stderr)

    return conn


def get_embedding(text: str) -> Optional[bytes]:
    """Get embedding from Ollama, return as packed floats."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30
        )
        resp.raise_for_status()
        embedding = resp.json()["embedding"]
        return struct.pack(f'{len(embedding)}f', *embedding)
    except Exception as e:
        print(f"Warning: Failed to get embedding: {e}", file=sys.stderr)
        return None


def unpack_embedding(blob: bytes) -> list[float]:
    """Unpack embedding from BLOB."""
    count = len(blob) // 4
    return list(struct.unpack(f'{count}f', blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (norm_a * norm_b)


# ============ Entity Functions ============

def extract_entities(content: str, title: str, tags: str) -> list[dict]:
    """Extract entities from memory content using DB-based lookup.
    Returns: [{"name": "...", "type": "...", "role": "mention", "confidence": 1.0}, ...]
    """
    text = f"{title} {content} {tags}".lower()
    found = {}

    # Check DB for known entities
    try:
        conn = get_db()
        db_entities = conn.execute("SELECT id, name, type, aliases FROM entities").fetchall()
        for row in db_entities:
            name = row['name']
            if name in found:
                continue
            aliases = json.loads(row['aliases']) if row['aliases'] else []
            all_names = [name.lower()] + [a.lower() for a in aliases]
            for a in all_names:
                if len(a) <= 2:
                    continue
                if len(a) <= 5:
                    if not re.search(r'\b' + re.escape(a) + r'\b', text):
                        continue
                if a in text:
                    found[name] = {
                        "name": name, "type": row['type'],
                        "role": "mention", "confidence": 0.9
                    }
                    break
    except Exception as e:
        print(f"Warning: entity DB lookup failed: {e}", file=sys.stderr)

    # Determine role: if entity appears in title, it's more likely the subject
    title_lower = title.lower()
    for name, entity in found.items():
        if name.lower() in title_lower or any(
            part.lower() in title_lower for part in name.split() if len(part) > 3
        ):
            entity["role"] = "subject"

    return list(found.values())


def ensure_entity(conn, name: str, etype: str, date: str = None) -> int:
    """Get or create an entity, return its ID."""
    row = conn.execute(
        "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) AND type = ?",
        (name, etype)
    ).fetchone()
    if row:
        eid = row['id']
        if date:
            conn.execute(
                "UPDATE entities SET last_seen = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (date, eid)
            )
        return eid

    now = date or datetime.now().strftime('%Y-%m-%d')
    aliases = [name.lower()]
    if etype == "person":
        parts = name.split()
        if len(parts) >= 2:
            if len(parts[0]) > 3:
                aliases.append(parts[0].lower())
            if len(parts[-1]) > 4:
                aliases.append(parts[-1].lower())

    cursor = conn.execute("""
        INSERT INTO entities (name, type, aliases, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
    """, (name, etype, json.dumps(aliases), now, now))
    return cursor.lastrowid


def link_memory_entities(conn, memory_id: int, entities: list[dict], date: str = None):
    """Link extracted entities to a memory. Creates entities if needed."""
    for ent in entities:
        eid = ensure_entity(conn, ent["name"], ent["type"], date)
        try:
            conn.execute("""
                INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role, confidence)
                VALUES (?, ?, ?, ?)
            """, (memory_id, eid, ent.get("role", "mention"), ent.get("confidence", 1.0)))
        except Exception:
            pass
    conn.execute("""
        UPDATE entities SET memory_count = (
            SELECT COUNT(*) FROM memory_entities WHERE entity_id = entities.id
        ), updated_at = datetime('now','localtime')
        WHERE id IN (
            SELECT entity_id FROM memory_entities WHERE memory_id = ?
        )
    """, (memory_id,))


def auto_create_edges(conn, memory_id: int, entity_ids: list[int]):
    """Create relates_to edges for memories sharing 3+ entities with this one."""
    if len(entity_ids) < 2:
        return 0

    placeholders = ','.join('?' * len(entity_ids))
    rows = conn.execute(f"""
        SELECT me.memory_id, COUNT(*) as shared
        FROM memory_entities me
        WHERE me.entity_id IN ({placeholders})
        AND me.memory_id != ?
        GROUP BY me.memory_id
        HAVING COUNT(*) >= 3
    """, entity_ids + [memory_id]).fetchall()

    edges_created = 0
    for row in rows:
        other_id = row['memory_id']
        shared = row['shared']
        my_count = len(entity_ids)
        other_count = conn.execute(
            "SELECT COUNT(*) FROM memory_entities WHERE memory_id = ?", (other_id,)
        ).fetchone()[0]
        weight = shared / max(my_count, other_count) if max(my_count, other_count) > 0 else 0.5

        try:
            conn.execute("""
                INSERT OR IGNORE INTO memory_edges (source_id, target_id, edge_type, weight)
                VALUES (?, ?, 'relates_to', ?)
            """, (memory_id, other_id, round(weight, 3)))
            edges_created += 1
        except Exception:
            pass

    return edges_created


# ============ Search Helpers ============

# Minimum cosine similarity threshold — below this, results are considered irrelevant
SEMANTIC_RELEVANCE_FLOOR = 0.35

# Expand known acronyms to improve search recall.
# Add your own domain-specific acronyms here.
ACRONYMS = {
    'MCP': 'MCP model context protocol',
    'FTS': 'FTS full text search',
}


def expand_acronyms(query: str) -> str:
    """Expand known acronyms to improve search recall."""
    expanded = query
    for acronym, expansion in ACRONYMS.items():
        if acronym.lower() in query.lower():
            expanded = query + ' ' + expansion
            break
    return expanded


def _fts_search(conn, query: str, type_filter: str = None, limit: int = 10):
    """Run FTS5 search, return list of (score, row_dict)."""
    words = list(set(w for w in query.split() if len(w) > 1))
    if len(words) > 1:
        fts_query = ' OR '.join(f'"{w}"' for w in words[:8])
    else:
        fts_query = f'"{query}"' if '-' in query else query

    sql = """
        SELECT m.*, fts.rank
        FROM memories m
        JOIN memories_fts fts ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
    """
    params = [fts_query]

    if type_filter:
        sql += " AND m.type = ?"
        params.append(type_filter)

    sql += " ORDER BY fts.rank LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
        return [(None, dict(r)) for r in rows]
    except Exception:
        return []


def recall_with_spread(conn, query: str, limit: int = 10, spread: bool = True,
                       max_hops: int = 2, decay: float = 0.5,
                       type_filter: str = None, semantic: bool = False) -> list:
    """Search with optional spreading activation over graph edges.
    Returns list of (score, row_dict).
    """
    seed_results = []
    expanded_query = expand_acronyms(query)

    if semantic:
        query_embedding = get_embedding(expanded_query)
        if query_embedding:
            query_vec = unpack_embedding(query_embedding)
            rows = conn.execute("""
                SELECT m.*, e.embedding FROM memories m
                JOIN embeddings e ON m.id = e.memory_id
                WHERE (? IS NULL OR m.type = ?)
            """, (type_filter, type_filter)).fetchall()
            for row in rows:
                vec = unpack_embedding(row['embedding'])
                score = cosine_similarity(query_vec, vec)
                if score >= SEMANTIC_RELEVANCE_FLOOR:
                    seed_results.append((score, dict(row)))
            seed_results.sort(key=lambda x: x[0], reverse=True)
            seed_results = seed_results[:limit * 2]
    else:
        fts_results = _fts_search(conn, expanded_query, type_filter, limit * 2)
        if not fts_results:
            fts_results = _fts_search(conn, query, type_filter, limit * 2)
        seed_results = [(1.0, r) for _, r in fts_results]

    if not spread or not seed_results:
        return seed_results[:limit]

    # Spread activation
    all_results = {r['id']: (score, r) for score, r in seed_results}

    for seed_score, seed_row in seed_results[:limit]:
        seed_id = seed_row['id']
        visited = {seed_id}
        frontier = [(seed_id, 0)]

        while frontier:
            current_id, hop = frontier.pop(0)
            if hop >= max_hops:
                continue

            neighbors = conn.execute("""
                SELECT m.*, e.weight, e.edge_type,
                    CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END as neighbor_id
                FROM memory_edges e
                JOIN memories m ON m.id = (CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END)
                WHERE (e.source_id = ? OR e.target_id = ?)
            """, (current_id, current_id, current_id, current_id)).fetchall()

            for row in neighbors:
                nid = row['neighbor_id']
                if nid in visited:
                    continue
                if type_filter and row['type'] != type_filter:
                    continue
                visited.add(nid)

                spread_score = seed_score * (decay ** (hop + 1)) * row['weight']
                row_dict = dict(row)
                row_dict['_spread'] = True
                row_dict['_hops'] = hop + 1

                if nid in all_results:
                    if spread_score > all_results[nid][0]:
                        all_results[nid] = (spread_score, row_dict)
                else:
                    all_results[nid] = (spread_score, row_dict)
                    frontier.append((nid, hop + 1))

    results = sorted(all_results.values(), key=lambda x: x[0], reverse=True)
    return results[:limit]


# ============ Commands ============

def cmd_add(args):
    """Add a new memory (with dedup check)."""
    conn = get_db()

    existing = conn.execute(
        "SELECT id, title FROM memories WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
        (args.title,)
    ).fetchone()
    if existing and not getattr(args, 'force', False):
        print(f"Duplicate detected: #{existing['id']} '{existing['title']}' — skipping (use --force to override)", file=sys.stderr)
        return

    tags_json = json.dumps(args.tags.split(',') if args.tags else [])
    metadata_json = args.metadata or '{}'

    cursor = conn.execute("""
        INSERT INTO memories (type, title, content, tags, metadata, importance, source, source_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (args.type, args.title, args.content, tags_json, metadata_json,
          args.importance, args.source, args.source_date))

    memory_id = cursor.lastrowid

    # Generate embedding
    embed_text = f"{args.title}\n\n{args.content}"
    embedding = get_embedding(embed_text)
    if embedding:
        conn.execute("""
            INSERT INTO embeddings (memory_id, embedding, model)
            VALUES (?, ?, ?)
        """, (memory_id, embedding, EMBED_MODEL))

    # Extract and link entities
    try:
        manual_entities = []
        if hasattr(args, 'entities') and args.entities:
            for ename in args.entities.split(','):
                ename = ename.strip()
                if ename:
                    manual_entities.append({"name": ename, "type": "concept", "role": "subject", "confidence": 1.0})

        auto_entities = extract_entities(args.content, args.title, args.tags or "")
        seen = {e["name"].lower() for e in manual_entities}
        all_entities = manual_entities + [e for e in auto_entities if e["name"].lower() not in seen]

        if all_entities:
            link_memory_entities(conn, memory_id, all_entities, args.source_date)
            entity_ids = []
            for ent in all_entities:
                row = conn.execute(
                    "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) AND type = ?",
                    (ent["name"], ent["type"])
                ).fetchone()
                if row:
                    entity_ids.append(row['id'])
            if entity_ids:
                edges = auto_create_edges(conn, memory_id, entity_ids)
                if edges:
                    print(f"  Auto-created {edges} edges")
            entity_names = [e["name"] for e in all_entities]
            print(f"  Entities: {', '.join(entity_names)}")
    except Exception as e:
        print(f"  Warning: entity extraction failed: {e}", file=sys.stderr)

    conn.commit()
    print(f"Added memory #{memory_id}: {args.title}")


def cmd_search(args):
    """Search memories using FTS and/or semantic search."""
    conn = get_db()
    results = []

    expanded_query = expand_acronyms(args.query)

    if args.semantic:
        query_embedding = get_embedding(expanded_query)
        if not query_embedding:
            print("Error: Could not generate query embedding", file=sys.stderr)
            sys.exit(1)

        query_vec = unpack_embedding(query_embedding)

        rows = conn.execute("""
            SELECT m.*, e.embedding
            FROM memories m
            JOIN embeddings e ON m.id = e.memory_id
            WHERE (? IS NULL OR m.type = ?)
        """, (args.type, args.type)).fetchall()

        scored = []
        for row in rows:
            vec = unpack_embedding(row['embedding'])
            score = cosine_similarity(query_vec, vec)
            if score >= SEMANTIC_RELEVANCE_FLOOR:
                scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [(s, dict(r)) for s, r in scored[:args.limit]]
    else:
        fts_results = _fts_search(conn, expanded_query, args.type, args.limit)
        if not fts_results:
            fts_results = _fts_search(conn, args.query, args.type, args.limit)

        if len(fts_results) < args.limit:
            like_query = f"%{args.query}%"
            existing_ids = list({r['id'] for _, r in fts_results})
            if existing_ids:
                placeholders = ','.join('?' * len(existing_ids))
                not_in_clause = f"AND m.id NOT IN ({placeholders})"
            else:
                not_in_clause = ""
                existing_ids = []
            like_sql = f"""
                SELECT m.*, NULL as rank
                FROM memories m
                WHERE (m.content LIKE ? OR m.title LIKE ? OR m.tags LIKE ?)
                {not_in_clause}
            """
            like_params = [like_query, like_query, like_query] + existing_ids
            if args.type:
                like_sql += " AND m.type = ?"
                like_params.append(args.type)
            like_sql += " ORDER BY m.importance DESC LIMIT ?"
            like_params.append(args.limit - len(fts_results))
            like_rows = conn.execute(like_sql, like_params).fetchall()
            fts_results.extend([(None, dict(r)) for r in like_rows])

        results = fts_results

    # Spreading activation if requested
    if getattr(args, 'spread', False) and results:
        spread_results = recall_with_spread(
            conn, args.query, limit=args.limit, spread=True,
            max_hops=getattr(args, 'hops', 2), decay=0.5,
            type_filter=args.type, semantic=args.semantic
        )
        seen_ids = set()
        merged = []
        for score, row in spread_results:
            rid = row.get('id')
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                merged.append((score, row))
        results = merged[:args.limit]

    # Output
    if args.format == 'json':
        output = [{"score": s, **r} for s, r in results]
        for o in output:
            o.pop('embedding', None)
        print(json.dumps(output, indent=2, default=str))
    else:
        for score, row in results:
            spread_marker = " 🔗" if row.get('_spread') else ""
            score_str = f" [{score:.3f}]" if score else ""
            print(f"#{row['id']} [{row['type']}] {row['title']}{score_str}{spread_marker}")
            if args.verbose:
                print(f"   {row['content'][:100]}...")
                print(f"   Tags: {row['tags']} | Importance: {row['importance']} | Date: {row['source_date']}")
            print()


def cmd_list(args):
    """List memories with optional filters."""
    conn = get_db()

    sql = "SELECT * FROM memories WHERE 1=1"
    params = []

    if args.type:
        sql += " AND type = ?"
        params.append(args.type)
    if args.since:
        sql += " AND (source_date >= ? OR created_at >= ?)"
        params.extend([args.since, args.since])
    if args.tag:
        sql += " AND tags LIKE ?"
        params.append(f'%"{args.tag}"%')
    if args.min_importance:
        sql += " AND importance >= ?"
        params.append(args.min_importance)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()

    if args.format == 'json':
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    else:
        for row in rows:
            date_str = row['source_date'] or row['created_at'][:10]
            print(f"#{row['id']} [{row['type']}] {row['title']} ({date_str}) ★{row['importance']}")


def cmd_show(args):
    """Show full details of a memory."""
    conn = get_db()

    row = conn.execute("SELECT * FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Memory #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    row = dict(row)

    if args.format == 'json':
        print(json.dumps(row, indent=2, default=str))
    else:
        print(f"# {row['title']} (#{row['id']})")
        print(f"Type: {row['type']} | Importance: {row['importance']}")
        print(f"Source: {row['source']} | Date: {row['source_date']}")
        print(f"Tags: {row['tags']}")
        print(f"Created: {row['created_at']} | Updated: {row['updated_at']}")
        print()
        print(row['content'])

        if row['related_ids'] != '[]':
            print(f"\nRelated: {row['related_ids']}")


def cmd_edit(args):
    """Edit an existing memory."""
    conn = get_db()

    row = conn.execute("SELECT * FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Memory #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    updates = []
    params = []

    if args.title:
        updates.append("title = ?")
        params.append(args.title)
    if args.content:
        updates.append("content = ?")
        params.append(args.content)
    if args.importance:
        updates.append("importance = ?")
        params.append(args.importance)
    if args.add_tag:
        current_tags = json.loads(row['tags'])
        current_tags.append(args.add_tag)
        updates.append("tags = ?")
        params.append(json.dumps(current_tags))
    if args.remove_tag:
        current_tags = json.loads(row['tags'])
        if args.remove_tag in current_tags:
            current_tags.remove(args.remove_tag)
        updates.append("tags = ?")
        params.append(json.dumps(current_tags))

    if not updates:
        print("No changes specified", file=sys.stderr)
        sys.exit(1)

    params.append(args.id)
    conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)

    if args.title or args.content:
        new_row = conn.execute("SELECT title, content FROM memories WHERE id = ?", (args.id,)).fetchone()
        embed_text = f"{new_row['title']}\n\n{new_row['content']}"
        embedding = get_embedding(embed_text)
        if embedding:
            conn.execute("""
                INSERT OR REPLACE INTO embeddings (memory_id, embedding, model)
                VALUES (?, ?, ?)
            """, (args.id, embedding, EMBED_MODEL))

    conn.commit()
    print(f"Updated memory #{args.id}")


def cmd_delete(args):
    """Delete a memory."""
    conn = get_db()

    row = conn.execute("SELECT title FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Memory #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    if not args.force:
        confirm = input(f"Delete '#{args.id} {row['title']}'? [y/N] ")
        if confirm.lower() != 'y':
            print("Cancelled")
            return

    conn.execute("DELETE FROM memories WHERE id = ?", (args.id,))
    conn.execute("DELETE FROM embeddings WHERE memory_id = ?", (args.id,))
    conn.commit()
    print(f"Deleted memory #{args.id}")


def cmd_link(args):
    """Link two memories."""
    conn = get_db()

    row1 = conn.execute("SELECT related_ids FROM memories WHERE id = ?", (args.id1,)).fetchone()
    row2 = conn.execute("SELECT related_ids FROM memories WHERE id = ?", (args.id2,)).fetchone()

    if not row1 or not row2:
        print("One or both memories not found", file=sys.stderr)
        sys.exit(1)

    related1 = json.loads(row1['related_ids'])
    related2 = json.loads(row2['related_ids'])

    if args.id2 not in related1:
        related1.append(args.id2)
        conn.execute("UPDATE memories SET related_ids = ? WHERE id = ?",
                    (json.dumps(related1), args.id1))
    if args.id1 not in related2:
        related2.append(args.id1)
        conn.execute("UPDATE memories SET related_ids = ? WHERE id = ?",
                    (json.dumps(related2), args.id2))

    conn.commit()
    print(f"Linked #{args.id1} <-> #{args.id2}")


def cmd_related(args):
    """Show memories related to a given one."""
    conn = get_db()

    row = conn.execute("SELECT * FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Memory #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    related_ids = json.loads(row['related_ids'])
    if not related_ids:
        print(f"No memories related to #{args.id}")
        return

    placeholders = ','.join('?' * len(related_ids))
    rows = conn.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", related_ids).fetchall()

    print(f"Related to #{args.id} '{row['title']}':")
    for r in rows:
        print(f"  #{r['id']} [{r['type']}] {r['title']}")


def cmd_stats(args):
    """Show database statistics."""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_type = conn.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type ORDER BY cnt DESC").fetchall()
    by_importance = conn.execute("SELECT importance, COUNT(*) as cnt FROM memories GROUP BY importance ORDER BY importance DESC").fetchall()
    with_embeddings = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    recent = conn.execute("SELECT COUNT(*) FROM memories WHERE created_at > datetime('now', '-7 days')").fetchone()[0]

    print(f"Total memories: {total}")
    print(f"With embeddings: {with_embeddings}")
    print(f"Added last 7 days: {recent}")
    print()
    print("By type:")
    for row in by_type:
        print(f"  {row['type']}: {row['cnt']}")
    print()
    print("By importance:")
    for row in by_importance:
        print(f"  ★{row['importance']}: {row['cnt']}")

    # Entity stats
    try:
        entity_total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        entity_by_type = conn.execute("SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC").fetchall()
        links_total = conn.execute("SELECT COUNT(*) FROM memory_entities").fetchone()[0]
        edges_total = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
        edges_by_type = conn.execute("SELECT edge_type, COUNT(*) as cnt FROM memory_edges GROUP BY edge_type ORDER BY cnt DESC").fetchall()

        print()
        print(f"Entities: {entity_total}")
        for row in entity_by_type:
            print(f"  {row['type']}: {row['cnt']}")
        print(f"Entity-memory links: {links_total}")
        print(f"Graph edges: {edges_total}")
        for row in edges_by_type:
            print(f"  {row['edge_type']}: {row['cnt']}")
    except Exception as e:
        print(f"Warning: entity stats unavailable: {e}", file=sys.stderr)


def cmd_export(args):
    """Export all memories."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()

    if args.format == 'json':
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    else:
        print("# Memories Export")
        print(f"Exported: {datetime.now().isoformat()}")
        print(f"Total: {len(rows)}")
        print()

        for row in rows:
            print(f"## #{row['id']} {row['title']}")
            print(f"- Type: {row['type']}")
            print(f"- Importance: {row['importance']}")
            print(f"- Tags: {row['tags']}")
            print(f"- Source: {row['source']} ({row['source_date']})")
            print(f"- Created: {row['created_at']}")
            print()
            print(row['content'])
            print()
            print("---")
            print()


def cmd_reindex(args):
    """Rebuild all embeddings."""
    conn = get_db()

    rows = conn.execute("SELECT id, title, content FROM memories").fetchall()
    total = len(rows)

    print(f"Reindexing {total} memories...")

    for i, row in enumerate(rows, 1):
        embed_text = f"{row['title']}\n\n{row['content']}"
        embedding = get_embedding(embed_text)
        if embedding:
            conn.execute("""
                INSERT OR REPLACE INTO embeddings (memory_id, embedding, model)
                VALUES (?, ?, ?)
            """, (row['id'], embedding, EMBED_MODEL))

        if i % 10 == 0 or i == total:
            print(f"  {i}/{total}")

    conn.commit()
    print("Done")


def cmd_entities(args):
    """List all entities with memory counts."""
    conn = get_db()
    sql = "SELECT * FROM entities WHERE 1=1"
    params = []
    if args.type:
        sql += " AND type = ?"
        params.append(args.type)
    sql += " ORDER BY memory_count DESC, name"
    rows = conn.execute(sql, params).fetchall()

    if args.format == 'json':
        print(json.dumps([dict(r) for r in rows], indent=2, default=str))
    else:
        by_type = {}
        for r in rows:
            by_type.setdefault(r['type'], []).append(r)
        for etype, entities in sorted(by_type.items()):
            print(f"\n{etype.upper()} ({len(entities)})")
            for e in entities:
                print(f"  {e['name']} ({e['memory_count']} memories)")


def cmd_entity(args):
    """Show entity details + linked memories."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM entities WHERE LOWER(name) LIKE LOWER(?)",
        (f"%{args.name}%",)
    ).fetchone()
    if not row:
        print(f"Entity '{args.name}' not found", file=sys.stderr)
        sys.exit(1)

    print(f"# {row['name']} ({row['type']})")
    print(f"  Aliases: {row['aliases']}")
    print(f"  First seen: {row['first_seen']} | Last seen: {row['last_seen']}")
    print(f"  Memories: {row['memory_count']}")
    if row['description']:
        print(f"  Description: {row['description']}")

    memories = conn.execute("""
        SELECT m.id, m.type, m.title, m.importance, m.source_date, me.role
        FROM memories m
        JOIN memory_entities me ON m.id = me.memory_id
        WHERE me.entity_id = ?
        ORDER BY m.source_date DESC, m.created_at DESC
    """, (row['id'],)).fetchall()

    if memories:
        print(f"\nLinked memories ({len(memories)}):")
        for m in memories:
            print(f"  #{m['id']} [{m['type']}] {m['title']} ({m['role']})")


def cmd_connect(args):
    """Create a typed edge between two memories."""
    conn = get_db()
    valid_types = ['relates_to', 'contradicts', 'supersedes', 'caused_by', 'follow_up']
    if args.edge_type not in valid_types:
        print(f"Edge type must be one of: {valid_types}", file=sys.stderr)
        sys.exit(1)

    for mid in [args.id1, args.id2]:
        if not conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone():
            print(f"Memory #{mid} not found", file=sys.stderr)
            sys.exit(1)

    try:
        conn.execute("""
            INSERT OR IGNORE INTO memory_edges (source_id, target_id, edge_type, weight)
            VALUES (?, ?, ?, ?)
        """, (args.id1, args.id2, args.edge_type, args.weight))
        conn.commit()
        print(f"Connected #{args.id1} --[{args.edge_type}]--> #{args.id2} (weight={args.weight})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_neighbors(args):
    """Show memories connected via graph edges."""
    conn = get_db()

    row = conn.execute("SELECT id, title FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Memory #{args.id} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Neighbors of #{row['id']} '{row['title']}':\n")

    visited = {args.id}
    frontier = [(args.id, 0)]

    while frontier:
        current_id, hop = frontier.pop(0)
        if hop >= args.depth:
            continue

        edges = conn.execute("""
            SELECT e.*, m.title as neighbor_title, m.type as neighbor_type,
                CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END as neighbor_id
            FROM memory_edges e
            JOIN memories m ON m.id = (CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END)
            WHERE e.source_id = ? OR e.target_id = ?
        """, (current_id, current_id, current_id, current_id)).fetchall()

        for e in edges:
            nid = e['neighbor_id']
            if nid in visited:
                continue
            visited.add(nid)
            indent = "  " * (hop + 1)
            print(f"{indent}#{nid} [{e['neighbor_type']}] {e['neighbor_title']} --[{e['edge_type']} w={e['weight']}]")
            frontier.append((nid, hop + 1))

    if len(visited) == 1:
        print("  (no connections)")


def main():
    parser = argparse.ArgumentParser(
        description="moonshine CLI — manage long-term AI agent memory",
        epilog="Environment: MOONSHINE_DB (path to memories.db), OLLAMA_URL, EMBED_MODEL"
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # add
    p_add = subparsers.add_parser('add', help='Add a memory')
    p_add.add_argument('title', help='Short title')
    p_add.add_argument('--type', '-t', required=True, choices=VALID_TYPES)
    p_add.add_argument('--content', '-c', required=True, help='Full content')
    p_add.add_argument('--tags', help='Comma-separated tags')
    p_add.add_argument('--importance', '-i', type=int, default=3, help='1-5 importance')
    p_add.add_argument('--source', '-s', help='Source (e.g., session:2026-02-11)')
    p_add.add_argument('--source-date', help='When it happened (YYYY-MM-DD)')
    p_add.add_argument('--metadata', '-m', help='JSON metadata')
    p_add.add_argument('--force', action='store_true', help='Skip duplicate check')
    p_add.add_argument('--entities', help='Comma-separated entity names to tag')

    # search
    p_search = subparsers.add_parser('search', help='Search memories')
    p_search.add_argument('query', help='Search query')
    p_search.add_argument('--type', '-t', help='Filter by type')
    p_search.add_argument('--limit', '-n', type=int, default=10)
    p_search.add_argument('--semantic', '-S', action='store_true', help='Use semantic search')
    p_search.add_argument('--format', '-f', choices=['table', 'json'], default='table')
    p_search.add_argument('--verbose', '-v', action='store_true')
    p_search.add_argument('--spread', action='store_true', help='Use spreading activation over graph')
    p_search.add_argument('--hops', type=int, default=2, help='Max hops for spread (default 2)')

    # list
    p_list = subparsers.add_parser('list', help='List memories')
    p_list.add_argument('--type', '-t', help='Filter by type')
    p_list.add_argument('--since', help='Filter by date (YYYY-MM-DD)')
    p_list.add_argument('--tag', help='Filter by tag')
    p_list.add_argument('--min-importance', type=int, help='Minimum importance')
    p_list.add_argument('--limit', '-n', type=int, default=20)
    p_list.add_argument('--format', '-f', choices=['table', 'json'], default='table')

    # show
    p_show = subparsers.add_parser('show', help='Show memory details')
    p_show.add_argument('id', type=int, help='Memory ID')
    p_show.add_argument('--format', '-f', choices=['table', 'json'], default='table')

    # edit
    p_edit = subparsers.add_parser('edit', help='Edit a memory')
    p_edit.add_argument('id', type=int, help='Memory ID')
    p_edit.add_argument('--title', help='New title')
    p_edit.add_argument('--content', '-c', help='New content')
    p_edit.add_argument('--importance', '-i', type=int, help='New importance')
    p_edit.add_argument('--add-tag', help='Add a tag')
    p_edit.add_argument('--remove-tag', help='Remove a tag')

    # delete
    p_delete = subparsers.add_parser('delete', help='Delete a memory')
    p_delete.add_argument('id', type=int, help='Memory ID')
    p_delete.add_argument('--force', '-f', action='store_true', help='Skip confirmation')

    # link
    p_link = subparsers.add_parser('link', help='Link two memories')
    p_link.add_argument('id1', type=int)
    p_link.add_argument('id2', type=int)

    # related
    p_related = subparsers.add_parser('related', help='Show related memories')
    p_related.add_argument('id', type=int)

    # stats
    subparsers.add_parser('stats', help='Show statistics')

    # export
    p_export = subparsers.add_parser('export', help='Export all memories')
    p_export.add_argument('--format', '-f', choices=['json', 'md'], default='json')

    # reindex
    subparsers.add_parser('reindex', help='Rebuild all embeddings')

    # entities
    p_entities = subparsers.add_parser('entities', help='List all entities')
    p_entities.add_argument('--type', '-t', help='Filter by entity type')
    p_entities.add_argument('--format', '-f', choices=['table', 'json'], default='table')

    # entity
    p_entity = subparsers.add_parser('entity', help='Show entity details')
    p_entity.add_argument('name', help='Entity name (partial match)')

    # connect
    p_connect = subparsers.add_parser('connect', help='Create edge between memories')
    p_connect.add_argument('id1', type=int, help='Source memory ID')
    p_connect.add_argument('id2', type=int, help='Target memory ID')
    p_connect.add_argument('--type', dest='edge_type', default='relates_to',
                           help='Edge type (relates_to, contradicts, supersedes, caused_by, follow_up)')
    p_connect.add_argument('--weight', type=float, default=1.0, help='Edge weight 0-1')

    # neighbors
    p_neighbors = subparsers.add_parser('neighbors', help='Show connected memories')
    p_neighbors.add_argument('id', type=int, help='Memory ID')
    p_neighbors.add_argument('--depth', '-d', type=int, default=2, help='Max hops (default 2)')

    args = parser.parse_args()

    commands = {
        'add': cmd_add,
        'search': cmd_search,
        'list': cmd_list,
        'show': cmd_show,
        'edit': cmd_edit,
        'delete': cmd_delete,
        'link': cmd_link,
        'related': cmd_related,
        'stats': cmd_stats,
        'export': cmd_export,
        'reindex': cmd_reindex,
        'entities': cmd_entities,
        'entity': cmd_entity,
        'connect': cmd_connect,
        'neighbors': cmd_neighbors,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
