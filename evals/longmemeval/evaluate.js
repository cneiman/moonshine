#!/usr/bin/env node

/**
 * LongMemEval Evaluation Script
 *
 * Uses an LLM judge to score hypotheses against reference answers.
 * Implements the exact evaluation prompts from the LongMemEval benchmark.
 *
 * Usage:
 *   node evaluate.js hypotheses-oracle.jsonl --judge anthropic
 *   node evaluate.js hypotheses-oracle.jsonl --judge openai
 *   node evaluate.js hypotheses-oracle.jsonl --dataset s --judge anthropic
 */

import { readFileSync, writeFileSync, appendFileSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── CLI Args ────────────────────────────────────────────────────────────────
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {
    hypFile: null,
    dataset: "oracle",
    judge: "anthropic",
    judgeModel: null,
    start: 0,
    limit: Infinity,
  };

  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--")) {
      switch (args[i]) {
        case "--dataset":
          opts.dataset = args[++i];
          break;
        case "--judge":
          opts.judge = args[++i];
          break;
        case "--judge-model":
          opts.judgeModel = args[++i];
          break;
        case "--start":
          opts.start = parseInt(args[++i], 10);
          break;
        case "--limit":
          opts.limit = parseInt(args[++i], 10);
          break;
        case "--help":
          console.log(`
LongMemEval Evaluator

  node evaluate.js <hypotheses.jsonl> [options]

  --dataset oracle|s    Reference dataset (default: oracle)
  --judge anthropic|openai  Judge provider (default: anthropic)
  --judge-model MODEL   Override judge model
  --start N             Start from hypothesis N
  --limit N             Evaluate at most N hypotheses
  --help                Show this help
`);
          process.exit(0);
      }
    } else if (!opts.hypFile) {
      opts.hypFile = args[i];
    }
  }

  if (!opts.hypFile) {
    console.error("Error: provide a hypotheses JSONL file as the first argument");
    process.exit(1);
  }

  return opts;
}

