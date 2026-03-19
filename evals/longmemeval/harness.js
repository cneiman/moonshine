#!/usr/bin/env node

/**
 * LongMemEval Benchmark Harness for Moonshine
 *
 * Ingests conversation sessions into a fresh SQLite memory DB,
 * searches via FTS5, generates answers via Anthropic Claude,
 * and appends hypotheses to a JSONL file for evaluation.
 *
 * Usage:
 *   node harness.js --dataset oracle --limit 10
 *   node harness.js --dataset oracle --start 50 --limit 20
 *   node harness.js --dataset s
 */

import { createRequire } from "module";
const require = createRequire(import.meta.url);
const Database = require("better-sqlite3");

import { readFileSync, appendFileSync, existsSync, unlinkSync } from "fs";
import { execSync } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── CLI Args ────────────────────────────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    dataset: "oracle",
    start: 0,
    limit: Infinity,
    output: null,
    model: null,
    search: "hybrid",
    temporal: false,
    rerank: false,
  };
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case "--dataset":
        opts.dataset = args[++i];
        break;
      case "--start":
        opts.start = parseInt(args[++i], 10);
        break;
      case "--limit":
        opts.limit = parseInt(args[++i], 10);
        break;
      case "--output":
        opts.output = args[++i];
        break;
      case "--model":
        opts.model = args[++i];
        break;
      case "--search":
        opts.search = args[++i];
        break;
      case "--temporal":
        opts.temporal = true;
        break;
      case "--rerank":
        opts.rerank = true;
        break;
      case "--help":
        console.log(`
LongMemEval Harness

  --dataset oracle|s   Dataset to use (default: oracle)
  --start N            Start from question index N (0-indexed, for resuming)
  --limit N            Process at most N questions
  --output FILE        Output JSONL file (default: hypotheses-{dataset}.jsonl)
  --model MODEL        Anthropic model for answering (default: claude-opus-4-6 or EVAL_MODEL env)
  --search MODE        Search mode: fts|semantic|hybrid (default: hybrid)
  --temporal           Enable temporal filtering on queries
  --rerank             Enable cross-encoder reranking
  --help               Show this help
`);
        process.exit(0);
    }
  }
  return opts;
}

// ── Anthropic API Key ───────────────────────────────────────────────────────
function getAnthropicKey() {
  if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;
  const envFile = join(process.env.HOME || "~", ".env.anthropic");
  if (existsSync(envFile)) {
    const content = readFileSync(envFile, "utf8");
    const match = content.match(/ANTHROPIC_API_KEY=(.+)/);
    if (match) return match[1].trim();
  }
  throw new Error("No ANTHROPIC_API_KEY found in env or ~/.env.anthropic");
}

// ── Schema ──────────────────────────────────────────────────────────────────
const SCHEMA_PATH = join(__dirname, "../../core/schema.sql");

function createFreshDb(dbPath) {
  if (existsSync(dbPath)) unlinkSync(dbPath);
  const db = new Database(dbPath);
  db.pragma("journal_mode = WAL");
  db.pragma("synchronous = OFF"); // speed over safety for temp eval DBs

  const schema = readFileSync(SCHEMA_PATH, "utf8");
  // Execute schema statements one at a time (better-sqlite3 can handle multi-statement)
  db.exec(schema);

  return db;
}

// ── Ingestion ───────────────────────────────────────────────────────────────
function ingestSessions(db, sessions, dates, sessionIds) {
  const insert = db.prepare(`
    INSERT INTO memories (type, title, content, importance, source, source_date, metadata)
    VALUES (@type, @title, @content, @importance, @source, @source_date, @metadata)
  `);

  const insertMany = db.transaction((items) => {
    for (const item of items) insert.run(item);
  });

  const rows = [];
  for (let si = 0; si < sessions.length; si++) {
    const session = sessions[si];
    const date = dates[si]; // e.g. "2023/04/10 (Mon) 17:50"
    const sessionId = sessionIds[si];
    // Parse the date into a sortable format
    const isoDate = parseDate(date);

    for (let ti = 0; ti < session.length; ti++) {
      const turn = session[ti];
      const role = turn.role; // "user" or "assistant"
      rows.push({
        type: "event",
        title: `${role} message (session ${si + 1}, turn ${ti + 1})`,
        content: turn.content,
        importance: 3,
        source: `longmemeval:${sessionId}`,
        source_date: isoDate,
        metadata: JSON.stringify({
          session_index: si,
          turn_index: ti,
          role,
          session_id: sessionId,
          original_date: date,
        }),
      });
    }
  }

  insertMany(rows);
  return rows.length;
}

function parseDate(dateStr) {
  // "2023/04/10 (Mon) 17:50" → "2023-04-10"
  if (!dateStr) return null;
  const match = dateStr.match(/(\d{4})\/(\d{2})\/(\d{2})/);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  return null;
}

