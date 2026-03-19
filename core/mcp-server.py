#!/usr/bin/env python3
"""
Memory MCP Server — Exposes moonshine long-term memory as MCP tools.

Tools:
  - memory_context: Load relevant memories for current session (call at start)
  - memory_search: Search memories by keyword or semantic similarity
  - memory_save: Persist a new memory (decision, lesson, event, etc.)

Transport: stdio (for mcporter/Claude Code/OpenClaw)
Protocol: MCP (Model Context Protocol) via JSON-RPC over stdin/stdout
"""

import json
import os
import sqlite3
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import logging
import requests

logger = logging.getLogger("moonshine.mcp")

# ============ Config ============

DB_PATH = Path(os.environ.get("MOONSHINE_DB", str(Path(__file__).parent / "memories.db")))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
MEMORY_MD = Path(__file__).parent.parent / "MEMORY.md"
DAILY_DIR = Path(__file__).parent

VALID_TYPES = ['event', 'lesson', 'person', 'behavior', 'project', 'insight', 'decision', 'preference', 'skill']

# Import entity functions from mem.py
sys.path.insert(0, str(Path(__file__).parent))
from mem import (
    extract_entities, link_memory_entities, auto_create_edges, ensure_entity,
    recall_with_spread, expand_acronyms, SEMANTIC_RELEVANCE_FLOOR, _fts_search
)
from temporal import parse_temporal, build_temporal_sql
from reranker import rerank, is_available as reranker_available, RERANK_ENABLED

# ============ Database ============

def get_db() -> sqlite3.Connection:
    db_exists = DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not db_exists:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
    return conn


def get_embedding(text: str) -> Optional[bytes]:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30
        )
        resp.raise_for_status()
        embedding = resp.json()["embedding"]
        return struct.pack(f'{len(embedding)}f', *embedding)
    except Exception:
        return None


def unpack_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f'{count}f', blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (norm_a * norm_b)


# ============ Tool Implementations ============

