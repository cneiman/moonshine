#!/usr/bin/env node
/**
 * Generic file watcher adapter for moonshine.
 *
 * Watches a directory for .jsonl transcript files and feeds new lines
 * to the observer pipeline for automatic memory extraction.
 *
 * Usage:
 *   MOONSHINE_WATCH_DIR=/path/to/transcripts node watcher.js
 *
 * Environment:
 *   MOONSHINE_WATCH_DIR  — directory to watch (required)
 *   OBSERVER_DB          — path to observations.db (default: ./observations.db)
 *   ANTHROPIC_API_KEY    — for observer LLM calls
 */

import { watch, readFileSync, statSync } from "fs";
import { join, basename } from "path";
import { spawn } from "child_process";
import { createRequire } from "node:module";
import { randomUUID } from "node:crypto";

const WATCH_DIR = process.env.MOONSHINE_WATCH_DIR;
if (!WATCH_DIR) {
  console.error(
    "Error: MOONSHINE_WATCH_DIR environment variable is required."
  );
  console.error(
    "Usage: MOONSHINE_WATCH_DIR=/path/to/transcripts node watcher.js"
  );
  process.exit(1);
}

const OBSERVER_SCRIPT =
  process.env.OBSERVER_SCRIPT ||
  join(import.meta.dirname, "..", "..", "observer", "observe.js");
const OBSERVER_DIR = join(import.meta.dirname, "..", "..", "observer");
const DB_PATH =
  process.env.OBSERVER_DB || join(OBSERVER_DIR, "observations.db");
const TOKEN_TRIGGER_THRESHOLD = 3000;

const fileOffsets = new Map();

// --- Lazy DB loading (mirrors openclaw handler pattern) ---
let _db = null;
let _dbLoadFailed = false;

function getDb() {
  if (_dbLoadFailed) return null;
  if (_db) return _db;

  try {
    const require = createRequire(join(OBSERVER_DIR, "package.json"));
    const Database = require("better-sqlite3");
    _db = new Database(DB_PATH);
    _db.pragma("journal_mode = WAL");
    _db.pragma("busy_timeout = 5000");

    _db.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT DEFAULT 'main',
        channel TEXT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        content_type TEXT DEFAULT 'text',
        timestamp TEXT NOT NULL,
        inserted_at TEXT DEFAULT (datetime('now', 'localtime')),
        token_estimate INTEGER,
        observed_at TEXT,
        metadata TEXT DEFAULT '{}'
      );

      CREATE TABLE IF NOT EXISTS observations (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT DEFAULT 'main',
        content TEXT NOT NULL,
        priority TEXT NOT NULL CHECK (priority IN ('high', 'medium', 'low')),
        observation_type TEXT,
        observation_date TEXT NOT NULL DEFAULT (date('now', 'localtime')),
        generation INTEGER DEFAULT 0,
        superseded_at TEXT,
        source_message_ids TEXT DEFAULT '[]',
        token_count INTEGER,
        entities TEXT DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
      );

      CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
      CREATE INDEX IF NOT EXISTS idx_messages_unobserved ON messages(session_id, observed_at) WHERE observed_at IS NULL;
      CREATE INDEX IF NOT EXISTS idx_messages_agent ON messages(agent_id, timestamp DESC);
      CREATE INDEX IF NOT EXISTS idx_observations_active ON observations(session_id, superseded_at, token_count) WHERE superseded_at IS NULL;
      CREATE INDEX IF NOT EXISTS idx_observations_priority ON observations(priority, created_at DESC) WHERE superseded_at IS NULL;
      CREATE INDEX IF NOT EXISTS idx_observations_date ON observations(observation_date DESC) WHERE superseded_at IS NULL;
    `);

    return _db;
  } catch (err) {
    _dbLoadFailed = true;
    console.error("[watcher] Failed to load DB:", err.message);
    return null;
  }
}

// --- Observer trigger ---
let _observerRunning = false;

function checkAndTriggerObserver(sessionId) {
  const db = getDb();
  if (!db) return;

  try {
    const result = db
      .prepare(
        `SELECT COALESCE(SUM(token_estimate), 0) as total_tokens
         FROM messages
         WHERE session_id = ? AND observed_at IS NULL`
      )
      .get(sessionId);

    if (result.total_tokens >= TOKEN_TRIGGER_THRESHOLD) {
      spawnObserver(sessionId);
    }
  } catch (err) {
    console.error("[watcher] Trigger check error:", err.message);
  }
}

function spawnObserver(sessionId) {
  if (_observerRunning) return;
  _observerRunning = true;

  try {
    const child = spawn("node", [OBSERVER_SCRIPT, sessionId, DB_PATH], {
      detached: true,
      stdio: ["ignore", "ignore", "pipe"],
      env: { ...process.env, OBSERVER_DB: DB_PATH },
    });

    child.stderr?.on("data", (data) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[watcher] observer: ${msg}`);
    });

    child.on("close", () => {
      _observerRunning = false;
    });

    child.on("error", (err) => {
      _observerRunning = false;
      console.error("[watcher] Spawn error:", err.message);
    });

    child.unref();
  } catch (err) {
    _observerRunning = false;
    console.error("[watcher] Spawn failed:", err.message);
  }
}