// ── Search ──────────────────────────────────────────────────────────────────
function searchFTS(db, query, limit = 20) {
  // Sanitize query for FTS5: remove special chars that break FTS
  const sanitized = sanitizeFtsQuery(query);
  if (!sanitized) return [];

  try {
    const stmt = db.prepare(`
      SELECT m.id, m.content, m.source_date, m.metadata, f.rank
      FROM memories_fts f
      JOIN memories m ON m.id = f.rowid
      WHERE memories_fts MATCH @query
      ORDER BY f.rank
      LIMIT @limit
    `);
    return stmt.all({ query: sanitized, limit });
  } catch {
    // If FTS match fails (e.g. bad syntax), fall back to LIKE
    return searchLike(db, query, limit);
  }
}

function searchLike(db, query, limit = 20) {
  // Extract key terms from the question
  const words = query
    .toLowerCase()
    .split(/\s+/)
    .filter((w) => w.length > 3)
    .slice(0, 8);

  if (words.length === 0) return [];

  // Match any of the key terms
  const conditions = words.map((_, i) => `LOWER(content) LIKE @w${i}`);
  const sql = `
    SELECT id, content, source_date, metadata
    FROM memories
    WHERE ${conditions.join(" OR ")}
    ORDER BY source_date DESC
    LIMIT @limit
  `;

  const params = { limit };
  words.forEach((w, i) => {
    params[`w${i}`] = `%${w}%`;
  });

  return db.prepare(sql).all(params);
}