// ── LongMemEval Evaluation Prompts (from official repo) ─────────────────────
function getJudgePrompt(questionType, question, answer, hypothesis, isAbstention) {
  if (isAbstention) {
    return (
      `I will give you an unanswerable question, an explanation, and a response from a model. ` +
      `Please answer yes if the model correctly identifies the question as unanswerable. ` +
      `The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\n` +
      `Question: ${question}\n\nExplanation: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
      `Does the model correctly identify the question as unanswerable? Answer yes or no only.`
    );
  }

  switch (questionType) {
    case "single-session-user":
    case "single-session-assistant":
    case "multi-session":
      return (
        `I will give you a question, a correct answer, and a response from a model. ` +
        `Please answer yes if the response contains the correct answer. Otherwise, answer no. ` +
        `If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. ` +
        `If the response only contains a subset of the information required by the answer, answer no. \n\n` +
        `Question: ${question}\n\nCorrect Answer: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
        `Is the model response correct? Answer yes or no only.`
      );

    case "temporal-reasoning":
      return (
        `I will give you a question, a correct answer, and a response from a model. ` +
        `Please answer yes if the response contains the correct answer. Otherwise, answer no. ` +
        `If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. ` +
        `If the response only contains a subset of the information required by the answer, answer no. ` +
        `In addition, do not penalize off-by-one errors for the number of days. ` +
        `If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\n` +
        `Question: ${question}\n\nCorrect Answer: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
        `Is the model response correct? Answer yes or no only.`
      );

    case "knowledge-update":
      return (
        `I will give you a question, a correct answer, and a response from a model. ` +
        `Please answer yes if the response contains the correct answer. Otherwise, answer no. ` +
        `If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\n` +
        `Question: ${question}\n\nCorrect Answer: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
        `Is the model response correct? Answer yes or no only.`
      );

    case "single-session-preference":
      return (
        `I will give you a question, a rubric for desired personalized response, and a response from a model. ` +
        `Please answer yes if the response satisfies the desired response. Otherwise, answer no. ` +
        `The model does not need to reflect all the points in the rubric. ` +
        `The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\n` +
        `Question: ${question}\n\nRubric: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
        `Is the model response correct? Answer yes or no only.`
      );

    default:
      // Generic fallback
      return (
        `I will give you a question, a correct answer, and a response from a model. ` +
        `Please answer yes if the response contains the correct answer. Otherwise, answer no.\n\n` +
        `Question: ${question}\n\nCorrect Answer: ${answer}\n\nModel Response: ${hypothesis}\n\n` +
        `Is the model response correct? Answer yes or no only.`
      );
  }
}

// ── API Keys ────────────────────────────────────────────────────────────────
function getAnthropicKey() {
  if (process.env.ANTHROPIC_API_KEY) return process.env.ANTHROPIC_API_KEY;
  const envFile = join(process.env.HOME || "~", ".env.anthropic");
  if (existsSync(envFile)) {
    const content = readFileSync(envFile, "utf8");
    const match = content.match(/ANTHROPIC_API_KEY=(.+)/);
    if (match) return match[1].trim();
  }
  throw new Error("No ANTHROPIC_API_KEY found");
}

function getOpenAIKey() {
  if (process.env.OPENAI_API_KEY) return process.env.OPENAI_API_KEY;
  const envFile = join(process.env.HOME || "~", ".env.openai");
  if (existsSync(envFile)) {
    const content = readFileSync(envFile, "utf8");
    const match = content.match(/OPENAI_API_KEY=(.+)/);
    if (match) return match[1].trim();
  }
  throw new Error("No OPENAI_API_KEY found");
}

// ── API Calls ───────────────────────────────────────────────────────────────
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function callAnthropicJudge(apiKey, model, prompt) {
  for (let attempt = 0; attempt < 3; attempt++) {
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
          max_tokens: 16,
          messages: [{ role: "user", content: prompt }],
        }),
      });

      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get("retry-after") || "10", 10);
        await sleep(retryAfter * 1000);
        continue;
      }

      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Anthropic ${res.status}: ${body}`);
      }

      const data = await res.json();
      return data.content?.[0]?.text || "";
    } catch (err) {
      if (attempt < 2) {
        await sleep(2000 * (attempt + 1));
      } else {
        throw err;
      }
    }
  }
}

