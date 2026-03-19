---
name: conversation-observer
description: "Captures conversation turns for automatic observation compression"
metadata:
  openclaw:
    emoji: "👁️"
    events: ["message:received", "message:sent", "session:compact:before"]
---

# Conversation Observer Hook

Captures every message in the main session to `observations.db`, then triggers
the observer/reflector pipeline when unobserved token count exceeds a threshold.

## Events

- **message:received** — captures inbound messages (user → agent)
- **message:sent** — captures outbound messages (agent → user)
- **session:compact:before** — triggers observer flush before compaction
