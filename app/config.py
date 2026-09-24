import os
import shutil
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self) -> None:
        # Bearer token guarding every /api/* route. Mandatory: this server can run
        # prompts and read live Google credentials, so an open port is a shell.
        self.token = os.environ.get("AGY_WEB_TOKEN", "")
        self.port = _int("PORT", 8080)

        # agy-cli-manager state (accounts/, runtime/, state.json). On the volume so
        # the pool survives a redeploy.
        self.manager_root = Path(
            os.environ.get("AGY_MANAGER_ROOT") or (Path.home() / ".agy-cli-manager")
        ).expanduser()

        # The live agy home the manager syncs the active profile into. agy-cli-manager
        # also reads AGY_MANAGER_LIVE_DIR itself (default_live_dir), so this is mainly
        # for display and the explicit set-live-dir endpoint.
        live = os.environ.get("AGY_MANAGER_LIVE_DIR")
        self.live_dir = Path(live).expanduser() if live else (Path.home() / ".gemini")

        self.agy_binary = os.environ.get("AGY_BINARY") or shutil.which("agy") or "agy"
        self.prompt_timeout = _int("AGY_PROMPT_TIMEOUT", 180)
        self.login_timeout = _int("AGY_LOGIN_TIMEOUT", 600)

        # agy prints an OAuth URL to paste a code back only when it believes it is on
        # a remote host; a container has no browser to launch. Same trick agy-lab uses.
        self.fake_ssh = _bool("AGY_FAKE_SSH", True)


CONFIG = Config()
