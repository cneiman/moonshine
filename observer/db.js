/**
 * Shared database initialization for the observer pipeline.
 * Creates observations.db with WAL mode, both tables, and all indexes.
 *
 * Environment:
 *   OBSERVER_DB  Path to observations.db (default: ./observations.db)
 */

import Database from 'better-sqlite3';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_DB_PATH = process.env.OBSERVER_DB || join(__dirname, 'observations.db');

/**
 * Open (and initialize if needed) the observations database.
 * @param {string} [dbPath] — override path for testing
 * @returns {import('better-sqlite3').Database}
 */
export function openDb(dbPath = DEFAULT_DB_PATH) {
  const db = new Database(dbPath);

  // WAL mode for concurrent reads/writes
  db.pragma('journal_mode = WAL');
  db.pragma('busy_timeout = 5000');

  // Create tables if they don't exist
  db.exec(`
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

    CREATE INDEX IF NOT EXISTS idx_messages_session
      ON messages(session_id);

    CREATE INDEX IF NOT EXISTS idx_messages_unobserved
      ON messages(session_id, observed_at)
      WHERE observed_at IS NULL;

    CREATE INDEX IF NOT EXISTS idx_messages_agent
      ON messages(agent_id, timestamp DESC);

    CREATE INDEX IF NOT EXISTS idx_observations_active
      ON observations(session_id, superseded_at, token_count)
      WHERE superseded_at IS NULL;

    CREATE INDEX IF NOT EXISTS idx_observations_priority
      ON observations(priority, created_at DESC)
      WHERE superseded_at IS NULL;

    CREATE INDEX IF NOT EXISTS idx_observations_date
      ON observations(observation_date DESC)
      WHERE superseded_at IS NULL;
  `);

  return db;
}