function processNewLines(filepath) {
  try {
    const stat = statSync(filepath);
    const prevSize = fileOffsets.get(filepath) || 0;

    if (stat.size <= prevSize) return;

    const content = readFileSync(filepath, "utf8");
    const lines = content.split("\n").filter((l) => l.trim());
    const prevLines = fileOffsets.has(filepath)
      ? Math.max(
          0,
          content.substring(0, prevSize).split("\n").filter((l) => l.trim())
            .length
        )
      : 0;

    const newLines = lines.slice(prevLines);
    fileOffsets.set(filepath, stat.size);

    if (newLines.length === 0) return;

    const sessionId =
      basename(filepath, ".jsonl") || `session-${Date.now()}`;

    const db = getDb();
    let insertedCount = 0;

    for (const line of newLines) {
      try {
        const msg = JSON.parse(line);
        if (!msg.role || !msg.content) continue;

        console.log(
          `[${sessionId}] ${msg.role}: ${msg.content.substring(0, 80)}...`
        );

        if (db) {
          const tokenEstimate = Math.ceil(msg.content.length / 4);
          const timestamp =
            msg.timestamp || new Date().toISOString();

          db.prepare(
            `INSERT OR IGNORE INTO messages (id, session_id, agent_id, channel, role, content, content_type, timestamp, token_estimate, metadata)
             VALUES (?, ?, 'main', NULL, ?, ?, 'text', ?, ?, '{}')`
          ).run(
            randomUUID(),
            sessionId,
            msg.role,
            msg.content,
            timestamp,
            tokenEstimate
          );
          insertedCount++;
        }
      } catch {
        // Skip malformed lines
      }
    }

    console.log(
      `[${sessionId}] Processed ${newLines.length} new line(s), inserted ${insertedCount} message(s) from ${basename(filepath)}`
    );

    // Check if unobserved tokens exceed threshold and trigger observer
    if (insertedCount > 0) {
      checkAndTriggerObserver(sessionId);
    }
  } catch (err) {
    console.error(`Error processing ${filepath}: ${err.message}`);
  }
}

console.log(`👁️ Watching ${WATCH_DIR} for .jsonl transcripts...`);

watch(WATCH_DIR, (eventType, filename) => {
  if (!filename || !filename.endsWith(".jsonl")) return;
  const filepath = join(WATCH_DIR, filename);
  processNewLines(filepath);
});

// Initial scan
import { readdirSync } from "fs";
for (const file of readdirSync(WATCH_DIR)) {
  if (file.endsWith(".jsonl")) {
    const filepath = join(WATCH_DIR, file);
    const stat = statSync(filepath);
    fileOffsets.set(filepath, stat.size);
    console.log(`  Tracking: ${file} (${stat.size} bytes)`);
  }
}

console.log(`Ready. Waiting for new transcript data...`);