async function callOpenAIJudge(apiKey, model, prompt) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model,
          messages: [{ role: "user", content: prompt }],
          max_tokens: 16,
          temperature: 0,
        }),
      });

      if (res.status === 429) {
        const retryAfter = parseInt(res.headers.get("retry-after") || "10", 10);
        await sleep(retryAfter * 1000);
        continue;
      }

      if (!res.ok) {
        const body = await res.text();
        throw new Error(`OpenAI ${res.status}: ${body}`);
      }

      const data = await res.json();
      return data.choices?.[0]?.message?.content || "";
    } catch (err) {
      if (attempt < 2) {
        await sleep(2000 * (attempt + 1));
      } else {
        throw err;
      }
    }
  }
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs();

  // Load reference dataset
  const refFile =
    opts.dataset === "oracle"
      ? join(__dirname, "data/longmemeval_oracle.json")
      : join(__dirname, "data/longmemeval_s.json");
  console.log(`Reference dataset: ${refFile}`);
  const references = JSON.parse(readFileSync(refFile, "utf8"));
  const refMap = new Map(references.map((r) => [r.question_id, r]));

  // Load hypotheses
  console.log(`Hypotheses file: ${opts.hypFile}`);
  const hypLines = readFileSync(opts.hypFile, "utf8").trim().split("\n").filter(Boolean);
  const hypotheses = hypLines.map((line) => JSON.parse(line));
  console.log(`Loaded ${hypotheses.length} hypotheses`);

  // Setup judge
  let judgeModel, judgeCall, apiKey;
  if (opts.judge === "anthropic") {
    apiKey = getAnthropicKey();
    judgeModel = opts.judgeModel || "claude-sonnet-4-20250514";
    judgeCall = (prompt) => callAnthropicJudge(apiKey, judgeModel, prompt);
  } else if (opts.judge === "openai") {
    apiKey = getOpenAIKey();
    judgeModel = opts.judgeModel || "gpt-4o";
    judgeCall = (prompt) => callOpenAIJudge(apiKey, judgeModel, prompt);
  } else {
    console.error(`Unknown judge: ${opts.judge}`);
    process.exit(1);
  }
  console.log(`Judge: ${opts.judge} (${judgeModel})`);

  // Evaluate
  const end = Math.min(opts.start + opts.limit, hypotheses.length);
  const slice = hypotheses.slice(opts.start, end);

  const results = [];
  const typeAccuracy = {};
  let correct = 0;
  let total = 0;

  const logFile = opts.hypFile + `.eval-${opts.judge}.jsonl`;

  for (let i = 0; i < slice.length; i++) {
    const hyp = slice[i];
    const ref = refMap.get(hyp.question_id);
    if (!ref) {
      console.log(`  Skipping ${hyp.question_id} — not in reference data`);
      continue;
    }

    const isAbstention = hyp.question_id.endsWith("_abs");
    const prompt = getJudgePrompt(
      ref.question_type,
      ref.question,
      ref.answer,
      hyp.hypothesis,
      isAbstention
    );

    try {
      const response = await judgeCall(prompt);
      const label = response.toLowerCase().includes("yes");

      const entry = {
        question_id: hyp.question_id,
        question_type: ref.question_type,
        question: ref.question,
        answer: ref.answer,
        hypothesis: hyp.hypothesis,
        judge_response: response.trim(),
        label,
      };
      results.push(entry);

      // Track per-type accuracy
      if (!typeAccuracy[ref.question_type]) {
        typeAccuracy[ref.question_type] = { correct: 0, total: 0 };
      }
      typeAccuracy[ref.question_type].total++;
      if (label) {
        typeAccuracy[ref.question_type].correct++;
        correct++;
      }
      total++;

      // Append to log
      appendFileSync(logFile, JSON.stringify(entry) + "\n");

      const pct = ((correct / total) * 100).toFixed(1);
      const marker = label ? "✓" : "✗";
      console.log(
        `  ${opts.start + i + 1}/${hypotheses.length} ${marker} [${ref.question_type}] ` +
          `running: ${pct}% (${correct}/${total})`
      );
    } catch (err) {
      console.error(`  Error evaluating ${hyp.question_id}: ${err.message}`);
    }
  }

  // ── Summary ─────────────────────────────────────────────────────────────
  console.log("\n" + "═".repeat(60));
  console.log("RESULTS");
  console.log("═".repeat(60));
  console.log(`Overall accuracy: ${((correct / total) * 100).toFixed(1)}% (${correct}/${total})`);
  console.log("");

  const sortedTypes = Object.entries(typeAccuracy).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [type, stats] of sortedTypes) {
    const pct = ((stats.correct / stats.total) * 100).toFixed(1);
    console.log(`  ${type}: ${pct}% (${stats.correct}/${stats.total})`);
  }

  console.log(`\nEvaluation log: ${logFile}`);

  // Write summary JSON
  const summaryFile = opts.hypFile + `.eval-${opts.judge}-summary.json`;
  writeFileSync(
    summaryFile,
    JSON.stringify(
      {
        overall: { accuracy: correct / total, correct, total },
        by_type: Object.fromEntries(
          sortedTypes.map(([type, stats]) => [
            type,
            {
              accuracy: stats.correct / stats.total,
              correct: stats.correct,
              total: stats.total,
            },
          ])
        ),
        judge: { provider: opts.judge, model: judgeModel },
        dataset: opts.dataset,
        timestamp: new Date().toISOString(),
      },
      null,
      2
    )
  );
  console.log(`Summary: ${summaryFile}`);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
