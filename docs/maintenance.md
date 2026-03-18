# Memory Maintenance Guide

A memory system that's never maintained is a memory system that slowly poisons itself. Old facts contradict new ones. Stale project references waste context. Duplicates dilute search results.

This guide covers how to keep things healthy.

---

## MEMORY.md Pruning

### When to Prune

MEMORY.md should stay between 6-8K characters. Check periodically:

```bash
wc -c MEMORY.md
# If approaching 10K+, it's time to prune.
```

### What to Remove

- **Completed projects** — if a project is done, archive it. A one-liner in "past projects" is fine; the detailed context doesn't need to be in hot memory anymore.
- **Stale rules** — rules about tools you no longer use, people who've moved on, workflows you've changed.
- **Redundant entries** — if you have "prefers direct communication" in three different sections, consolidate.
- **Ephemeral context** — "Currently waiting on X" or "Will revisit next week" — these go stale fast.

### Where Pruned Items Go

Move them to cold storage:

```bash
cd core

# Save a pruned item as a memory
./mem add "Completed: Migration project" \
  --type project \
  --content "Migrated from Postgres to SQLite. Completed 2025-03-01. Key decisions: chose FTS5 over trigram, WAL mode for concurrency." \
  --importance 3 \
  --tags "completed,migration"
```

Or archive in bulk:

```bash
./mem add "Archived from MEMORY.md" \
  --type insight \
  --content "$(cat archived-section.md)" \
  --importance 2
```

The memory is preserved in the cold tier, searchable on demand, but no longer burning hot-tier context space.

---

## Archiving Old Memories

### When to Archive

Memories don't auto-expire, but some become irrelevant:

- Events from months ago that have no ongoing impact
- Decisions that were later reversed
- Lessons that became obvious (internalized into MEMORY.md rules)

### How to Archive

```bash
# List old, low-importance memories
./mem list --max-importance 2 --before 2025-01-01

# The consolidate tool can identify candidates
# (via MCP — call memory_consolidate from your agent)
```

You can delete memories, but a better approach is to lower their importance:

```bash
# Reduce importance so they don't surface in context loading
# but remain searchable
sqlite3 memories.db "UPDATE memories SET importance = 1 WHERE id IN (42, 43, 44)"
```

Importance 1 memories still appear in explicit searches but won't be included in `memory_context` results.

---

## Running Consolidation

The `memory_consolidate` MCP tool (or manual review) addresses three problems:

### 1. Contradictions

Over time, memories contradict each other:
- "Prefers TypeScript" vs "Switched to Python for scripts"
- "Uses Postgres" vs "Migrated to SQLite"

Consolidation finds these by identifying memories with overlapping entities but conflicting content. Resolution: keep the newer one, archive or update the older one.

### 2. Duplicates

The same fact captured multiple times from different daily logs:
- "Set up CI pipeline" on three different dates (because it came up in three conversations)

Consolidation merges these into a single memory with the best content from each.

### 3. Drift

Gradual shifts that aren't captured:
- A preference that changed over weeks but was never explicitly updated
- A project that evolved in scope

Consolidation surfaces these for human review.

### Running It

From your agent (via MCP):
```
Use memory_consolidate to find contradictions and duplicates in recent memories.
```

Manual review:
```bash
# Find potential duplicates (memories with similar titles)
sqlite3 memories.db "
  SELECT m1.id, m1.title, m2.id, m2.title
  FROM memories m1, memories m2
  WHERE m1.id < m2.id
  AND m1.title LIKE '%' || substr(m2.title, 1, 20) || '%'
  LIMIT 20
"
```

---

## The Eval Suite

### What It Tests

