# Observer & Reflector Prompts

> These prompts are designed for small, fast models (e.g., Claude Haiku 3.5).
> They use explicit instructions, few-shot examples, negative examples, and strict output formatting.
> The prompts are embedded as constants in observe.js and reflect.js.

---

## Observer Prompt

Extracts structured observations from conversation logs:

- **Input:** Timestamped messages (user + assistant)
- **Output:** JSON array of observations
- **Each observation:** one sentence, max 200 chars, with priority/type/entities/date
- **Max:** 10 observations per batch
- **Key rules:**
  - Extract SPECIFIC facts, not summaries
  - Capture decisions (explicit and implicit)
  - Only include entities actually mentioned in text
  - Capture results/decisions from tool output, not raw data

## Reflector Prompt

Condenses observations by merging related items:

- **Input:** JSON array of observations with IDs
- **Output:** JSON object with condensed observations + superseded IDs
- **Key rules:**
  - Output must have FEWER observations than input
  - Only merge observations about the SAME event/decision
  - Preserve high-priority items unless truly superseded
  - superseded_ids must reference actual input IDs

## Cost

Using Claude Haiku 3.5:
- Observer fires when unobserved tokens > 3,000
- Reflector fires when active observation tokens > 4,000
- Typical cost: ~$0.11-0.29/day ($3-9/month) with regular usage

## Customizing

To change the model, set `OBSERVER_MODEL` environment variable:
```bash
export OBSERVER_MODEL=claude-haiku-4-5  # default
export OBSERVER_MODEL=claude-sonnet-4-6 # higher quality, higher cost
```