function sanitizeFtsQuery(query) {
  // Remove FTS5 special characters, keep words
  const words = query
    .replace(/[^\w\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2);

  if (words.length === 0) return null;

  // Use OR-based matching for better recall
  return words.join(" OR ");
}

// ── Pipeline Search (via search_helper.py) ──────────────────────────────
const SEARCH_HELPER = join(__dirname, "search_helper.py");

function searchWithPipeline(dbPath, query, opts, questionDate) {
  const args = [
    SEARCH_HELPER,
    dbPath,
    query,
    "--search", opts.search,
    "--limit", "20",
  ];
  if (opts.temporal) args.push("--temporal");
  if (opts.rerank) args.push("--rerank");
  if (questionDate) args.push("--question-date", questionDate);

  try {
    const stdout = execSync(`python3 ${args.map(a => JSON.stringify(a)).join(" ")}`, {
      encoding: "utf8",
      timeout: 120_000, // 2 min — embedding can be slow on first run
      maxBuffer: 10 * 1024 * 1024,
      env: {
        ...process.env,
        MOONSHINE_RERANK: opts.rerank ? "true" : "false",
      },
    });
    // search_helper prints JSON to stdout, diagnostics to stderr
    const lines = stdout.trim().split("\n");
    const jsonLine = lines[lines.length - 1]; // last line is the JSON
    return JSON.parse(jsonLine);
  } catch (err) {
    console.error(`  Pipeline search failed: ${err.message}`);
    console.error("  Falling back to inline FTS...");
    return null; // caller will fall back to searchFTS
  }
}

// ── Anthropic API ───────────────────────────────────────────────────────────
async function callAnthropic(apiKey, model, system, userMessage) {
  const maxRetries = 3;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model,
          max_tokens: 512,
          system,
          messages: [{ role: "user", content: userMessage }],
        }),
      });

      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get("retry-after") || "10", 10);
        console.log(`  Rate limited, waiting ${retryAfter}s...`);
        await sleep(retryAfter * 1000);
        continue;
      }

      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Anthropic API error ${res.status}: ${body}`);
      }

      const data = await res.json();
      return data.content?.[0]?.text || "";
    } catch (err) {
      if (attempt < maxRetries - 1) {
        console.log(`  API error (attempt ${attempt + 1}): ${err.message}, retrying...`);
        await sleep(2000 * (attempt + 1));
      } else {
        throw err;
      }
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs();
  const apiKey = getAnthropicKey();
  const model = opts.model || process.env.EVAL_MODEL || "claude-opus-4-6";
  const outputFile = opts.output || `hypotheses-${opts.dataset}.jsonl`;

  // Load dataset
  const dataFile =
    opts.dataset === "oracle"
      ? join(__dirname, "data/longmemeval_oracle.json")
      : join(__dirname, "data/longmemeval_s.json");

  console.log(`Loading dataset: ${dataFile}`);
  const dataset = JSON.parse(readFileSync(dataFile, "utf8"));
  console.log(`Loaded ${dataset.length} questions`);

  // Determine which questions already have hypotheses (for resume)
  const answered = new Set();
  if (existsSync(outputFile)) {
    const lines = readFileSync(outputFile, "utf8").trim().split("\n").filter(Boolean);
    for (const line of lines) {
      try {
        const obj = JSON.parse(line);
        answered.add(obj.question_id);
      } catch {
        // skip malformed lines
      }
    }
    console.log(`Found ${answered.size} existing hypotheses in ${outputFile}`);
  }

  // Slice dataset based on --start and --limit
  const end = Math.min(opts.start + opts.limit, dataset.length);
  const questions = dataset.slice(opts.start, end);
  console.log(
    `Processing questions ${opts.start} to ${end - 1} (${questions.length} questions)`
  );
  const usePipeline = opts.search !== "fts" || opts.temporal || opts.rerank;
  console.log(`Model: ${model}`);
  console.log(`Search: ${opts.search}${opts.temporal ? " +temporal" : ""}${opts.rerank ? " +rerank" : ""}`);
  console.log(`Pipeline: ${usePipeline ? "search_helper.py" : "inline FTS"}`);
  console.log(`Output: ${outputFile}`);
  console.log("─".repeat(60));

  const tmpDbPath = join(__dirname, ".tmp-eval.db");
  const times = [];
  let processed = 0;
  let skipped = 0;

  for (let qi = 0; qi < questions.length; qi++) {
    const q = questions[qi];
    const globalIdx = opts.start + qi;

    // Skip if already answered
    if (answered.has(q.question_id)) {
      skipped++;
      continue;
    }

    const t0 = Date.now();

    try {
      // 1. Create fresh DB
      const db = createFreshDb(tmpDbPath);

      // 2. Ingest sessions
      const turnCount = ingestSessions(
        db,
        q.haystack_sessions,
        q.haystack_dates,
        q.haystack_session_ids
      );

      // 3. Search — use pipeline or inline FTS
      let results;
      if (usePipeline) {
        // Close DB first so Python can open it
        db.close();
        results = searchWithPipeline(tmpDbPath, q.question, opts, q.question_date);
        if (!results) {
          // Pipeline failed — reopen DB and fall back to FTS
          const fallbackDb = new Database(tmpDbPath);
          results = searchFTS(fallbackDb, q.question, 20);
          fallbackDb.close();
        }
      } else {
        results = searchFTS(db, q.question, 20);
        db.close();
      }

      // 5. Build context from search results
      let context;
      if (results.length > 0) {
        context = results
          .map((r, i) => {
            const meta = r.metadata ? JSON.parse(r.metadata) : {};
            const dateStr = r.source_date || "unknown date";
            const role = meta.role || "unknown";
            return `[${i + 1}] (${dateStr}, ${role}): ${r.content}`;
          })
          .join("\n\n");
      } else {
        // Fall back: if FTS returned nothing, use all content (shouldn't happen often)
        context = "(No relevant memories found)";
      }

      // 6. Generate answer
      const systemPrompt =
        "You are answering questions about past conversations. Use the provided context to answer accurately and concisely.";
      const userPrompt = `Context:\n${context}\n\nQuestion: ${q.question}\n\nAnswer concisely based on the context. If the information isn't in the context, say 'I don't have that information.'`;

      const hypothesis = await callAnthropic(apiKey, model, systemPrompt, userPrompt);

      // 7. Append to JSONL
      const entry = {
        question_id: q.question_id,
        hypothesis: hypothesis.trim(),
      };
      appendFileSync(outputFile, JSON.stringify(entry) + "\n");

      const elapsed = Date.now() - t0;
      times.push(elapsed);
      processed++;

      const avgTime = times.reduce((a, b) => a + b, 0) / times.length;
      const remaining = questions.length - qi - 1 - skipped;
      const eta = Math.round((avgTime * remaining) / 1000 / 60);

      console.log(
        `Question ${globalIdx + 1}/${dataset.length} [${q.question_type}] ` +
          `${(elapsed / 1000).toFixed(1)}s | ${results.length} results | ` +
          `ETA: ${eta}min | ` +
          `"${q.question.slice(0, 60)}..."`
      );
    } catch (err) {
      console.error(
        `ERROR on question ${globalIdx + 1} (${q.question_id}): ${err.message}`
      );
      // Continue to next question — don't crash the whole run
    } finally {
      // Cleanup temp DB
      try {
        if (existsSync(tmpDbPath)) unlinkSync(tmpDbPath);
        if (existsSync(tmpDbPath + "-wal")) unlinkSync(tmpDbPath + "-wal");
        if (existsSync(tmpDbPath + "-shm")) unlinkSync(tmpDbPath + "-shm");
      } catch {
        // ignore cleanup errors
      }
    }
  }

  console.log("─".repeat(60));
  console.log(`Done! Processed: ${processed}, Skipped (already done): ${skipped}`);
  if (times.length > 0) {
    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    const total = times.reduce((a, b) => a + b, 0);
    console.log(
      `Avg time per question: ${(avg / 1000).toFixed(1)}s, Total: ${(total / 1000 / 60).toFixed(1)}min`
    );
  }
  console.log(`Hypotheses saved to: ${outputFile}`);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