The eval suite uses [promptfoo](https://promptfoo.dev/) to verify that searches return the right memories. It tests *quality*, not just "does it run."

Test categories:
- **People queries** — "What's X's competency level?" should surface the right person
- **Project lookups** — "Status of project Y" should find project memories
- **Temporal questions** — "What happened last week" should return recent events
- **Semantic similarity** — "database decisions" should find "chose SQLite over Postgres"
- **Acronym expansion** — configured acronyms expand during search
- **Graph traversal** — related entities surface connected memories

### How to Run

```bash
cd evals/

# Run all tests
npx promptfoo eval

# View results in browser
npx promptfoo view
```

### How to Add Test Cases

Edit `evals/promptfooconfig.yaml`:

```yaml
tests:
  - vars:
      query: "Your search query"
    assert:
      - type: contains-any
        value: ["expected", "terms", "in", "results"]
        metric: recall
        weight: 2
      - type: not-contains
        value: "No results found"
        metric: has-results
      - type: llm-rubric
        value: "Results contain information about [what you expect]"
        metric: relevance
```

Good test cases have:
- A realistic query (how would you actually ask this?)
- `contains-any` assertions for key terms that should appear
- An `llm-rubric` assertion for semantic relevance
- A `not-contains` guard against empty results

### Running on a Schedule

For ongoing quality monitoring:

```bash
# Weekly eval (cron example)
0 5 * * 1 cd /path/to/agent-memory/evals && npx promptfoo eval --no-cache 2>&1 | mail -s "Memory Eval" you@example.com
```

---

## Common Issues

### "Memory keeps forgetting things"

**Symptom:** The agent doesn't recall something you told it last week.

**Causes and fixes:**

1. **It's not in MEMORY.md and wasn't explicitly saved.**
   - If the observer is enabled, check `observations.db` — it might be there as an observation but not promoted to a memory.
   - Fix: Enable the observer, or explicitly call `memory_save` for important facts.

2. **It's in memories.db but search isn't finding it.**
   - Try different search terms. FTS5 is literal — "database" won't find "DB."
   - Try `--semantic` search if Ollama is running.
   - Fix: Add acronym mappings in the search config. Use semantic search.

3. **It's in memories.db but not surfacing in context.**
   - `memory_context` prioritizes high-importance, recent memories.
   - If it's old and importance 2, it won't appear in context — but it will appear in explicit searches.
   - Fix: Increase the memory's importance, or search explicitly.

4. **MEMORY.md is too large, diluting attention.**
   - Check the size. If it's over 12K characters, the model is drowning in context.
   - Fix: Prune MEMORY.md. Move stale items to cold storage.

### "Observer costs are too high"

**Symptom:** Your Anthropic bill is higher than expected from observer calls.

**Fixes:**
- Increase `TOKEN_THRESHOLD` in `observe.js` (default 3000). Higher threshold = fewer calls.
- Increase `TOKEN_THRESHOLD` in `reflect.js` (default 4000).
- If you're in a burst period (lots of long conversations), temporarily disable the observer and rely on manual saves.
- Switch to a cheaper model. Any model that handles structured JSON extraction works.

### "Embeddings aren't generating"

**Symptom:** Semantic search returns no results even though memories exist.

**Fixes:**
- Verify Ollama is running: `curl http://127.0.0.1:11434/api/tags`
- Verify the model is pulled: `ollama list` should show `nomic-embed-text`
- Check the embedding count: `sqlite3 memories.db "SELECT COUNT(*) FROM embeddings"`
- Re-embed existing memories: `./mem reembed` (if your CLI supports it)

### "Knowledge graph is sparse"

**Symptom:** `memory_entities` returns few entities despite many memories.

**Fixes:**
- Entity extraction is pattern-based. It catches explicit mentions of names, projects, tools.
- If your memories use abbreviations or nicknames, add aliases to existing entities.
- Re-run entity extraction on existing memories: call `memory_save` with the same content to trigger re-extraction.

---

## Backup Strategy

### What to Back Up

- `core/memories.db` — the entire cold tier
- `observer/observations.db` — observer state
- `MEMORY.md` — hot tier
- `CONTEXT.md` — technically regeneratable, but nice to have

### How

**Simple (rsync):**
```bash
rsync -av memories.db /path/to/backup/memories-$(date +%Y%m%d).db
```

**With rotation (keep 7 days):**
```bash
#!/bin/bash
BACKUP_DIR=/path/to/backups
cp memories.db "$BACKUP_DIR/memories-$(date +%Y%m%d).db"
find "$BACKUP_DIR" -name "memories-*.db" -mtime +7 -delete
```

**Git (for MEMORY.md):**
MEMORY.md is a text file — it belongs in version control. Commit changes regularly. The git history becomes an audit trail of how your agent's identity evolved.

**Don't back up:**
- `node_modules/` — reinstallable
- Embedding cache — regeneratable from memories + Ollama

### Recovery

If you lose `memories.db`, you lose cold-tier memories. MEMORY.md (hot tier) and CONTEXT.md (warm tier, regeneratable) are unaffected.

If you lose `observations.db`, the observer starts fresh. No conversation history is lost (that's in your platform's logs), but compressed observations need to be rebuilt.

If you lose `MEMORY.md`, you lose hot-tier context. Restore from git or a backup. In a pinch, `memory_context` can bootstrap a new MEMORY.md from high-importance cold-tier memories.

---

## Maintenance Schedule

A reasonable cadence:

| Frequency | Task |
|-----------|------|
| Weekly | Glance at MEMORY.md size. Prune if > 10K chars. |
| Monthly | Run consolidation. Check for contradictions and duplicates. |
| Monthly | Run the eval suite. Investigate any regressions. |
| Quarterly | Review entity list. Clean up stale entities, add missing aliases. |
| On backup schedule | Back up memories.db and observations.db. |

Automate what you can. The eval suite and backups are trivially cron-able. MEMORY.md pruning requires judgment — keep that human-in-the-loop.
