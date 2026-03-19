#!/usr/bin/env node

/**
 * Reflector: condenses active observations into tighter, higher-quality set.
 *
 * Usage: node reflect.js <session_id> [db_path]
 *
 * 1. Queries active (non-superseded) observations for the session
 * 2. If total tokens < 4,000: exits early
 * 3. Calls Anthropic API with reflector prompt + observations (including IDs)
 * 4. Validates: superseded_ids exist in input, output count < input count
 * 5. Inserts condensed observations (generation + 1)
 * 6. Soft-tombstones superseded observations
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

const TOKEN_THRESHOLD = 4000;
const MODEL = process.env.OBSERVER_MODEL || 'claude-haiku-4-5';

// ---- API Key Resolution ----

function getApiKey() {
  if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;

  try {
    const keyPath = join(process.env.HOME, '.env.anthropic');
    const stats = statSync(keyPath);
    const mode = stats.mode & 0o777;
    if (mode & 0o077) {
      console.error(`[reflector] WARNING: ${keyPath} is accessible by other users (mode: ${mode.toString(8)}). Run: chmod 600 ${keyPath}`);
    }
    const envFile = readFileSync(keyPath, 'utf8');
    const match = envFile.match(/ANTHROPIC_API_KEY=(.+)/);
    if (match) return match[1].trim();
  } catch (err) {
    if (err.code !== 'ENOENT') console.error(`[reflector] Warning reading ~/.env.anthropic: ${err.message}`);
  }

  throw new Error('No Anthropic API key found. Set ANTHROPIC_API_KEY or add to ~/.env.anthropic');
}

// ---- Reflector Prompt ----

const REFLECTOR_PROMPT = `You condense observations by merging related items and removing outdated ones. You output ONLY valid JSON.

## Steps
1. Read all observations below (each has an ID)
2. Find observations about the SAME event/decision that can be merged into one richer observation
3. Identify observations superseded by newer information
4. Drop low-priority items that add no lasting value
5. Output condensed observations + list of superseded IDs

## Rules
- Output must have FEWER observations than input
- Only merge observations about the SAME specific event or decision
- Do NOT merge observations that are merely topically related (e.g., don't merge "deployed site" with "fixed bug" just because both involve code)
- Preserve ALL high-priority observations unless truly superseded by newer info
- You may upgrade priority (medium→high) if context warrants it
- Each condensed observation: ONE sentence, max 250 characters
- superseded_ids must ONLY contain IDs from the input list — never invent IDs
- When merging, keep the most specific details from each source

## Output Format
Output ONLY this JSON object. No other text. No markdown. No explanation.
{
  "observations": [
    {
      "content": "string",
      "priority": "high" | "medium" | "low",
      "type": "event" | "decision" | "lesson" | "preference" | "insight",
      "observation_date": "YYYY-MM-DD",
      "entities": ["Entity1"]
    }
  ],
  "superseded_ids": ["id-1", "id-2"]
}

## Example

Input:
- [id-1] "Started building the QA dashboard MVP" (high, event)
- [id-2] "QA dashboard uses Next.js 15 and Playwright" (medium, event)
- [id-3] "QA dashboard MVP completed with 64 tests passing" (high, event)
- [id-4] "User said good morning" (low, event)

Output:
{
  "observations": [
    {"content": "QA dashboard MVP completed: Next.js 15 + Playwright + axe-core, 64 tests passing", "priority": "high", "type": "event", "observation_date": "2026-03-12", "entities": ["QA dashboard"]}
  ],
  "superseded_ids": ["id-1", "id-2", "id-3", "id-4"]
}

### BAD outputs:
- ❌ More observations in output than input — must be FEWER
- ❌ superseded_ids containing IDs not in the input list
- ❌ Merging unrelated observations just because they're from the same day
- ❌ Any text outside the JSON object

## Observations to condense:`;

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
        { role: 'assistant', content: '{' },  // Prefill to force JSON object
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
  return '{' + text;
}

// ---- Main ----

export async function reflect(sessionId, dbPath, opts = {}) {
  const tokenThreshold = opts.tokenThreshold ?? TOKEN_THRESHOLD;
  const db = openDb(dbPath);

  try {
    // Query active (non-superseded) observations for this session
    const observations = db.prepare(`
      SELECT id, content, priority, observation_type, observation_date, generation, token_count, entities
      FROM observations
      WHERE session_id = ? AND superseded_at IS NULL
      ORDER BY created_at ASC
    `).all(sessionId);

    if (observations.length < 3) {
      return { skipped: true, reason: `only ${observations.length} active observations (need ≥ 3)` };
    }

    // Check token threshold
    const totalTokens = observations.reduce((sum, o) => sum + (o.token_count || 0), 0);
    if (totalTokens < tokenThreshold) {
      return { skipped: true, reason: `tokens ${totalTokens} < threshold ${tokenThreshold}` };
    }

    // Build input for reflector (include IDs)
    const inputObservations = observations.map(o => ({
      id: o.id,
      content: o.content,
      priority: o.priority,
      type: o.observation_type,
      observation_date: o.observation_date,
      generation: o.generation,
      entities: JSON.parse(o.entities || '[]'),
    }));

    const userContent = JSON.stringify(inputObservations, null, 2);
    const apiKey = getApiKey();

    // Call model
    const rawResponse = await callModel(REFLECTOR_PROMPT, userContent, apiKey);

    // Parse JSON
    let cleaned = rawResponse.trim();
    if (cleaned.startsWith('```')) {
      cleaned = cleaned.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '').trim();
    }

    let result;
    try {
      result = JSON.parse(cleaned);
    } catch (e) {
      throw new Error(`Failed to parse reflector response: ${e.message}\nRaw: ${rawResponse.slice(0, 500)}`);
    }

    // Validate structure
    if (!result.observations || !Array.isArray(result.observations)) {
      throw new Error('Reflector response missing observations array');
    }
    if (!result.superseded_ids || !Array.isArray(result.superseded_ids)) {
      throw new Error('Reflector response missing superseded_ids array');
    }

    // Validate: output count < input count
    if (result.observations.length >= observations.length) {
      throw new Error(`Reflector output (${result.observations.length}) must be fewer than input (${observations.length})`);
    }

    // Validate: superseded_ids must exist in input set
    const inputIds = new Set(observations.map(o => o.id));
    const invalidIds = result.superseded_ids.filter(id => !inputIds.has(id));
    if (invalidIds.length > 0) {
      throw new Error(`Reflector referenced unknown IDs: ${invalidIds.join(', ')}`);
    }

    // Calculate max generation from input
    const maxGeneration = Math.max(...observations.map(o => o.generation));
    const newGeneration = maxGeneration + 1;

    const validPriorities = new Set(['high', 'medium', 'low']);
    const validTypes = new Set(['event', 'decision', 'lesson', 'insight', 'preference', 'behavior']);

    const insertObs = db.prepare(`
      INSERT INTO observations (id, session_id, agent_id, content, priority, observation_type, observation_date, generation, source_message_ids, token_count, entities)
      VALUES (?, ?, 'main', ?, ?, ?, ?, ?, '[]', ?, ?)
    `);

    const markSuperseded = db.prepare(`
      UPDATE observations SET superseded_at = datetime('now', 'localtime')
      WHERE id = ?
    `);

    const inserted = [];

    const reflectAll = db.transaction(() => {
      // Insert new condensed observations
      for (const obs of result.observations) {
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
          newGeneration,
          tokenCount,
          JSON.stringify(entities)
        );
        inserted.push({ id, content: obs.content, priority, type, generation: newGeneration });
      }

      // Soft-tombstone superseded observations
      for (const sid of result.superseded_ids) {
        markSuperseded.run(sid);
      }
    });

    reflectAll();

    return {
      skipped: false,
      inputCount: observations.length,
      inputTokens: totalTokens,
      observationsCreated: inserted.length,
      supersededCount: result.superseded_ids.length,
      newGeneration,
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
    console.error('Usage: node reflect.js <session_id> [db_path]');
    process.exit(1);
  }

  const dbPath = process.argv[3] || undefined;

  reflect(sessionId, dbPath)
    .then(result => {
      if (result.skipped) {
        console.error(`Reflector skipped: ${result.reason}`);
      } else {
        console.error(`Reflector: ${result.observationsCreated} condensed observations (gen ${result.newGeneration}), superseded ${result.supersededCount}`);
      }
    })
    .catch(err => {
      console.error(`Reflector error: ${err.message}`);
      process.exit(1);
    });
}
