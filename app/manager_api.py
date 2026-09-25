"""Thin, serialization-friendly wrapper over agy_cli_manager.

agy-cli-manager owns the account pool, quota tracking and failover logic; this
module only adapts it to the web layer: a single ManagerPaths built from config,
dataclass results turned into dicts, an account-delete the CLI does not expose,
and the prompt runner that the manager itself does not provide.
"""
import dataclasses
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import agy_cli_manager.manager as M

from . import config

CFG = config.CONFIG

_paths = M.build_paths(CFG.manager_root)

# agy's own words for "this credential is dead", so a failed prompt says
# "re-authenticate" instead of dumping a stack trace. IneligibleTier is the
# Antigravity/Code-Assist rejection seen when a client/account is not entitled.
AUTH_FAILURE = re.compile(
    r"not logged in|you are not logged into|authentication required|"
    r"not (?:authenticated|signed[- ]?in)|please (?:sign|log)[- ]?in|"
    r"unauthenticated|ineligibletier|unsupported_client|401",
    re.IGNORECASE,
)


def init() -> None:
    M.ensure_layout(_paths)


def paths() -> M.ManagerPaths:
    return _paths


def agy_path() -> str:
    return M.resolve_agy_binary(CFG.agy_binary)


def _ser(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


# --- read -------------------------------------------------------------------

def snapshot() -> dict:
    return M.get_status_snapshot(_paths)


def info() -> dict:
    snap = M.get_status_snapshot(_paths)
    return {
        "root": str(_paths.root),
        "runtime_dir": str(_paths.runtime_dir),
        "live_dir": snap.get("live_dir"),
        "agy_binary": CFG.agy_binary,
        "agy_resolved": M.resolve_agy_binary(CFG.agy_binary),
        "active": snap.get("active"),
        "switch_mode": snap.get("switch_mode"),
        "switch_policy": snap.get("switch_policy"),
        "switch_runtime": snap.get("switch_runtime"),
        "log_watch": snap.get("log_watch"),
        "accounts": snap.get("accounts", {}),
    }


def models(name: str | None = None) -> dict:
    return M.list_models(_paths, name, agy_binary=CFG.agy_binary)


def get_policy() -> dict:
    return M.get_switch_policy(_paths)


def verify() -> dict:
    return M.verify_accounts(_paths)


# --- write ------------------------------------------------------------------

def switch(name: str) -> str:
    return M.switch_account(_paths, name)


def switch_next() -> str:
    return M.switch_next(_paths)


def set_enabled(name: str, enabled: bool) -> None:
    M.set_enabled(_paths, name, enabled)


def mark_bad(name: str, reason: str, cooldown_minutes: int) -> None:
    M.mark_bad(_paths, name, reason, cooldown_minutes)


def clear_bad(name: str) -> None:
    M.clear_bad(_paths, name)


def set_mode(mode: str) -> str:
    return M.set_switch_mode(_paths, mode)


def update_policy(**kwargs) -> dict:
    return M.update_switch_policy(_paths, **kwargs)


def set_live_dir(directory: str | None) -> None:
    M.set_live_dir(_paths, Path(directory).expanduser() if directory else None)


def apply_active() -> str:
    return M.apply_active(_paths)


def ensure_active(force: bool = False):
    return _ser(M.ensure_active_account(_paths, force=force))


def rotate(reason: str, cooldown_minutes: int, force_switch: bool):
    return _ser(
        M.rotate_after_failure(
            _paths,
            reason,
            cooldown_minutes=cooldown_minutes,
            force_switch=force_switch,
            trigger="web",
        )
    )


def refresh_usage(name: str | None = None):
    return _ser(M.refresh_account_usage(_paths, name, agy_binary=CFG.agy_binary))


def import_current(name: str, source: str | None = None) -> None:
    M.import_current(_paths, name, Path(source).expanduser() if source else None)


def delete_account(name: str) -> None:
    """agy-cli-manager has no delete; drop the state entry and the profile dir.

    Done under the manager lock so a concurrent switch cannot resurrect it.
    """
    adir = M.account_dir(_paths, name)  # validates the name against traversal
    with M.manager_lock(_paths):
        state = M.sync_state_from_disk(_paths, M.load_state(_paths))
        if name not in state.get("accounts", {}):
            raise KeyError(name)
        state["accounts"].pop(name, None)
        if state.get("active") == name:
            state["active"] = None
        state = M.sync_state_from_disk(_paths, state)
        M.save_state(_paths, state)
    shutil.rmtree(adir, ignore_errors=True)


# --- prompt -----------------------------------------------------------------

def account_home(name: str) -> Path:
    return M.account_dir(_paths, name)


def run_prompt(
    prompt: str,
    account: str | None = None,
    timeout: int | None = None,
    tools: bool | None = None,
    sandbox: bool | None = None,
    cwd: str | None = None,
    model: str | None = None,
) -> dict:
    """Run one prompt through agy as a given account (default: the active one).

    agy derives its whole home from $HOME, so pointing HOME at the account's
    profile dir runs it as that account without disturbing the shared runtime.

    Headless note: a remote/print agy blocks on tool-permission prompts that nobody
    can answer, and agy exposes no per-tool allow flag — auto-approval is
    all-or-nothing. So --dangerously-skip-permissions is the DEFAULT here
    (CFG.auto_approve); `tools` overrides per request. `sandbox` adds --sandbox to
    narrow what auto-approved tools may touch. `cwd` sets the workspace (default:
    the account home); HOME still selects the account.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")

    snap = M.get_status_snapshot(_paths)
    name = account or snap.get("active")
    if not name:
        raise ValueError("No active account and none specified. Add or activate one first.")

    home = M.account_dir(_paths, name)
    if not (home / ".gemini").is_dir():
        raise ValueError(f"Account '{name}' has no profile at {home}.")

    to = int(timeout or CFG.prompt_timeout)
    agy = M.resolve_agy_binary(CFG.agy_binary)
    auto = CFG.auto_approve if tools is None else tools
    sb = CFG.sandbox if sandbox is None else sandbox
    args = [agy, "-p", prompt, "--print-timeout", f"{to}s", "--output-format", "text"]
    if auto:
        args.append("--dangerously-skip-permissions")
    if sb:
        args.append("--sandbox")
    mdl = model or CFG.default_model
    if mdl:
        args += ["--model", mdl]

    env = dict(os.environ)
    env["HOME"] = str(home)
    workdir = Path(cwd).expanduser() if cwd else home

    started = time.time()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=to + 15,
            env=env,
            cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "account": name,
            "cwd": str(workdir),
            "ms": int((time.time() - started) * 1000),
            "error": f"agy did not answer within {to}s (timed out).",
        }
    except FileNotFoundError:
        return {"ok": False, "account": name, "error": f"agy binary not found: {agy}"}

    ms = int((time.time() - started) * 1000)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()

    if proc.returncode != 0 or AUTH_FAILURE.search(out) or AUTH_FAILURE.search(err):
        detail = err or out or f"agy exited {proc.returncode}"
        return {
            "ok": False,
            "account": name,
            "cwd": str(workdir),
            "ms": ms,
            "exit": proc.returncode,
            "auth_failure": bool(AUTH_FAILURE.search(out) or AUTH_FAILURE.search(err)),
            "error": detail[:2000],
        }

    return {
        "ok": True,
        "account": name,
        "cwd": str(workdir),
        "ms": ms,
        "exit": proc.returncode,
        "answer": out,
    }
