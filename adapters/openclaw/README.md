# OpenClaw Adapter — Conversation Observer 👁️

Automatic conversation compression for the moonshine system, packaged as an OpenClaw hook.

## What It Does

Every message in the main session is captured to `observations.db`. When unobserved tokens exceed 3,000, the observer pipeline fires:

1. **Observer** — extracts structured observations (type, priority, entities, date)
2. **Reflector** — condenses observations when they grow past 4,000 tokens

This eliminates the "model forgot to write it down" failure mode. No agent initiative required.

## Architecture

```
message:received / message:sent hooks
  │
  └──► observations.db → messages table (fire-and-forget)
         │
         │  (when unobserved tokens > 3,000)
         ▼
      Observer → observations table
         │
         │  (when active observation tokens > 4,000)
         ▼
      Reflector → condensed observations (generation+1)
```

## Setup

1. Copy this directory into your OpenClaw workspace hooks:
   ```bash
   cp -r adapters/openclaw/ ~/your-workspace/hooks/conversation-observer/
   ```

2. Set environment variables (optional — defaults work if moonshine is in your workspace root):
   ```bash
   export OBSERVER_DB="./observations.db"
   export OBSERVER_SCRIPT="./observer/observe.js"
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

3. Enable the hook:
   ```bash
   openclaw hooks enable conversation-observer
   openclaw gateway restart
   ```

   Or add to `~/.openclaw/openclaw.json`:
   ```json
   {
     "hooks": {
       "internal": {
         "entries": {
           "conversation-observer": { "enabled": true }
         }
       }
     }
   }
   ```

## Cost

~$0.11-0.29/day ($3-9/month) using Claude Haiku 3.5.

## Files

- `HOOK.md` — OpenClaw hook metadata (events declaration)
- `handler.ts` — Hook handler (message capture + observer triggering)
- `../../observer/observe.js` — Observer script (extracts observations)
- `../../observer/reflect.js` — Reflector script (condenses observations)
- `../../observer/db.js` — Shared DB initialization
