"""Render a benchmark run (raw.json + grades.json) as one self-contained HTML page.

Usage (from the repository root):
    python3 .claude/skills/benchmark/scripts/create_benchmark_report.py .local/benchmarks/<run_id> [--out PATH]

Writes benchmarks/benchmark_MM_DD_YYYY.html by default (with _2, _3, ... if another run already used the name)
and prints its path. Standard library only.
"""
import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
DIMENSIONS = [("accuracy", "Accuracy"), ("adherence", "Adherence to transcript"), ("helpfulness", "Helpfulness")]


def validate(raw: dict, grades: dict) -> list[str]:
    problems = []
    graded = grades.get("questions", {})
    for answer in raw["answers"]:
        entry = graded.get(answer["id"])
        if entry is None:
            problems.append(f"{answer['id']}: not graded")
            continue
        for key, _ in DIMENSIONS:
            score = (entry.get(key) or {}).get("score")
            if not isinstance(score, int) or not 1 <= score <= 5:
                problems.append(f"{answer['id']}.{key}.score must be an integer from 1 to 5 (got {score!r})")
            if not (entry.get(key) or {}).get("rationale"):
                problems.append(f"{answer['id']}.{key}.rationale is missing")
    if not grades.get("summary"):
        problems.append("summary is missing")
    return problems


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f}"


def bar(score: float | None) -> str:
    if score is None:
        return '<span class="bar"></span>'
    return (f'<span class="bar" title="{score:.1f} of 5"><span style="width:{score / 5 * 100:.0f}%"></span></span>')


def text(value) -> str:
    return escape(str(value)) if value is not None else ""