def _clamp_int(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    """Extract and clamp an integer parameter to [lo, hi]."""
    val = params.get(key, default)
    try:
        val = int(val)
    except (TypeError, ValueError):
        val = default
    return max(lo, min(val, hi))


def _not_in_clause(ids: set, prefix: str = "id") -> tuple[str, list]:
    """Build a parameterized NOT IN clause, or empty string if no IDs to exclude."""
    if not ids:
        return "", []
    placeholders = ','.join('?' * len(ids))
    return f"AND {prefix} NOT IN ({placeholders})", list(ids)


def tool_memory_context(params: dict) -> str:
    """Load relevant context for session start. Returns recent + important memories."""
    conn = get_db()
    project = params.get("project", "")
    limit = _clamp_int(params, "limit", 20, 1, 100)
    
    results = []
    
    # 1. High-importance memories (★4-5)
    rows = conn.execute("""
        SELECT id, type, title, content, importance, source_date, tags
        FROM memories 
        WHERE importance >= 4
        ORDER BY source_date DESC, created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    
    for r in rows:
        results.append(dict(r))
    seen_ids = {r['id'] for r in results}
    
    # 2. Recent memories (last 7 days)
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    excl_clause, excl_params = _not_in_clause(seen_ids)
    rows = conn.execute(f"""
        SELECT id, type, title, content, importance, source_date, tags
        FROM memories
        WHERE (source_date >= ? OR created_at >= ?)
        {excl_clause}
        ORDER BY source_date DESC
        LIMIT ?
    """, (week_ago, week_ago, *excl_params, limit)).fetchall()
    
    for r in rows:
        results.append(dict(r))
        seen_ids.add(r['id'])
    
    # 3. If project specified, do semantic search for project-related memories
    if project:
        embedding = get_embedding(project)
        if embedding:
            query_vec = unpack_embedding(embedding)
            excl_clause2, excl_params2 = _not_in_clause(seen_ids, "m.id")
            emb_rows = conn.execute(f"""
                SELECT m.id, m.type, m.title, m.content, m.importance, m.source_date, m.tags, e.embedding
                FROM memories m
                JOIN embeddings e ON m.id = e.memory_id
                WHERE 1=1 {excl_clause2}
            """, excl_params2).fetchall()
            
            scored = []
            for row in emb_rows:
                vec = unpack_embedding(row['embedding'])
                score = cosine_similarity(query_vec, vec)
                if score > 0.3:
                    scored.append((score, dict(row)))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            for score, row in scored[:10]:
                row.pop('embedding', None)
                row['relevance'] = round(score, 3)
                results.append(row)
    
    # Format output
    output_lines = [f"## Memory Context ({len(results)} memories loaded)\n"]
    
    by_type = {}
    for r in results:
        by_type.setdefault(r['type'], []).append(r)
    
    for type_name, memories in sorted(by_type.items()):
        output_lines.append(f"\n### {type_name.title()}s")
        for m in memories:
            star = '★' * m['importance']
            date = m.get('source_date', '')
            output_lines.append(f"- **{m['title']}** ({date}) {star}")
            if m['content']:
                # Truncate long content
                content = m['content'][:200]
                if len(m['content']) > 200:
                    content += '...'
                output_lines.append(f"  {content}")
    
    # Also include today's daily file summary if it exists
    today = datetime.now().strftime('%Y-%m-%d')
    daily_file = DAILY_DIR / f"{today}.md"
    if daily_file.exists():
        content = daily_file.read_text()
        lines = content.split('\n')
        # Get headers as summary
        headers = [l for l in lines if l.startswith('## ')]
        if headers:
            output_lines.append(f"\n### Today's Session Log ({today})")
            output_lines.append(f"Sections: {', '.join(h.lstrip('#').strip() for h in headers[:10])}")
    
    return '\n'.join(output_lines)


def tool_memory_search(params: dict) -> str:
    """Search memories by keyword or semantic similarity, with temporal awareness."""
    conn = get_db()
    query = params.get("query", "")
    semantic = params.get("semantic", True)
    mem_type = params.get("type")
    limit = _clamp_int(params, "limit", 10, 1, 100)
    spread = params.get("spread", False)
    # Explicit temporal overrides from params
    param_after = params.get("after")
    param_before = params.get("before")
    
    if not query:
        return "Error: query is required"
    
    # Parse temporal expressions from the query
    temporal = parse_temporal(query)
    search_query = temporal['cleaned_query'] or query
    t_after = param_after or temporal['after']
    t_before = param_before or temporal['before']
    temporal_note = ""
    if temporal['temporal_expr']:
        temporal_note = f" (temporal: {temporal['temporal_expr']} → {t_after or '∞'} to {t_before or '∞'})"
    
    # Build temporal SQL fragment for filtering
    temporal_sql, temporal_params = build_temporal_sql(t_after, t_before, 'created_at')
    
    # If spread requested, use recall_with_spread (temporal filter applied post-hoc)
    if spread:
        conn = get_db()
        spread_results = recall_with_spread(
            conn, search_query, limit=limit * 3 if t_after or t_before else limit,
            spread=True, max_hops=2, decay=0.5,
            type_filter=mem_type, semantic=semantic
        )
        # Apply temporal filter
        if t_after or t_before:
            spread_results = _apply_temporal_filter(spread_results, t_after, t_before)
        spread_results = spread_results[:limit]
        
        lines = []
        for score, row in spread_results:
            row.pop('embedding', None)
            score_str = f" [score: {score:.3f}]" if score else ""
            spread_marker = " 🔗" if row.get('_spread') else ""
            star = '★' * row.get('importance', 3)
            lines.append(f"**#{row['id']}** [{row['type']}] {row['title']}{score_str}{spread_marker} {star}")
            if row.get('content'):
                lines.append(f"  {row['content'][:300]}")
            lines.append("")
        if not lines:
            return f"No memories found for: {query}{temporal_note}"
        return f"Found {len(spread_results)} memories (with spread){temporal_note}:\n\n" + '\n'.join(lines)
    
    results = []
    
    if semantic:
        embedding = get_embedding(search_query)
        if embedding:
            query_vec = unpack_embedding(embedding)
            sql = """
                SELECT m.*, e.embedding
                FROM memories m
                JOIN embeddings e ON m.id = e.memory_id
                WHERE (? IS NULL OR m.type = ?)
            """
            sql_params = [mem_type, mem_type]
            
            # Apply temporal filter in SQL for efficiency
            if temporal_sql:
                sql += temporal_sql.replace('created_at', 'm.created_at')
                sql_params.extend(temporal_params)
            
            rows = conn.execute(sql, sql_params).fetchall()
            
            scored = []
            for row in rows:
                vec = unpack_embedding(row['embedding'])
                score = cosine_similarity(query_vec, vec)
                scored.append((score, dict(row)))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            results = scored[:limit]
        else:
            # Fallback to FTS
            semantic = False
    
    if not semantic:
        fts_query = f'"{search_query}"' if ' ' in search_query else search_query
        sql = """
            SELECT m.*, fts.rank
            FROM memories m
            JOIN memories_fts fts ON m.id = fts.rowid
            WHERE memories_fts MATCH ?
        """
        sql_params = [fts_query]
        if mem_type:
            sql += " AND m.type = ?"
            sql_params.append(mem_type)
        # Apply temporal filter
        if temporal_sql:
            sql += temporal_sql.replace('created_at', 'm.created_at')
            sql_params.extend(temporal_params)
        sql += " ORDER BY fts.rank LIMIT ?"
        sql_params.append(limit)
        
        rows = conn.execute(sql, sql_params).fetchall()
        results = [(None, dict(r)) for r in rows]
    
    # Cross-encoder reranking (opt-in via MOONSHINE_RERANK=true)
    rerank_note = ""
    if RERANK_ENABLED and results:
        try:
            results = rerank(query, results, top_k=limit)
            rerank_note = " (reranked)"
        except Exception as e:
            logger.warning(f"Reranking failed, using original order: {e}")

    # Format output
    lines = []
    for score, row in results:
        row.pop('embedding', None)
        score_str = ""
        if row.get('rerank_score') is not None:
            score_str = f" [rerank: {row['rerank_score']:.3f}]"
        elif score:
            score_str = f" [similarity: {score:.3f}]"
        star = '★' * row.get('importance', 3)
        date_str = row.get('source_date') or row.get('created_at', '')[:10]
        lines.append(f"**#{row['id']}** [{row['type']}] {row['title']} ({date_str}){score_str} {star}")
        if row.get('content'):
            lines.append(f"  {row['content'][:300]}")
        if row.get('tags') and row['tags'] != '[]':
            lines.append(f"  Tags: {row['tags']}")
        lines.append("")
    
    if not lines:
        return f"No memories found for: {query}{temporal_note}"
    
    return f"Found {len(results)} memories{temporal_note}{rerank_note}:\n\n" + '\n'.join(lines)


def _apply_temporal_filter(results: list, after: str = None, before: str = None) -> list:
    """Filter (score, row) results by temporal bounds on created_at."""
    filtered = []
    for score, row in results:
        created = row.get('created_at', '') or row.get('source_date', '') or ''
        date_str = created[:10]  # YYYY-MM-DD
        if after and date_str < after:
            continue
        if before and date_str >= before:
            continue
        filtered.append((score, row))
    return filtered


def tool_memory_save(params: dict) -> str:
    """Save a new memory to the database."""
    conn = get_db()
    
    title = params.get("title", "")
    content = params.get("content", "")
    mem_type = params.get("type", "insight")
    importance = _clamp_int(params, "importance", 3, 1, 5)
    tags = params.get("tags", [])
    source = params.get("source", "")
    source_date = params.get("source_date", datetime.now().strftime('%Y-%m-%d'))
    
    if not title:
        return "Error: title is required"
    if mem_type not in VALID_TYPES:
        return f"Error: type must be one of {VALID_TYPES}"
    
    # Dedup check
    existing = conn.execute(
        "SELECT id, title FROM memories WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))",
        (title,)
    ).fetchone()
    if existing:
        return f"Duplicate detected: #{existing['id']} '{existing['title']}' — not saved"
    
    tags_json = json.dumps(tags if isinstance(tags, list) else tags.split(','))
    
    cursor = conn.execute("""
        INSERT INTO memories (type, title, content, tags, importance, source, source_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (mem_type, title, content, tags_json, importance, source, source_date))
    
    memory_id = cursor.lastrowid
    
    # Generate embedding
    embed_text = f"{title}\n\n{content}"
    embedding = get_embedding(embed_text)
    if embedding:
        conn.execute("""
            INSERT INTO embeddings (memory_id, embedding, model)
            VALUES (?, ?, ?)
        """, (memory_id, embedding, EMBED_MODEL))
    
    # Extract and link entities
    entity_names = []
    try:
        entities = extract_entities(content, title, json.dumps(tags) if isinstance(tags, list) else str(tags))
        if entities:
            link_memory_entities(conn, memory_id, entities, source_date)
            entity_ids = []
            for ent in entities:
                row = conn.execute(
                    "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) AND type = ?",
                    (ent["name"], ent["type"])
                ).fetchone()
                if row:
                    entity_ids.append(row['id'])
            if entity_ids:
                auto_create_edges(conn, memory_id, entity_ids)
            entity_names = [e["name"] for e in entities]
    except Exception as e:
        logger.warning(f"Entity extraction failed: {e}")
    
    conn.commit()
    ent_str = f", entities: {', '.join(entity_names)}" if entity_names else ""
    return f"Saved memory #{memory_id}: {title} (type={mem_type}, importance={importance}{ent_str})"


def tool_memory_briefing(params: dict) -> str:
    """Structured session briefing — no LLM, just aggregation."""
    conn = get_db()
    context = params.get("context", "")
    limit = _clamp_int(params, "limit", 10, 1, 50)
    
    sections = {}
    
    # Recent activity (48h)
    rows = conn.execute("""
        SELECT id, type, title, content, importance, source_date
        FROM memories WHERE created_at > datetime('now', '-2 days')
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    sections["recent_activity"] = [dict(r) for r in rows]
    
    # Active commitments (decisions, follow_ups)
    rows = conn.execute("""
        SELECT m.id, m.type, m.title, m.content, m.importance
        FROM memories m
        WHERE m.type IN ('decision', 'event') AND m.importance >= 4
        ORDER BY m.source_date DESC LIMIT ?
    """, (limit,)).fetchall()
    sections["active_commitments"] = [dict(r) for r in rows]
    
    # Key facts (high importance lessons/insights)
    rows = conn.execute("""
        SELECT id, type, title, content, importance FROM memories
        WHERE type IN ('lesson', 'insight') AND importance >= 4
        ORDER BY importance DESC, created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    sections["key_facts"] = [dict(r) for r in rows]
    
    # Relevant entities (if context given)
    relevant_entities = []
    if context:
        entities = extract_entities(context, context, "")
        for ent in entities:
            row = conn.execute(
                "SELECT * FROM entities WHERE LOWER(name) = LOWER(?) AND type = ?",
                (ent["name"], ent["type"])
            ).fetchone()
            if row:
                relevant_entities.append(dict(row))
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY memory_count DESC LIMIT 10"
        ).fetchall()
        relevant_entities = [dict(r) for r in rows]
    sections["relevant_entities"] = relevant_entities
    
    # Format
    lines = ["## Session Briefing\n"]
    
    if sections["recent_activity"]:
        lines.append("### Recent Activity (48h)")
        for m in sections["recent_activity"]:
            lines.append(f"- **{m['title']}** [{m['type']}] ★{m['importance']}")
    
    if sections["active_commitments"]:
        lines.append("\n### Active Commitments")
        for m in sections["active_commitments"]:
            lines.append(f"- **{m['title']}**: {m['content'][:150]}")
    
    if sections["key_facts"]:
        lines.append("\n### Key Facts")
        for m in sections["key_facts"]:
            lines.append(f"- **{m['title']}**: {m['content'][:150]}")
    
    if sections["relevant_entities"]:
        lines.append("\n### Relevant Entities")
        for e in sections["relevant_entities"]:
            lines.append(f"- **{e['name']}** ({e['type']}) — {e['memory_count']} memories")
    
    return '\n'.join(lines)


def tool_memory_surface(params: dict) -> str:
    """Proactive memory surfacing via entity + spread."""
    conn = get_db()
    context = params.get("context", "")
    exclude_ids = params.get("exclude_ids", [])
    limit = _clamp_int(params, "limit", 5, 1, 50)
    
    if not context:
        return "Error: context is required"
    
    # Extract entities from context
    entities = extract_entities(context, context, "")
    if not entities:
        return "No entities found in context — nothing to surface"
    
    # Get memories linked to those entities
    surfaced = []
    seen = set(exclude_ids)
    
    for ent in entities:
        row = conn.execute(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) AND type = ?",
            (ent["name"], ent["type"])
        ).fetchone()
        if not row:
            continue
        
        excl_clause3, excl_params3 = _not_in_clause(seen, "m.id")
        memories = conn.execute(f"""
            SELECT m.id, m.type, m.title, m.content, m.importance, m.source_date
            FROM memories m
            JOIN memory_entities me ON m.id = me.memory_id
            WHERE me.entity_id = ? {excl_clause3}
            ORDER BY m.importance DESC, m.created_at DESC
            LIMIT ?
        """, (row['id'], *excl_params3, limit)).fetchall()
        
        for m in memories:
            if m['id'] not in seen:
                seen.add(m['id'])
                surfaced.append({
                    "memory": dict(m),
                    "reason": f"Connected to entity: {ent['name']}"
                })
    
    if not surfaced:
        return "No additional memories to surface"
    
    lines = [f"Surfaced {len(surfaced)} memories:\n"]
    for s in surfaced[:limit]:
        m = s["memory"]
        lines.append(f"**#{m['id']}** [{m['type']}] {m['title']} ★{m['importance']}")
        lines.append(f"  Reason: {s['reason']}")
        if m.get('content'):
            lines.append(f"  {m['content'][:200]}")
        lines.append("")
    
    return '\n'.join(lines)


def tool_memory_entities(params: dict) -> str:
    """List/query entities."""
    conn = get_db()
    name = params.get("name")
    etype = params.get("type")
    limit = _clamp_int(params, "limit", 20, 1, 100)
    
    if name:
        rows = conn.execute(
            "SELECT * FROM entities WHERE LOWER(name) LIKE LOWER(?) ORDER BY memory_count DESC LIMIT ?",
            (f"%{name}%", limit)
        ).fetchall()
    elif etype:
        rows = conn.execute(
            "SELECT * FROM entities WHERE type = ? ORDER BY memory_count DESC LIMIT ?",
            (etype, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entities ORDER BY memory_count DESC LIMIT ?", (limit,)
        ).fetchall()
    
    if not rows:
        return "No entities found"
    
    lines = [f"Found {len(rows)} entities:\n"]
    for r in rows:
        lines.append(f"- **{r['name']}** ({r['type']}) — {r['memory_count']} memories, last seen: {r['last_seen']}")
    return '\n'.join(lines)


def tool_memory_connect(params: dict) -> str:
    """Create a typed edge between two memories."""
    conn = get_db()
    source_id = params.get("source_id")
    target_id = params.get("target_id")
    edge_type = params.get("edge_type", "relates_to")
    raw_weight = params.get("weight", 1.0)
    try:
        weight = max(0.0, min(float(raw_weight), 1.0))
    except (TypeError, ValueError):
        weight = 1.0
    
    valid_types = ['relates_to', 'contradicts', 'supersedes', 'caused_by', 'follow_up']
    if edge_type not in valid_types:
        return f"Error: edge_type must be one of {valid_types}"
    
    for mid in [source_id, target_id]:
        if not conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone():
            return f"Error: memory #{mid} not found"
    
    try:
        conn.execute("""
            INSERT OR IGNORE INTO memory_edges (source_id, target_id, edge_type, weight)
            VALUES (?, ?, ?, ?)
        """, (source_id, target_id, edge_type, weight))
        conn.commit()
        return f"Connected #{source_id} --[{edge_type}]--> #{target_id} (weight={weight})"
    except Exception as e:
        return f"Error: {e}"


def tool_memory_neighbors(params: dict) -> str:
    """Get graph neighbors of a memory."""
    conn = get_db()
    memory_id = params.get("memory_id")
    depth = _clamp_int(params, "depth", 1, 1, 3)
    edge_types = params.get("edge_types")
    
    row = conn.execute("SELECT id, title FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return f"Error: memory #{memory_id} not found"
    
    visited = {memory_id}
    frontier = [(memory_id, 0)]
    neighbors = []
    
    while frontier:
        current_id, hop = frontier.pop(0)
        if hop >= depth:
            continue
        
        sql = """
            SELECT e.edge_type, e.weight, m.id, m.type, m.title, m.importance,
                CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END as neighbor_id
            FROM memory_edges e
            JOIN memories m ON m.id = (CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END)
            WHERE (e.source_id = ? OR e.target_id = ?)
        """
        params_list = [current_id, current_id, current_id, current_id]
        
        if edge_types:
            placeholders = ','.join('?' * len(edge_types))
            sql += f" AND e.edge_type IN ({placeholders})"
            params_list.extend(edge_types)
        
        edges = conn.execute(sql, params_list).fetchall()
        
        for e in edges:
            nid = e['neighbor_id']
            if nid in visited:
                continue
            visited.add(nid)
            neighbors.append({
                "memory": {"id": e['id'], "type": e['type'], "title": e['title'], "importance": e['importance']},
                "edge_type": e['edge_type'],
                "weight": e['weight'],
                "hops": hop + 1
            })
            frontier.append((nid, hop + 1))
    
    if not neighbors:
        return f"No neighbors found for #{memory_id}"
    
    lines = [f"Neighbors of #{memory_id} '{row['title']}' ({len(neighbors)} found):\n"]
    for n in neighbors:
        m = n["memory"]
        lines.append(f"- **#{m['id']}** [{m['type']}] {m['title']} — {n['edge_type']} (w={n['weight']}, {n['hops']} hop{'s' if n['hops']>1 else ''})")
    return '\n'.join(lines)


def tool_memory_consolidate(params: dict) -> str:
    """Find contradictions, near-duplicates. Pattern-based, no LLM."""
    conn = get_db()
    scope = params.get("scope", "recent")
    dry_run = params.get("dry_run", False)
    
    results = {"contradictions_found": 0, "edges_created": 0, "entities_updated": 0, "duplicates_merged": 0}
    details = []
    
    # 1. Find exact-title duplicates
    dupes = conn.execute("""
        SELECT LOWER(TRIM(title)) as norm_title, GROUP_CONCAT(id) as ids, COUNT(*) as cnt
        FROM memories GROUP BY norm_title HAVING cnt > 1
    """).fetchall()
    
    for d in dupes:
        ids = [int(i) for i in d['ids'].split(',')]
        results["duplicates_merged"] += len(ids) - 1
        details.append(f"Duplicate titles: IDs {ids} — '{d['norm_title']}'")
    
    # 2. Find memories sharing entities that lack edges
    if scope == "all":
        time_filter = ""
    else:
        time_filter = "AND m1.created_at > datetime('now', '-7 days')"
    
    # Find pairs sharing 2+ entities without edges
    pairs = conn.execute(f"""
        SELECT me1.memory_id as m1, me2.memory_id as m2, COUNT(*) as shared
        FROM memory_entities me1
        JOIN memory_entities me2 ON me1.entity_id = me2.entity_id AND me1.memory_id < me2.memory_id
        JOIN memories m1 ON m1.id = me1.memory_id
        WHERE NOT EXISTS (
            SELECT 1 FROM memory_edges e 
            WHERE (e.source_id = me1.memory_id AND e.target_id = me2.memory_id)
            OR (e.source_id = me2.memory_id AND e.target_id = me1.memory_id)
        )
        {time_filter}
        GROUP BY me1.memory_id, me2.memory_id
        HAVING shared >= 2
        LIMIT 100
    """).fetchall()
    
    for p in pairs:
        if not dry_run:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO memory_edges (source_id, target_id, edge_type, weight)
                    VALUES (?, ?, 'relates_to', ?)
                """, (p['m1'], p['m2'], min(p['shared'] / 5.0, 1.0)))
                results["edges_created"] += 1
            except Exception:
                pass
        else:
            results["edges_created"] += 1
    
    # 3. Update entity memory counts
    if not dry_run:
        conn.execute("""
            UPDATE entities SET memory_count = (
                SELECT COUNT(*) FROM memory_entities WHERE entity_id = entities.id
            ), updated_at = datetime('now', 'localtime')
        """)
        updated = conn.execute("SELECT changes()").fetchone()[0]
        results["entities_updated"] = updated
        conn.commit()
    
    lines = [f"Consolidation {'(DRY RUN) ' if dry_run else ''}results:"]
    lines.append(f"  Duplicates found: {results['duplicates_merged']}")
    lines.append(f"  Edges created: {results['edges_created']}")
    lines.append(f"  Entities updated: {results['entities_updated']}")
    if details:
        lines.append("\nDetails:")
        for d in details[:10]:
            lines.append(f"  - {d}")
    
    return '\n'.join(lines)


# ============ MCP Protocol ============

TOOLS = [
    {
        "name": "memory_context",
        "description": "Load relevant memories for the current session. Call this at session start to get context from previous sessions. Returns high-importance memories, recent memories (last 7 days), and optionally project-specific memories via semantic search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Optional project name or topic to find relevant memories for"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max memories per category (default 20)",
                    "default": 20
                }
            }
        }
    },
    {
        "name": "memory_search",
        "description": "Search the long-term memory database. Uses semantic similarity (Ollama embeddings) by default, falls back to keyword search (FTS5). Supports natural language time expressions (e.g. 'decisions last week', 'what happened yesterday', 'since March 10') — temporal filters are auto-detected and applied. You can also pass explicit after/before dates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — can be natural language. Time expressions like 'last week', 'yesterday', '3 days ago' are auto-detected and used as date filters."
                },
                "semantic": {
                    "type": "boolean",
                    "description": "Use semantic search (default true). Set false for exact keyword matching.",
                    "default": True
                },
                "type": {
                    "type": "string",
                    "enum": ["event", "lesson", "person", "behavior", "project", "insight", "decision", "preference", "skill"],
                    "description": "Filter by memory type"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                    "default": 10
                },
                "spread": {
                    "type": "boolean",
                    "description": "Use spreading activation over graph edges (default false)",
                    "default": False
                },
                "after": {
                    "type": "string",
                    "description": "Only return memories created on/after this date (YYYY-MM-DD). Overrides auto-detected temporal."
                },
                "before": {
                    "type": "string",
                    "description": "Only return memories created before this date (YYYY-MM-DD). Overrides auto-detected temporal."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_save",
        "description": "Save a new memory to the long-term database. Use this to persist decisions, lessons learned, events, insights, or any knowledge worth keeping across sessions. Memories are automatically embedded for semantic search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short, searchable title"
                },
                "content": {
                    "type": "string",
                    "description": "Full description — should be standalone (make sense without context)"
                },
                "type": {
                    "type": "string",
                    "enum": ["event", "lesson", "person", "behavior", "project", "insight", "decision", "preference", "skill"],
                    "description": "Memory type (default: insight)"
                },
                "importance": {
                    "type": "integer",
                    "description": "1-5 scale (1=trivial, 3=normal, 5=critical). Default: 3",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for filtering"
                },
                "source": {
                    "type": "string",
                    "description": "Where this memory came from (e.g., 'session:2026-02-18')"
                },
                "source_date": {
                    "type": "string",
                    "description": "Date the event/knowledge occurred (YYYY-MM-DD). Defaults to today."
                }
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "memory_briefing",
        "description": "Get a structured session briefing — recent activity, active commitments, key facts, relevant entities. No LLM cost, just aggregation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "What the session is about (optional)"},
                "limit": {"type": "integer", "description": "Max memories per section (default 10)", "default": 10}
            }
        }
    },
    {
        "name": "memory_surface",
        "description": "Proactively surface relevant memories based on current context via entity extraction + graph traversal. Use to find memories the agent didn't explicitly search for.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Current conversation context or summary"},
                "exclude_ids": {"type": "array", "items": {"type": "integer"}, "description": "Memory IDs already seen this session"},
                "limit": {"type": "integer", "description": "Max results (default 5)", "default": 5}
            },
            "required": ["context"]
        }
    },
    {
        "name": "memory_entities",
        "description": "List tracked entities in the knowledge graph, or get details about a specific entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to search (partial match)"},
                "type": {"type": "string", "description": "Filter by type (person/project/tool/concept/organization)"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20}
            }
        }
    },
    {
        "name": "memory_connect",
        "description": "Create a typed edge between two memories in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "integer", "description": "Source memory ID"},
                "target_id": {"type": "integer", "description": "Target memory ID"},
                "edge_type": {"type": "string", "enum": ["relates_to", "contradicts", "supersedes", "caused_by", "follow_up"], "description": "Edge type (default: relates_to)"},
                "weight": {"type": "number", "description": "Edge weight 0-1 (default: 1.0)", "default": 1.0}
            },
            "required": ["source_id", "target_id"]
        }
    },
    {
        "name": "memory_neighbors",
        "description": "Get memories connected to a specific memory via knowledge graph edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "Memory ID to find neighbors for"},
                "depth": {"type": "integer", "description": "Max hops (default 1, max 3)", "default": 1},
                "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Filter by edge types"}
            },
            "required": ["memory_id"]
        }
    },
    {
        "name": "memory_consolidate",
        "description": "Run memory consolidation — detect contradictions, find near-duplicates, create missing edges, update entity counts. Pattern-based, no LLM cost.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["all", "recent"], "description": "Scope: 'all' or 'recent' (last 7 days, default)"},
                "dry_run": {"type": "boolean", "description": "Preview changes without applying (default false)", "default": False}
            }
        }
    }
]

SERVER_INFO = {
    "name": "moonshine-memory",
    "version": "1.0.0"
}

CAPABILITIES = {
    "tools": {}
}


def handle_request(request: dict) -> dict:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": SERVER_INFO,
                "capabilities": CAPABILITIES
            }
        }
    
    elif method == "notifications/initialized":
        return None  # No response for notifications
    
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS}
        }
    
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        
        try:
            if tool_name == "memory_context":
                result = tool_memory_context(tool_args)
            elif tool_name == "memory_search":
                result = tool_memory_search(tool_args)
            elif tool_name == "memory_save":
                result = tool_memory_save(tool_args)
            elif tool_name == "memory_briefing":
                result = tool_memory_briefing(tool_args)
            elif tool_name == "memory_surface":
                result = tool_memory_surface(tool_args)
            elif tool_name == "memory_entities":
                result = tool_memory_entities(tool_args)
            elif tool_name == "memory_connect":
                result = tool_memory_connect(tool_args)
            elif tool_name == "memory_neighbors":
                result = tool_memory_neighbors(tool_args)
            elif tool_name == "memory_consolidate":
                result = tool_memory_consolidate(tool_args)
            else:
                result = f"Unknown tool: {tool_name}"
            
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result}]
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True
                }
            }
    
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


def main():
    """Run MCP server over stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + '\n')
            sys.stdout.flush()


if __name__ == "__main__":
    main()
