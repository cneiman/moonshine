#!/usr/bin/env node

/**
 * Observer: extracts structured observations from unobserved messages.
 *
 * Usage: node observe.js <session_id> [db_path]
 *
 * 1. Queries unobserved messages for the given session
 * 2. If total estimated tokens < 3,000: exits early (no cost)
 * 3. Calls Anthropic API to extract observations
 * 4. Inserts observations into DB, marks messages as observed
 * 5. Chains to reflector if needed
 *
 * Environment:
 *   ANTHROPIC_API_KEY  Anthropic API key (or ~/.env.anthropic)
 *   OBSERVER_MODEL     Model to use (default: claude-haiku-4-5)
 *   OBSERVER_DB        Path to observations.db (default: ./observations.db)
 */

import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { openDb } from './db.js';

const TOKEN_THRESHOLD = 3000;
const MAX_OBSERVATIONS = 10;
const MODEL = process.env.OBSERVER_MODEL || 'claude-haiku-4-5';

// ---- API Key Resolution ----

function getApiKey() {
  // 1. Environment variable
  if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;

  // 2. ~/.env.anthropic
  try {
    const keyPath = join(process.env.HOME, '.env.anthropic');
    const stats = statSync(keyPath);
    const mode = stats.mode & 0o777;
    if (mode & 0o077) {
      console.error(`[observer] WARNING: ${keyPath} is accessible by other users (mode: ${mode.toString(8)}). Run: chmod 600 ${keyPath}`);
    }
    const envFile = readFileSync(keyPath, 'utf8');
    const match = envFile.match(/ANTHROPIC_API_KEY=(.+)/);
    if (match) return match[1].trim();
  } catch (err) {
    if (err.code !== 'ENOENT') console.error(`[observer] Warning reading ~/.env.anthropic: ${err.message}`);
  }

  throw new Error('No Anthropic API key found. Set ANTHROPIC_API_KEY or add to ~/.env.anthropic');
}

// ---- Observer Prompt ----

const OBSERVER_PROMPT = `You extract observations from AI assistant conversation logs. You output ONLY valid JSON.

## Steps
1. Read all messages below
2. Identify important information: decisions made, work completed, facts learned, preferences stated, problems encountered
3. Write each as a standalone observation sentence
4. Classify priority and type
5. Extract entity names that appear explicitly in the messages
6. Output as a JSON array

## Rules
- Each observation: ONE sentence, max 200 characters
- Max ${MAX_OBSERVATIONS} observations total
- Priority: "high" = decisions, completed work, commitments, blockers. "medium" = context, discussion, preferences. "low" = greetings, acknowledgments, routine
- Type: "event" (something happened), "decision" (choice made or deferred), "lesson" (something learned), "preference" (user preference stated), "insight" (realization)
- Entities: ONLY names explicitly mentioned (people, projects, tools, companies). Never infer entities not in the text.
- For tool output (file reads, API responses, git logs): capture the RESULT or DECISION, not the raw output
- Implicit decisions count: "let's skip that" = decision to defer. "I'll handle it" = decision to take ownership.
- DO NOT summarize the conversation generally. Extract SPECIFIC facts.

## Output Format
Output ONLY this JSON array. No other text. No markdown. No explanation.
[
  {
    "content": "string — one sentence describing what happened",
    "priority": "high" | "medium" | "low",
    "type": "event" | "decision" | "lesson" | "preference" | "insight",
    "observation_date": "YYYY-MM-DD",
    "entities": ["Entity1", "Entity2"]
  }
]

## Examples

### Example 1
Input messages:
- User: "can you deploy the site to vercel?"
- Assistant: "Done — deployed to Vercel at mysite.vercel.app. Added password protection with cookie-based auth."
- User: "perfect"

Output:
[
  {"content": "Deployed site to Vercel with password protection (cookie-based auth)", "priority": "high", "type": "event", "observation_date": "2026-03-18", "entities": ["Vercel"]},
  {"content": "User approved the Vercel deployment", "priority": "low", "type": "event", "observation_date": "2026-03-18", "entities": ["Vercel"]}
]

### Example 2
Input messages:
- User: "lets leave the polling cron. ill finish tailscale and we will use webhooks"
- Assistant: "Makes sense. Todoist webhooks need a public URL which Tailscale would provide."

Output:
[
  {"content": "Decided to skip Todoist polling cron — will use webhooks after Tailscale setup", "priority": "high", "type": "decision", "observation_date": "2026-03-18", "entities": ["Todoist", "Tailscale"]},
  {"content": "Tailscale is prerequisite for Todoist webhook integration", "priority": "medium", "type": "insight", "observation_date": "2026-03-18", "entities": ["Tailscale", "Todoist"]}
]

### BAD outputs (DO NOT do this):
- ❌ "User and assistant discussed various deployment topics" — too vague, no specifics
- ❌ "The conversation covered Todoist and other tools" — summarizing not extracting
- ❌ "Assistant helped user with tasks" — meaningless without specifics
- ❌ "Multiple decisions were made during the session" — which decisions? Be specific
- ❌ Any text that is not valid JSON

## Messages to observe:`;

// ---- Anthropic API Call ----

