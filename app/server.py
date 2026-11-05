"""HTTP surface: a token-guarded JSON API + the single-page management UI.

Blocking work (manager calls, agy subprocesses) runs in sync `def` endpoints so
FastAPI dispatches them to a threadpool instead of stalling the event loop.
"""
import asyncio
import hmac
import json
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from . import manager_api as api
from . import pty_login

CFG = config.CONFIG

if len(CFG.token) < 16:
    sys.stderr.write(
        "AGY_WEB_TOKEN is missing or shorter than 16 characters. Refusing to start.\n"
        "This server runs prompts and can read live credentials; an open port is a shell.\n"
    )
    sys.exit(1)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="agy-web", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    api.init()


def require_auth(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    elif token:
        supplied = token
    if not hmac.compare_digest(supplied, CFG.token):
        raise HTTPException(status_code=401, detail="bad or missing token")


def guard(fn, *args, **kwargs):
    """Run a manager call, turning its expected errors into a 400."""
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc.args[0]) if exc.args else "not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --- request bodies ---------------------------------------------------------

class PromptIn(BaseModel):
    prompt: str
    account: str | None = None
    timeout: int | None = None
    tools: bool | None = None
    sandbox: bool | None = None
    cwd: str | None = None
    model: str | None = None


class NameIn(BaseModel):
    name: str


class EnableIn(BaseModel):
    enabled: bool = True


class MarkBadIn(BaseModel):
    reason: str = "manual"
    cooldown_minutes: int = 60


class RotateIn(BaseModel):
    reason: str = "manual"
    cooldown_minutes: int = 60
    force_switch: bool = False


class SwitchModeIn(BaseModel):
    mode: str


class PolicyIn(BaseModel):
    short_usage_threshold_percent: float | None = None
    refresh_failure_threshold: int | None = None
    candidate_strategy: str | None = None


class LiveDirIn(BaseModel):
    dir: str | None = None


class ImportIn(BaseModel):
    name: str
    source: str | None = None


class EnsureIn(BaseModel):
    force: bool = False


class InputIn(BaseModel):
    text: str
    newline: bool = True


# --- unauthenticated --------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"cache-control": "no-store"})


@app.get("/docs")
def apidocs() -> FileResponse:
    return FileResponse(STATIC_DIR / "apidocs.html", headers={"cache-control": "no-store"})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- authenticated API ------------------------------------------------------

r = APIRouter(dependencies=[Depends(require_auth)])


@r.get("/api/status")
def status() -> dict:
    return guard(api.info)


@r.get("/api/agy/version")
def agy_version() -> dict:
    return {"version": api.agy_version()}


@r.post("/api/agy/update")
def agy_update() -> dict:
    return guard(api.update_agy)


@r.post("/api/prompt")
def prompt(body: PromptIn) -> dict:
    return guard(
        api.run_prompt,
        body.prompt,
        body.account,
        body.timeout,
        body.tools,
        body.sandbox,
        body.cwd,
        body.model,
    )


@r.post("/api/switch")
def switch(body: NameIn) -> dict:
    return {"active": guard(api.switch, body.name)}


@r.post("/api/switch-next")
def switch_next() -> dict:
    return {"active": guard(api.switch_next)}


@r.post("/api/accounts/{name}/enable")
def enable(name: str, body: EnableIn) -> dict:
    guard(api.set_enabled, name, body.enabled)
    return {"ok": True, "name": name, "enabled": body.enabled}


@r.post("/api/accounts/{name}/mark-bad")
def mark_bad(name: str, body: MarkBadIn) -> dict:
    guard(api.mark_bad, name, body.reason, body.cooldown_minutes)
    return {"ok": True, "name": name}


@r.post("/api/accounts/{name}/clear-bad")
def clear_bad(name: str) -> dict:
    guard(api.clear_bad, name)
    return {"ok": True, "name": name}


@r.post("/api/accounts/{name}/refresh-usage")
def refresh_usage(name: str) -> dict:
    return guard(api.refresh_usage, name)


@r.delete("/api/accounts/{name}")
def delete_account(name: str) -> dict:
    guard(api.delete_account, name)
    return {"ok": True, "name": name}


@r.get("/api/models")
def models(name: str | None = Query(default=None)) -> dict:
    return guard(api.models, name)


@r.get("/api/verify")
def verify() -> dict:
    return guard(api.verify)


@r.get("/api/switch-policy")
def get_policy() -> dict:
    return guard(api.get_policy)


@r.post("/api/switch-policy")
def set_policy(body: PolicyIn) -> dict:
    return guard(api.update_policy, **body.model_dump(exclude_none=True))


@r.post("/api/switch-mode")
def set_mode(body: SwitchModeIn) -> dict:
    return {"mode": guard(api.set_mode, body.mode)}


@r.post("/api/set-live-dir")
def set_live_dir(body: LiveDirIn) -> dict:
    guard(api.set_live_dir, body.dir)
    return {"ok": True, "live_dir": body.dir}


@r.post("/api/apply-active")
def apply_active() -> dict:
    return {"active": guard(api.apply_active)}


@r.post("/api/ensure-active")
def ensure_active(body: EnsureIn) -> dict:
    return guard(api.ensure_active, body.force)


@r.post("/api/rotate")
def rotate(body: RotateIn) -> dict:
    return guard(api.rotate, body.reason, body.cooldown_minutes, body.force_switch)


