"""Async prompt jobs: submit now, poll for the result later.

Why: POST /api/prompt holds the HTTP connection open until agy finishes. Railway's
edge cuts a request after ~5 minutes (HTTP 502 "upstream error"), so any task longer
than that never returns its answer. A job returns an id at once; the agent runs in a
background thread; the caller polls GET /api/jobs/{id} (optionally long-polling with
?wait=N, kept under the proxy limit).

Jobs for the SAME account run one after another (one agy process per account home at
a time); jobs for different accounts run in parallel. Finished jobs are written to
the volume so a poll still works after a restart; a job that was queued/running when
the server died is reported as failed ("server restarted").
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from . import manager_api as api

CFG = config.CONFIG

MAX_TIMEOUT = 3600          # longest single agy run a job may ask for (seconds)
MAX_WAIT = 25               # longest long-poll (seconds); must stay under the proxy limit
KEEP_SECONDS = 24 * 3600    # finished jobs are dropped after this long
MAX_JOBS = 500

_dir = CFG.manager_root.parent / ".agy-web-jobs"
_jobs: dict[str, dict] = {}
_events: dict[str, threading.Event] = {}
_lock = threading.Lock()
_acct_locks: dict[str, threading.Lock] = {}
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="agy-job")

FINAL = ("done", "failed")


def _save(job: dict) -> None:
    try:
        _dir.mkdir(parents=True, exist_ok=True)
        tmp = _dir / f"{job['id']}.json.tmp"
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(_dir / f"{job['id']}.json")
    except Exception:
        pass  # persistence is best effort; the in-memory copy is the live truth


def load() -> None:
    """Called at startup: bring back saved jobs; anything unfinished died with the old process."""
    if not _dir.is_dir():
        return
    now = time.time()
    for f in _dir.glob("*.json"):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ref = job.get("finished") or job.get("created") or 0
        if now - ref > KEEP_SECONDS:
            f.unlink(missing_ok=True)
            continue
        if job.get("status") not in FINAL:
            job.update(status="failed", ok=False, finished=now, error="server restarted while the job was running")
            _save(job)
        ev = threading.Event()
        ev.set()
        with _lock:
            _jobs[job["id"]] = job
            _events[job["id"]] = ev


def _gc() -> None:
    """Drop old finished jobs (caller holds _lock)."""
    now = time.time()
    for jid, job in list(_jobs.items()):
        if job["status"] in FINAL and now - (job.get("finished") or now) > KEEP_SECONDS:
            _jobs.pop(jid, None)
            _events.pop(jid, None)
            (_dir / f"{jid}.json").unlink(missing_ok=True)
    if len(_jobs) > MAX_JOBS:
        done = sorted((j for j in _jobs.values() if j["status"] in FINAL), key=lambda j: j.get("finished") or 0)
        for job in done[: len(_jobs) - MAX_JOBS]:
            _jobs.pop(job["id"], None)
            _events.pop(job["id"], None)
            (_dir / f"{job['id']}.json").unlink(missing_ok=True)


def submit(
    prompt: str,
    account: str | None = None,
    timeout: int | None = None,
    tools: bool | None = None,
    sandbox: bool | None = None,
    cwd: str | None = None,
    model: str | None = None,
) -> dict:
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")
    name = account or api.snapshot().get("active")
    if not name:
        raise ValueError("No active account and none specified. Add or activate one first.")
    if not (api.account_home(name) / ".gemini").is_dir():
        raise ValueError(f"Account '{name}' has no profile.")
    to = min(int(timeout or CFG.prompt_timeout), MAX_TIMEOUT)

    job = {
        "id": uuid.uuid4().hex[:16],
        "status": "queued",
        "account": name,
        "model": model or CFG.default_model or None,
        "timeout": to,
        "created": time.time(),
        "started": None,
        "finished": None,
        "ok": None,
    }
    args = dict(prompt=prompt, account=name, timeout=to, tools=tools, sandbox=sandbox, cwd=cwd, model=model)
    with _lock:
        _gc()
        _jobs[job["id"]] = job
        _events[job["id"]] = threading.Event()
        _acct_locks.setdefault(name, threading.Lock())
    _save(job)
    _pool.submit(_run, job["id"], args)
    return view(job)


def _run(jid: str, args: dict) -> None:
    job = _jobs[jid]
    with _acct_locks[job["account"]]:
        job.update(status="running", started=time.time())
        _save(job)
        try:
            res = api.run_prompt(**args)
        except Exception as exc:  # run_prompt raises ValueError for bad input
            res = {"ok": False, "error": str(exc)[:2000]}
    job.update(
        {k: res[k] for k in ("answer", "error", "exit", "ms", "cwd", "auth_failure") if k in res},
        ok=bool(res.get("ok")),
        status="done" if res.get("ok") else "failed",
        finished=time.time(),
    )
    _save(job)
    _events[jid].set()


def view(job: dict, with_answer: bool = True) -> dict:
    out = {k: v for k, v in job.items() if with_answer or k != "answer"}
    out["job_id"] = job["id"]
    return out


def get(jid: str, wait: float = 0) -> dict | None:
    job = _jobs.get(jid)
    if job is None:
        return None
    wait = max(0.0, min(float(wait or 0), MAX_WAIT))
    if wait and job["status"] not in FINAL:
        _events[jid].wait(wait)
    return view(job)


def listing() -> list[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)
    return [view(j, with_answer=False) for j in jobs]
