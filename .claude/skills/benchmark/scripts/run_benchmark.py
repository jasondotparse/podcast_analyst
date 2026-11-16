"""Run one ingestion benchmark case against a throwaway local copy of the app.

Starts FastAPI/Codex on its own port with a fresh Postgres database, graph file, and Codex home, uploads
the case's transcript, waits for the pipeline, asks each question in a fresh chat session, and writes
everything a grader needs to .local/benchmarks/<run_id>/raw.json. The run's database is dropped at the end
unless --keep-db is passed.

Usage (from the repository root):
    .venv/bin/python .claude/skills/benchmark/scripts/run_benchmark.py [--case DIR] [--port 8765] [--keep-db]
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

SKILL_DIR = Path(__file__).resolve().parents[1]
ROOT = SKILL_DIR.parents[2]
DEFAULT_CASE = SKILL_DIR / "cases" / "optical-bottlenecks"
TERMINAL = {"completed", "failed", "skipped"}


def log(message: str) -> None:
    print(f"[benchmark] {message}", flush=True)


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    """Returns (status, parsed JSON or None)."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, {"detail": raw.decode(errors="replace")[:500]}


def post_json(base: str, path: str, payload: dict):
    return http("POST", base + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})


def upload(base: str, metadata: dict, transcript: Path):
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'.encode()
        + json.dumps(metadata).encode() + b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; name="transcript"; filename="{transcript.name}"\r\n'
        "Content-Type: text/plain\r\n\r\n".encode() + transcript.read_bytes() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return http("POST", base + "/api/ingestions", b"".join(parts),
                {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=120)


def sse_events(response):
    """Yields (event, data) from an open text/event-stream response, plus ("keepalive", {}) for comments
    (the server sends one every 15 s), so a caller can enforce its timeout during a quiet turn."""
    event, data = None, []
    for raw in response:
        line = raw.decode().rstrip("\r\n")
        if line.startswith(":"):
            yield "keepalive", {}
        elif not line:
            if event:
                yield event, json.loads("\n".join(data)) if data else {}
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


def ask(base: str, question: str, timeout: float) -> dict:
    """One question in a fresh chat session: what the user would see, plus the tool calls behind it."""
    started = time.monotonic()
    result = {"answer": "", "messages": [], "tools": [], "status": None, "error": None}
    status, body = post_json(base, "/api/sessions", {})
    if status != 201:
        result.update(status="error", error=f"POST /api/sessions returned {status}: {body}")
        return result
    session = body["session_id"]
    messages, tools = {}, {}
    try:
        with urllib.request.urlopen(f"{base}/api/sessions/{session}/events", timeout=60) as stream:
            events = sse_events(stream)
            for event, _ in events:
                if event == "ready":
                    break
            status, body = post_json(base, f"/api/sessions/{session}/turns", {"message": question})
            if status != 202:
                raise RuntimeError(f"POST turns returned {status}: {body}")
            for event, data in events:
                if event == "message":
                    messages[data["item_id"]] = data["text"]
                elif event == "tool" and data.get("phase") == "completed":
                    preview = data.get("result")
                    preview = preview if isinstance(preview, str) else json.dumps(preview)
                    tools[data["item_id"]] = {
                        "name": data.get("name"), "arguments": data.get("arguments"), "status": data.get("status"),
                        "duration_ms": data.get("duration_ms"), "result_preview": (preview or "")[:2000],
                    }
                elif event == "completed":
                    result.update(status=data.get("status"), error=data.get("error"))
                    break
                elif event == "fatal":
                    result.update(status="fatal", error=data.get("message"))
                    break
                if time.monotonic() - started > timeout:
                    result.update(status="timeout", error=f"No completed event after {timeout:.0f}s")
                    break
    except Exception as error:  # Record and move on to the next question.
        result.update(status=result["status"] or "error", error=f"{type(error).__name__}: {error}")
    result["messages"] = list(messages.values())
    result["answer"] = "\n\n".join(result["messages"])  # Everything the chat would have shown the user.
    result["tools"] = list(tools.values())
    result["latency_s"] = round(time.monotonic() - started, 1)
    return result


def git_info() -> dict:
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "--short", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def configured_model(dotenv: dict) -> str:
    if model := os.environ.get("CODEX_MODEL") or dotenv.get("CODEX_MODEL"):
        return model
    for line in (ROOT / "codex" / "config.toml").read_text().splitlines():
        if line.startswith("model ="):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def stop(server: subprocess.Popen) -> None:
    if server.poll() is not None:
        return
    os.killpg(server.pid, signal.SIGINT)  # Lets the lifespan handler reap Codex.
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(server.pid, signal.SIGKILL)
        server.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE, help="case folder (or a name under cases/) with case.json and transcript.txt")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keep-db", action="store_true", help="keep the run's Postgres database for debugging")
    parser.add_argument("--ingest-timeout", type=float, default=45 * 60, help="seconds")
    parser.add_argument("--turn-timeout", type=float, default=10 * 60, help="seconds per question")
    args = parser.parse_args()

    from dotenv import dotenv_values  # The app's venv has these; import late so --help works anywhere.
    import psycopg
    from psycopg.conninfo import make_conninfo

    case_dir = args.case if args.case.exists() else SKILL_DIR / "cases" / args.case  # A bare case name works too.
    case_dir = case_dir.resolve()
    case = json.loads((case_dir / "case.json").read_text())
    transcript = case_dir / "transcript.txt"
    dotenv = dotenv_values(ROOT / ".env")
    base_url = os.environ.get("DATABASE_URL") or dotenv.get("DATABASE_URL")
    if not base_url:
        sys.exit("DATABASE_URL is not set (see docs/local_development.md).")
    if not (os.environ.get("OPENAI_API_KEY") or dotenv.get("OPENAI_API_KEY")):
        sys.exit("OPENAI_API_KEY is not set in .env.")
    if not port_free(args.port):
        sys.exit(f"Port {args.port} is in use. Pass --port with a free one.")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / ".local" / "benchmarks" / run_id
    run_dir.mkdir(parents=True)
    shutil.copy(transcript, run_dir / "transcript.txt")
    db_name = f"podcast_bench_{run_id.replace('-', '_')}"
    db_url = make_conninfo(base_url, dbname=db_name)
    with psycopg.connect(base_url, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {db_name}")
    log(f"run {run_id}: database {db_name}, output {run_dir.relative_to(ROOT)}")

    raw = {
        "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(), "git": git_info(),
        "model": configured_model(dotenv), "case": case, "case_dir": os.path.relpath(case_dir, ROOT),
        "transcript_words": len(transcript.read_text().split()),
        "database": {"name": db_name, "kept": args.keep_db}, "ingestion": None, "answers": [],
    }
    env = {**os.environ, "DATABASE_URL": db_url,
           "PODCAST_LADYBUG_PATH": str(run_dir / "graph" / "graph.lbug"),
           "PODCAST_CODEX_HOME": str(run_dir / "codex"),
           # Already-set variables win over .env: no login, no real email.
           "PODCAST_APP_PASSWORD": "", "PODCAST_REQUIRE_AUTH": "0", "PODCAST_ALERT_SENDER": ""}
    base = f"http://127.0.0.1:{args.port}"
    server_log = open(run_dir / "server.log", "w")
    server = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "-m", "uvicorn", "src.backend.main:app", "--host", "127.0.0.1",
         "--port", str(args.port), "--timeout-graceful-shutdown", "3"],
        cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 180
        while True:
            if server.poll() is not None:
                raise RuntimeError("The backend exited during startup. See server.log.")
            try:
                if http("GET", base + "/ready", timeout=5)[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("The backend was not ready after 180s. See server.log.")
            time.sleep(2)
        log(f"backend ready on {base}")

        # Ingest.
        started = time.monotonic()
        status, job = upload(base, case["metadata"], transcript)
        if status != 202:
            raise RuntimeError(f"Upload returned {status}: {job}")
        steps, last_step = [], None
        while job["status"] not in TERMINAL and time.monotonic() - started < args.ingest_timeout:
            time.sleep(5)
            status, polled = http("GET", f"{base}/api/ingestions/{job['job_id']}")
            if status == 200:
                job = polled
            if job.get("current_step") and job["current_step"] != last_step:
                last_step = job["current_step"]
                steps.append({"step": last_step, "started_s": round(time.monotonic() - started, 1)})
                log(f"ingestion: {last_step} ({steps[-1]['started_s']:.0f}s)")
        ingestion = {"job_id": job["job_id"], "episode_id": job["episode_id"],
                     "status": job["status"] if job["status"] in TERMINAL else "timeout", "error": job.get("error"),
                     "duration_s": round(time.monotonic() - started, 1), "steps": steps}
        with psycopg.connect(db_url) as conn:
            ingestion["passages"] = conn.execute("SELECT count(*) FROM passages WHERE episode_id = %s",
                                                 (job["episode_id"],)).fetchone()[0]
            rows = conn.execute(
                "SELECT c.text, c.speaker, c.evidence_quote, c.signal, p.ordinal FROM claims c "
                "JOIN passages p ON p.id = c.passage_id WHERE p.episode_id = %s "
                "ORDER BY c.signal DESC NULLS LAST, p.ordinal", (job["episode_id"],)).fetchall()
        ingestion["claims"] = [{"text": t, "speaker": s, "quote": q, "signal": g, "passage": o}
                               for t, s, q, g, o in rows]
        raw["ingestion"] = ingestion
        log(f"ingestion {ingestion['status']} in {ingestion['duration_s']:.0f}s: "
            f"{ingestion['passages']} passages, {len(rows)} claims")

        # Ask.
        if ingestion["status"] == "completed":
            for item in case["questions"]:
                log(f"asking {item['id']}: {item['question']}")
                answer = ask(base, item["question"], args.turn_timeout)
                raw["answers"].append({"id": item["id"], "question": item["question"], **answer})
                log(f"{item['id']} {answer['status']} in {answer['latency_s']:.0f}s, {len(answer['tools'])} tool calls")
        else:
            log("ingestion did not complete, so no questions were asked")
    except Exception as error:
        raw["error"] = str(error)
        log(f"error: {error}")
    finally:
        stop(server)
        server_log.close()
        if not args.keep_db:
            with psycopg.connect(base_url, autocommit=True) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        raw["finished_at"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "raw.json").write_text(json.dumps(raw, indent=2))
        log(f"wrote {(run_dir / 'raw.json').relative_to(ROOT)}")
    return 1 if raw.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