def render(raw: dict, grades: dict) -> str:
    graded = grades.get("questions", {})
    answers = raw["answers"]
    scores = {key: [graded[a["id"]][key]["score"] for a in answers if a["id"] in graded] for key, _ in DIMENSIONS}
    averages = {key: mean(values) for key, values in scores.items()}
    overall = mean([v for v in averages.values() if v is not None])
    expected = {q["id"]: q.get("expected", []) for q in raw["case"]["questions"]}
    started = datetime.fromisoformat(raw["started_at"]).astimezone()
    git = raw.get("git") or {}
    ingestion = raw.get("ingestion") or {}
    case = raw["case"]

    tiles = [("Overall", overall)] + [(label, averages[key]) for key, label in DIMENSIONS]
    tiles_html = "".join(
        f'<div class="tile"><div class="tile-label">{label}</div>'
        f'<div class="tile-value">{fmt(value)}<span class="tile-unit"> / 5</span></div>{bar(value)}</div>'
        for label, value in tiles)

    status = ingestion.get("status", "not run")
    status_class = "ok" if status == "completed" else "bad"
    steps = "".join(f"<li><code>{text(s['step'])}</code> at {s['started_s']:.0f}s</li>"
                    for s in ingestion.get("steps", []))
    claims_rows = "".join(
        f"<tr><td class='num'>{text(c['signal'])}</td><td>{text(c['speaker'])}</td><td>{text(c['text'])}</td>"
        f"<td class='quote'>{text(c['quote'])}</td></tr>" for c in ingestion.get("claims", []))
    ingestion_html = f"""
      <dl class="facts">
        <div><dt>Status</dt><dd><span class="status {status_class}">{text(status)}</span></dd></div>
        <div><dt>Duration</dt><dd>{fmt((ingestion.get('duration_s') or 0) / 60)} min</dd></div>
        <div><dt>Passages</dt><dd>{text(ingestion.get('passages', '–'))}</dd></div>
        <div><dt>Claims extracted</dt><dd>{len(ingestion.get('claims', []))}</dd></div>
      </dl>
      {f'<p class="error">Error: {text(ingestion.get("error"))}</p>' if ingestion.get('error') else ''}
      {f'<p class="error">Run error: {text(raw.get("error"))}</p>' if raw.get('error') else ''}
      {f'<p>{text(grades.get("ingestion_notes"))}</p>' if grades.get('ingestion_notes') else ''}
      {f'<details><summary>Pipeline steps</summary><ul class="steps">{steps}</ul></details>' if steps else ''}
      {f'''<details><summary>All {len(ingestion.get("claims", []))} extracted claims, highest signal first</summary>
        <div class="scroll"><table class="claims"><thead><tr><th>Signal</th><th>Speaker</th><th>Claim</th><th>Evidence quote</th></tr></thead>
        <tbody>{claims_rows}</tbody></table></div></details>''' if claims_rows else ''}"""

    summary_rows = "".join(
        f"<tr><td><a href='#{text(a['id'])}'>{text(a['id'])}</a></td><td>{text(a['question'])}</td>"
        + "".join(f"<td class='num'>{text(graded.get(a['id'], {}).get(key, {}).get('score', '–'))}</td>"
                  for key, _ in DIMENSIONS)
        + f"<td class='num'>{a.get('latency_s', 0):.0f}s</td><td class='num'>{len(a.get('tools', []))}</td></tr>"
        for a in answers)

    cards = []
    for a in answers:
        g = graded.get(a["id"], {})
        score_rows = "".join(
            f"<div class='score'><div class='score-head'><span>{label}</span>"
            f"<strong>{text(g.get(key, {}).get('score', '–'))}</strong>{bar(g.get(key, {}).get('score'))}</div>"
            f"<p>{text(g.get(key, {}).get('rationale'))}</p></div>" for key, label in DIMENSIONS)
        tools = "".join(
            f"<li><code>{text(t['name'])}</code> <span class='muted'>{text(t['status'])}"
            f"{', ' + str(round(t['duration_ms'] / 1000, 1)) + 's' if t.get('duration_ms') else ''}</span>"
            f"<pre>{text(json.dumps(t.get('arguments'), indent=1))}</pre></li>" for t in a.get("tools", []))
        problem = (f"<p class='error'>Turn {text(a.get('status'))}: {text(a.get('error'))}</p>"
                   if a.get("status") != "completed" or a.get("error") else "")
        cards.append(f"""
      <article class="card" id="{text(a['id'])}">
        <h3><span class="qid">{text(a['id'])}</span> {text(a['question'])}</h3>
        {problem}
        <div class="columns">
          <div>
            <h4>Expected</h4><ul>{''.join(f'<li>{text(e)}</li>' for e in expected.get(a['id'], []))}</ul>
            <h4>Answer <span class="muted">({a.get('latency_s', 0):.0f}s)</span></h4>
            <div class="answer">{text(a.get('answer')) or '<span class="muted">No answer.</span>'}</div>
          </div>
          <div>
            <h4>Grades</h4>{score_rows}
            {f"<h4>Diagnosis</h4><p>{text(g.get('diagnosis'))}</p>" if g.get('diagnosis') else ''}
            {f'<details><summary>{len(a.get("tools", []))} tool calls</summary><ul class="tools">{tools}</ul></details>' if tools else '<p class="muted">No tool calls.</p>'}
          </div>
        </div>
      </article>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="benchmark-run-id" content="{text(raw['run_id'])}">
<title>Ingestion benchmark · {started:%b %-d, %Y}</title>
<style>
  :root {{
    --bg: #161826; --surface: #232532; --text: #e9e9ed; --muted: #9397ab; --accent: #9184d9;
    --divider: color-mix(in srgb, #e9e9ed 16%, transparent); --error: oklch(0.76 0.09 22);
    --ok: oklch(0.80 0.10 155); --radius: 14px;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.55 Inter, system-ui, sans-serif; }}
  main {{ max-width: 1120px; margin: 0 auto; padding: 40px 24px 80px; }}
  h1 {{ font-size: 28px; margin: 0 0 4px; font-weight: 600; }}
  h2 {{ font-size: 18px; margin: 40px 0 12px; font-weight: 600; }}
  h3 {{ font-size: 16px; margin: 0 0 12px; font-weight: 600; }}
  h4 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 16px 0 6px; }}
  a {{ color: var(--accent); }}
  code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
  pre {{ white-space: pre-wrap; margin: 4px 0 8px; color: var(--muted); }}
  .meta, .muted {{ color: var(--muted); }}
  .meta {{ margin: 0; font-size: 13px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 28px; }}
  .tile {{ background: var(--surface); border-radius: var(--radius); padding: 16px 18px; }}
  .tile-label {{ color: var(--muted); font-size: 13px; }}
  .tile-value {{ font-size: 32px; font-weight: 600; margin: 2px 0 8px; font-variant-numeric: tabular-nums; }}
  .tile-unit {{ font-size: 15px; color: var(--muted); font-weight: 400; }}
  .bar {{ display: inline-block; width: 100%; height: 6px; border-radius: 3px; background: var(--divider); overflow: hidden; vertical-align: middle; }}
  .bar > span {{ display: block; height: 100%; background: var(--accent); border-radius: 3px; }}
  .summary {{ background: var(--surface); border-radius: var(--radius); padding: 16px 20px; margin-top: 12px; }}
  .summary p {{ margin: 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--divider); vertical-align: top; }}
  th {{ color: var(--muted); font-weight: 500; font-size: 13px; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .claims td.quote {{ color: var(--muted); font-style: italic; }}
  .scroll {{ overflow-x: auto; }}
  .facts {{ display: flex; gap: 32px; flex-wrap: wrap; margin: 16px 0 12px; }}
  .facts dt {{ color: var(--muted); font-size: 13px; }}
  .facts dd {{ margin: 0; font-size: 18px; font-weight: 600; }}
  .status {{ font-size: 14px; padding: 2px 10px; border-radius: 999px; border: 1px solid currentColor; }}
  .status.ok {{ color: var(--ok); }} .status.bad {{ color: var(--error); }}
  .error {{ color: var(--error); }}
  details {{ margin: 8px 0; }}
  summary {{ cursor: pointer; color: var(--accent); }}
  .steps {{ columns: 3; font-size: 13px; }}
  .card {{ background: var(--surface); border-radius: var(--radius); padding: 20px 22px; margin-bottom: 16px; }}
  .qid {{ color: var(--accent); margin-right: 6px; }}
  .columns {{ display: grid; grid-template-columns: 3fr 2fr; gap: 28px; }}
  .columns ul {{ margin: 0; padding-left: 18px; }}
  .answer {{ white-space: pre-wrap; background: var(--bg); border: 1px solid var(--divider); border-radius: 8px; padding: 12px 14px; font-size: 14px; }}
  .score {{ margin-bottom: 10px; }}
  .score-head {{ display: grid; grid-template-columns: 1fr 24px 80px; align-items: center; gap: 10px; }}
  .score-head strong {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .score p {{ margin: 2px 0 0; color: var(--muted); font-size: 13px; }}
  .tools {{ padding-left: 18px; font-size: 13px; }}
  @media (max-width: 760px) {{ .tiles {{ grid-template-columns: repeat(2, 1fr); }} .columns {{ grid-template-columns: 1fr; }} .steps {{ columns: 1; }} }}
</style>
</head>
<body data-run-id="{text(raw['run_id'])}">
<main>
  <h1>Ingestion benchmark</h1>
  <p class="meta">{started:%A, %B %-d, %Y at %-I:%M %p} · case <code>{text(case['name'])}</code> ·
    chat model <code>{text(raw.get('model'))}</code> · commit <code>{text(git.get('commit'))}</code>
    on <code>{text(git.get('branch'))}</code>{' (uncommitted changes)' if git.get('dirty') else ''} ·
    graded by {text(grades.get('grader', 'unknown'))}</p>

  <section class="tiles" aria-label="Average scores, 1 to 5">{tiles_html}</section>
  <div class="summary"><p>{text(grades.get('summary'))}</p></div>

  <h2>Ingestion</h2>
  <p class="meta">“{text(case['metadata'].get('title'))}” from {text(case['metadata'].get('podcast_name'))},
    {text(raw.get('transcript_words'))} words. {text(case.get('notes'))}</p>
  {ingestion_html}

  <h2>Questions</h2>
  {f'''<div class="scroll"><table><thead><tr><th>#</th><th>Question</th><th class="num">Accuracy</th><th class="num">Adherence</th>
    <th class="num">Helpfulness</th><th class="num">Time</th><th class="num">Tools</th></tr></thead>
    <tbody>{summary_rows}</tbody></table></div>''' if answers else '<p class="muted">No questions were asked.</p>'}

  <h2>Answers and grades</h2>
  {''.join(cards)}
  <p class="meta">Run <code>{text(raw['run_id'])}</code>. Raw data: <code>.local/benchmarks/{text(raw['run_id'])}/</code>.
    Scores are 1–5, graded against the transcript by an LLM, so treat single-point differences between runs as noise.</p>
</main>
</body>
</html>
"""


def output_path(raw: dict, out: Path | None) -> Path:
    if out:
        return out
    date = datetime.fromisoformat(raw["started_at"]).astimezone()
    folder = ROOT / "benchmarks"
    folder.mkdir(exist_ok=True)
    stem, n = f"benchmark_{date:%m_%d_%Y}", 1
    while True:
        path = folder / (f"{stem}.html" if n == 1 else f"{stem}_{n}.html")
        if not path.exists() or f'data-run-id="{raw["run_id"]}"' in path.read_text():
            return path  # A new name, or this run's own earlier report.
        n += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help=".local/benchmarks/<run_id>")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = json.loads((args.run_dir / "raw.json").read_text())
    grades_path = args.run_dir / "grades.json"
    if not grades_path.exists():
        sys.exit(f"Missing {grades_path}. Grade the run first (see SKILL.md).")
    grades = json.loads(grades_path.read_text())
    if problems := validate(raw, grades):
        sys.exit("grades.json is incomplete:\n  " + "\n  ".join(problems))
    path = output_path(raw, args.out)
    path.write_text(render(raw, grades))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
