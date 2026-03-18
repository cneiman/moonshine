/**
 * Conversation Observer Hook for OpenClaw
 *
 * Captures every message in the main agent session to observations.db,
 * then triggers the observer pipeline when unobserved tokens exceed threshold.
 *
 * Fire-and-forget: never blocks the response pipeline.
 *
 * Environment:
 *   OBSERVER_DB       Path to observations.db (default: <workspace>/observations.db)
 *   OBSERVER_SCRIPT   Path to observe.js (default: <workspace>/observer/observe.js)
 */

import { join } from 'node:path';
import { spawn } from 'node:child_process';

// --- Configuration ---
const MAX_CONTENT_LENGTH = 2000;
const TOKEN_TRIGGER_THRESHOLD = 3000;

// Resolve paths from environment or workspace-relative defaults
const WORKSPACE = process.env.AGENT_MEMORY_WORKSPACE || process.cwd();
const DB_PATH = process.env.OBSERVER_DB || join(WORKSPACE, 'observations.db');
const OBSERVER_SCRIPT = process.env.OBSERVER_SCRIPT || join(WORKSPACE, 'observer', 'observe.js');

// --- Lazy DB loading ---
let _db: any = null;
let _dbLoadFailed = false;

function getDb() {
  if (_dbLoadFailed) return null;
  if (_db) return _db;

  try {
    // Dynamic require since better-sqlite3 is native
    const Database = require('better-sqlite3');
    _db = new Database(DB_PATH);
    _db.pragma('journal_mode = WAL');
    _db.pragma('busy_timeout = 5000');

    // Ensure tables exist (idempotent)
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
    console.error('[conversation-observer] Failed to load DB:', (err as Error).message);
    return null;
  }
}

// --- UUID generation ---
function uuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// --- Message capture ---
function captureMessage(event: any) {
  const db = getDb();
  if (!db) return;

  try {
    const ctx = event.context || {};
    const isReceived = event.action === 'received';

    // Extract content
    let content = ctx.content;
    if (!content || typeof content !== 'string') return;

    // Truncate long content (tool output etc.)
    if (content.length > MAX_CONTENT_LENGTH) {
      content = content.slice(0, MAX_CONTENT_LENGTH) + `\n[truncated: ${content.length - MAX_CONTENT_LENGTH} chars]`;
    }

    const role = isReceived ? 'user' : 'assistant';
    const channel = ctx.channelId || null;
    const timestamp = event.timestamp
      ? new Date(event.timestamp).toISOString()
      : new Date().toISOString();

    const sessionId = event.sessionId || event.sessionKey || 'unknown';

    let agentId = 'main';
    if (event.sessionKey) {
      const parts = event.sessionKey.split(':');
      if (parts.length >= 2) agentId = parts[1];
    }

    const tokenEstimate = Math.ceil(content.length / 4);
    const metadata = JSON.stringify({
      senderName: ctx.metadata?.senderName || null,
      messageId: ctx.messageId || null,
      conversationId: ctx.conversationId || null,
    });

    db.prepare(`
      INSERT OR IGNORE INTO messages (id, session_id, agent_id, channel, role, content, content_type, timestamp, token_estimate, metadata)
      VALUES (?, ?, ?, ?, ?, ?, 'text', ?, ?, ?)
    `).run(
      uuid(),
      sessionId,
      agentId,
      channel,
      role,
      content,
      timestamp,
      tokenEstimate,
      metadata
    );

    // Check if we should trigger the observer
    checkAndTriggerObserver(sessionId);
  } catch (err) {
    console.error('[conversation-observer] Capture error:', (err as Error).message);
  }
}

// --- Observer trigger ---
function checkAndTriggerObserver(sessionId: string) {
  const db = getDb();
  if (!db) return;

  try {
    const result = db.prepare(`
      SELECT COALESCE(SUM(token_estimate), 0) as total_tokens
      FROM messages
      WHERE session_id = ? AND observed_at IS NULL
    `).get(sessionId) as { total_tokens: number };

    if (result.total_tokens >= TOKEN_TRIGGER_THRESHOLD) {
      spawnObserver(sessionId);
    }
  } catch (err) {
    console.error('[conversation-observer] Trigger check error:', (err as Error).message);
  }
}

// --- Spawn observer as fire-and-forget child process ---
let _observerRunning = false;

function spawnObserver(sessionId: string) {
  if (_observerRunning) return; // Debounce: only one observer at a time
  _observerRunning = true;

  try {
    const child = spawn('node', [OBSERVER_SCRIPT, sessionId], {
      detached: true,
      stdio: ['ignore', 'ignore', 'pipe'],
      env: { ...process.env, HOME: process.env.HOME },
    });

    child.stderr?.on('data', (data: Buffer) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[conversation-observer] ${msg}`);
    });

    child.on('close', () => {
      _observerRunning = false;
    });

    child.on('error', (err: Error) => {
      _observerRunning = false;
      console.error('[conversation-observer] Spawn error:', err.message);
    });

    child.unref();
  } catch (err) {
    _observerRunning = false;
    console.error('[conversation-observer] Spawn failed:', (err as Error).message);
  }
}

// --- Hook Handler ---
const handler = async (event: any) => {
  // Filter: only main agent sessions (customize this for your setup)
  if (event.sessionKey && event.sessionKey !== 'agent:main:main') return;

  if (event.type === 'message') {
    if (event.action === 'received' || event.action === 'sent') {
      // Fire and forget — never block
      void Promise.resolve().then(() => captureMessage(event));
    }
  }

  if (event.type === 'session' && event.action === 'compact:before') {
    // Compaction imminent — trigger observer NOW for any pending messages
    const sessionId = event.sessionId || event.sessionKey || 'agent:main:main';
    void Promise.resolve().then(() => spawnObserver(sessionId));
  }
};

export default handler;