async function callModel(systemPrompt, userContent, apiKey) {
  const resp = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 4096,
      system: systemPrompt,
      messages: [
        { role: 'user', content: userContent },
        { role: 'assistant', content: '[' },  // Prefill to force JSON array
      ],
    }),
  });

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Anthropic API error ${resp.status}: ${body}`);
  }

  const data = await resp.json();
  const text = data.content?.[0]?.text;
  if (!text) throw new Error('Empty response from Anthropic API');
  return '[' + text;  // Prepend the prefilled bracket
}

// ---- Main ----

export async function observe(sessionId, dbPath, opts = {}) {
  const tokenThreshold = opts.tokenThreshold ?? TOKEN_THRESHOLD;
  const db = openDb(dbPath);

  try {
    // Query unobserved messages for this session
    const messages = db.prepare(`
      SELECT id, role, content, content_type, timestamp, token_estimate
      FROM messages
      WHERE session_id = ? AND observed_at IS NULL
      ORDER BY timestamp ASC
    `).all(sessionId);

    if (messages.length === 0) {
      return { skipped: true, reason: 'no unobserved messages' };
    }

    // Check token threshold
    const totalTokens = messages.reduce((sum, m) => sum + (m.token_estimate || 0), 0);
    if (totalTokens < tokenThreshold) {
      return { skipped: true, reason: `tokens ${totalTokens} < threshold ${tokenThreshold}` };
    }

    // Build conversation text for the observer
    const conversationText = messages.map(m =>
      `[${m.timestamp}] ${m.role}: ${m.content}`
    ).join('\n\n');

    const apiKey = getApiKey();

    // Call model
    const rawResponse = await callModel(OBSERVER_PROMPT, conversationText, apiKey);

    // Parse JSON — handle potential markdown fences
    let cleaned = rawResponse.trim();
    if (cleaned.startsWith('```')) {
      cleaned = cleaned.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '').trim();
    }

    let observations;
    try {
      observations = JSON.parse(cleaned);
    } catch (e) {
      throw new Error(`Failed to parse observer response as JSON: ${e.message}\nRaw: ${rawResponse.slice(0, 500)}`);
    }

    if (!Array.isArray(observations)) {
      throw new Error(`Observer response is not an array: ${typeof observations}`);
    }

    // Cap observations
    observations = observations.slice(0, MAX_OBSERVATIONS);

    // Validate and insert observations
    const messageIds = messages.map(m => m.id);
    const insertObs = db.prepare(`
      INSERT INTO observations (id, session_id, agent_id, content, priority, observation_type, observation_date, generation, source_message_ids, token_count, entities)
      VALUES (?, ?, 'main', ?, ?, ?, ?, 0, ?, ?, ?)
    `);

    const validPriorities = new Set(['high', 'medium', 'low']);
    const validTypes = new Set(['event', 'decision', 'lesson', 'insight', 'preference', 'behavior']);
    const inserted = [];

    const insertAll = db.transaction(() => {
      for (const obs of observations) {
        if (!obs.content || typeof obs.content !== 'string') continue;

        const priority = validPriorities.has(obs.priority) ? obs.priority : 'medium';
        const type = validTypes.has(obs.type) ? obs.type : null;
        const date = obs.observation_date || new Date().toISOString().slice(0, 10);
        const entities = Array.isArray(obs.entities) ? obs.entities : [];
        const tokenCount = Math.ceil(obs.content.length / 4);
        const id = randomUUID();

        insertObs.run(
          id,
          sessionId,
          obs.content,
          priority,
          type,
          date,
          JSON.stringify(messageIds),
          tokenCount,
          JSON.stringify(entities)
        );
        inserted.push({ id, content: obs.content, priority, type });
      }

      // Mark messages as observed
      const markObserved = db.prepare(`
        UPDATE messages SET observed_at = datetime('now', 'localtime')
        WHERE id = ?
      `);
      for (const msg of messages) {
        markObserved.run(msg.id);
      }
    });

    insertAll();

    return {
      skipped: false,
      messagesProcessed: messages.length,
      totalTokens,
      observationsCreated: inserted.length,
      observations: inserted,
    };
  } finally {
    db.close();
  }
}

// ---- CLI Entry Point ----

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const sessionId = process.argv[2];
  if (!sessionId) {
    console.error('Usage: node observe.js <session_id> [db_path]');
    process.exit(1);
  }

  const dbPath = process.argv[3] || undefined;

  observe(sessionId, dbPath)
    .then(result => {
      if (result.skipped) {
        console.error(`Observer skipped: ${result.reason}`);
      } else {
        console.error(`Observer: ${result.observationsCreated} observations from ${result.messagesProcessed} messages (${result.totalTokens} tokens)`);
      }

      // Chain to reflector if observations exist
      if (!result.skipped && result.observationsCreated > 0) {
        import('./reflect.js').then(mod => mod.reflect(sessionId, dbPath)).then(reflectResult => {
          if (reflectResult.skipped) {
            console.error(`Reflector skipped: ${reflectResult.reason}`);
          } else {
            console.error(`Reflector: condensed to ${reflectResult.observationsCreated} observations, superseded ${reflectResult.supersededCount}`);
          }
        }).catch(err => {
          console.error(`Reflector error (non-fatal): ${err.message}`);
        });
      }
    })
    .catch(err => {
      console.error(`Observer error: ${err.message}`);
      process.exit(1);
    });
}
