---
name: benchmark
description: Benchmark the ingestion pipeline end to end. Starts Podcast Analyst on localhost with a throwaway database, ingests a test transcript, asks the case's questions in chat, grades each answer on accuracy, adherence to the transcript, and helpfulness, and writes benchmarks/benchmark_MM_DD_YYYY.html.
argument-hint: "[case-folder]"
allowed-tools: Bash(.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/*)
---

# Benchmark the ingestion pipeline

This measures how well ingestion captures what a transcript says, judged by the chat answers an analyst
would actually get. One run makes real OpenAI calls (extraction, ranking, one chat turn per question) and
takes about 10–25 minutes.

Case to run: `$ARGUMENTS` (empty means the default, `${CLAUDE_SKILL_DIR}/cases/optical-bottlenecks`).

## 1. Preflight

Work from the repository root. Check, and stop with a clear message if anything is missing (setup is in
`docs/local_development.md` and `docs/local_codex_chat.md`):

- `.venv/` exists, and `.env` has `OPENAI_API_KEY` and `DATABASE_URL`.
- The Postgres container is up: `docker ps --filter name=podcast-postgres`. If it's stopped, `docker start podcast-postgres`.
- `codex --version` prints 0.167.0.

The run doesn't touch the user's dev server or data. It uses port 8765, its own database
(`podcast_bench_<run_id>`, dropped at the end), and its own graph file and Codex home under
`.local/benchmarks/<run_id>/`. Logins and real alert emails are switched off for the run.

## 2. Run

```bash
.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/run_benchmark.py [--case <case-folder>]
```

Run it in the background and check its output every few minutes until it prints `wrote .../raw.json`. Never
start a second run while one is going. Progress lines show each pipeline step and each question.

- **Exit code 1** (the backend didn't start, or the upload failed): read the end of
  `.local/benchmarks/<run_id>/server.log`, tell the user what went wrong, and stop.
- **Ingestion `failed` or `skipped`** (no questions are asked): still write `grades.json` with
  `"questions": {}` and a summary explaining the failure, then build the report.

## 3. Grade

Read `.local/benchmarks/<run_id>/raw.json` (answers, tool calls, every extracted claim, and the case's
expected points) and `transcript.txt` in the same folder, which is the source of truth. Score each answer
from 1 to 5 on each dimension, with a rationale of one or two sentences that names specifics.

**Accuracy.** Does it answer the question asked with the right facts, compared with the expected points?
- 5: every key expected point, with correct numbers and speakers, and nothing wrong.
- 4: a minor omission or imprecision.
- 3: misses a key point or has one factual error.
- 2: misses most key points or gets the central fact wrong.
- 1: wrong, off-topic, or no answer.

**Adherence to the transcript.** Is everything it attributes to the episode actually in the transcript?
Search the transcript for every quoted phrase (verbatim apart from whitespace and punctuation). Check each
speaker attribution. Hedges must survive: a rumor stays a rumor, and a corrected figure is the corrected
one. Outside information is fine only when it's clearly labeled as not from the episode.
- 5: every claim and quote traces to the transcript, quotes are verbatim, and attributions are right.
- 4: a paraphrase shown as a quote, or a slight overstatement.
- 3: one unsupported or misattributed claim.
- 2: several unsupported claims, or a fabricated quote.
- 1: mostly invented, or states as fact something the episode only speculated about or never covered.

**Helpfulness.** Would a busy analyst be well served? This means a direct answer first, then the supporting
claims with speaker and quote, at a sensible length, with uncertainty flagged and no filler.
- 5: exactly what an analyst needs.
- 4: useful, with minor friction (padding, buried lede).
- 3: usable but takes work: disorganized, too long, or thin on citations.
- 2: hard to use.
- 1: no useful answer.

Rules:
- Grade against the transcript, not your own knowledge. The default case's companies and people are fictional.
- Score the three dimensions independently. For example, a correct "the episode doesn't cover that" can score 5 on all three.
- For a turn that timed out or errored, grade what the user would have seen, and say so.
- When any score is 3 or lower, add a `diagnosis`: look for the needed fact in `ingestion.claims`. Say
  `extraction` if it is missing or wrong there, `answer` if it was extracted but the chat missed or garbled it,
  and add a sentence of detail. This is what tells the user whether ingestion or chat is at fault.
- `ingestion_notes`: how many of the expected facts, across all questions, appear in the extracted claims,
  and any notably bad claims (a wrong speaker, a quote that isn't verbatim).
- `summary`: two to four sentences giving the overall verdict, the biggest weakness, and where it came from.

Write `.local/benchmarks/<run_id>/grades.json`:

```json
{
  "grader": "<your model name>",
  "summary": "...",
  "ingestion_notes": "...",
  "questions": {
    "q1": {
      "accuracy": {"score": 4, "rationale": "..."},
      "adherence": {"score": 5, "rationale": "..."},
      "helpfulness": {"score": 4, "rationale": "..."},
      "diagnosis": "extraction: ..."
    }
  }
}
```

## 4. Report

```bash
.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/create_benchmark_report.py .local/benchmarks/<run_id>
```

It prints the path of the report, `benchmarks/benchmark_MM_DD_YYYY.html` (with `_2`, `_3`, and so on for later
runs that day). If it lists problems with `grades.json`, fix them and run it again.

## 5. Tell the user

Give the report's path (they open it in a browser), the overall and per-dimension averages, and the biggest
weakness with its diagnosis. Don't commit the report unless they ask.

## Adding a case

Make a new folder under `${CLAUDE_SKILL_DIR}/cases/` with a `transcript.txt` and a `case.json` (copy the
default case's format: `metadata` is the upload form's fields, and each question has an `id`, the `question`, and
its `expected` points). Real transcripts work. Write questions that the transcript alone can answer, and
include at least one it doesn't cover. Run it with `/benchmark <case-folder>`.