@r.post("/api/import-current")
def import_current(body: ImportIn) -> dict:
    guard(api.import_current, body.name, body.source)
    return {"ok": True, "name": body.name}


# --- interactive login (pty) ------------------------------------------------

@r.post("/api/login")
def login_start(body: NameIn) -> dict:
    session = guard(pty_login.start_login, body.name)
    return session.view()


@r.get("/api/login")
def login_list() -> dict:
    return {"sessions": pty_login.list_sessions()}


@r.get("/api/login/{sid}")
def login_stream(sid: str, offset: int = Query(default=0)) -> dict:
    session = pty_login.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="no such login session")
    raw = session.since(offset)
    return {
        **session.view(),
        **raw,
        "data": pty_login.strip_ansi(raw["data"]),
        "auth_url": session.auth_url(),
        "result": pty_login.login_result(sid),
    }


@r.post("/api/login/{sid}/input")
def login_input(sid: str, body: InputIn) -> dict:
    session = pty_login.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="no such login session")
    clean_text = body.text.strip()
    ok = session.write(clean_text, body.newline)
    return {**session.view(), "written": ok}


@r.post("/api/login/{sid}/kill")
def login_kill(sid: str) -> dict:
    session = pty_login.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="no such login session")
    session.kill()
    return session.view()


# --- debug ------------------------------------------------------------------
# agy-lab had a /dom + /frame debug layer; this is its equivalent here: enough to
# diagnose a misbehaving pty/login or a missing agy/script on a live deployment.

@r.get("/api/debug")
def debug() -> dict:
    import os as _os
    import shutil as _sh
    import sys as _sys

    agy = api.agy_path()
    sessions = [{**v, "result": pty_login.login_result(v["id"])} for v in pty_login.list_sessions()]
    return {
        "python": _sys.version.split()[0],
        "platform": _sys.platform,
        "script": _sh.which("script"),
        "agy_binary": CFG.agy_binary,
        "agy_resolved": agy,
        "agy_version": api.agy_version(),
        "agy_executable": _os.access(agy, _os.X_OK),
        "manager_root": str(CFG.manager_root),
        "live_dir": str(CFG.live_dir),
        "fake_ssh": CFG.fake_ssh,
        "prompt_timeout": CFG.prompt_timeout,
        "login_timeout": CFG.login_timeout,
        "acp_auto_approve": CFG.acp_auto_approve,
        "acp_sandbox": CFG.acp_sandbox,
        "login_sessions": sessions,
    }


@r.get("/api/debug/pty/{sid}")
def debug_pty(
    sid: str,
    offset: int = Query(default=0),
    limit: int = Query(default=4000),
) -> dict:
    session = pty_login.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="no such login session")
    raw = session.since(offset)
    text = raw["data"]
    return {
        **session.view(),
        "offset": raw["offset"],
        "raw_bytes": len(text),
        "raw": text[:limit],
        "clean": pty_login.strip_ansi(text)[:limit],
    }


app.include_router(r)


# --- ACP (Agent Client Protocol) WebSocket Bridge ---------------------------

async def handle_acp_ws(
    websocket: WebSocket,
    token: str | None = None,
    account: str | None = None,
) -> None:
    supplied = token or ""
    if not supplied or not hmac.compare_digest(supplied, CFG.token):
        await websocket.close(code=1008, reason="bad or missing token")
        return

    await websocket.accept()

    snap = api.snapshot()
    acct = account or snap.get("active")
    if not acct:
        err_msg = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": "No active agy account configured on server."},
        })
        await websocket.send_text(err_msg)
        await websocket.close(code=1011, reason="no active account")
        return

    try:
        home = api.account_home(acct)
    except Exception as exc:
        err_msg = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": f"Invalid account {acct}: {exc}"},
        })
        await websocket.send_text(err_msg)
        await websocket.close(code=1011, reason="account not found")
        return

    agy = api.agy_path()
    cmd = [agy, "acp", "--stdio"]
    if CFG.acp_auto_approve:
        cmd.append("--dangerously-skip-permissions")
    if CFG.acp_sandbox:
        cmd.append("--sandbox")
    if CFG.acp_process_per_session:
        cmd.append("--process-per-session")

    env = dict(os.environ)
    env["HOME"] = str(home)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(home),
        )
    except Exception as exc:
        err_msg = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32603, "message": f"Failed to spawn agy acp: {exc}"},
        })
        await websocket.send_text(err_msg)
        await websocket.close(code=1011, reason="spawn error")
        return

    async def ws_to_stdin():
        try:
            while True:
                data = await websocket.receive_text()
                if not data:
                    continue
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write(data.encode("utf-8") + b"\n")
                    await proc.stdin.drain()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            pass
        finally:
            if proc.stdin and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    async def stdout_to_ws():
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text:
                    await websocket.send_text(text)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            pass

    async def drain_stderr():
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
        except Exception:
            pass

    tasks = [
        asyncio.create_task(ws_to_stdin()),
        asyncio.create_task(stdout_to_ws()),
        asyncio.create_task(drain_stderr()),
    ]

    try:
        await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/api/acp/ws")
@app.websocket("/ws/acp")
async def websocket_acp(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    account: str | None = Query(default=None),
):
    await handle_acp_ws(websocket, token=token, account=account)


@app.exception_handler(HTTPException)
async def http_exc_handler(request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=CFG.port, log_level="info")


if __name__ == "__main__":
    main()
