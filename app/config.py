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
        self.token = os.environ.get("AGY_WEB_TOKEN", "").strip()
        self.port = _int("PORT", 8080)

        # agy-cli-manager state (accounts/, runtime/, state.json). On the volume so
        # the pool survives a redeploy. Automatically adapts to /storage or /data.
        mgr_env = os.environ.get("AGY_MANAGER_ROOT")
        if not mgr_env:
            if Path("/storage").is_dir():
                mgr_env = "/storage/.agy-cli-manager"
            elif Path("/data").is_dir():
                mgr_env = "/data/.agy-cli-manager"
        self.manager_root = Path(mgr_env or (Path.home() / ".agy-cli-manager")).expanduser()

        # The live agy home the manager syncs the active profile into. agy-cli-manager
        # also reads AGY_MANAGER_LIVE_DIR itself (default_live_dir).
        live = os.environ.get("AGY_MANAGER_LIVE_DIR")
        if not live:
            if Path("/storage").is_dir():
                live = "/storage/.gemini"
            elif Path("/data").is_dir():
                live = "/data/.gemini"
        self.live_dir = Path(live).expanduser() if live else (Path.home() / ".gemini")

        agy_bin = os.environ.get("AGY_BINARY")
        if not agy_bin:
            if Path("/storage/.local/bin/agy").is_file():
                agy_bin = "/storage/.local/bin/agy"
            elif Path("/data/.local/bin/agy").is_file():
                agy_bin = "/data/.local/bin/agy"
        self.agy_binary = agy_bin or shutil.which("agy") or "agy"
        self.prompt_timeout = _int("AGY_PROMPT_TIMEOUT", 180)
        self.login_timeout = _int("AGY_LOGIN_TIMEOUT", 600)

        # agy prints an OAuth URL to paste a code back only when it believes it is on
        # a remote host; a container has no browser to launch. Same trick agy-lab uses.
        self.fake_ssh = _bool("AGY_FAKE_SSH", True)

        # Headless agy blocks on tool-permission prompts nobody can answer, and agy
        # exposes no per-tool allow flag — auto-approval is all-or-nothing. Default ON
        # so the HTTP API is usable headless; per-request `tools` still overrides.
        self.auto_approve = _bool("AGY_AUTO_APPROVE", True)
        # --sandbox narrows what auto-approved tools may touch. Recommended alongside
        # auto-approve; off by default to leave existing behaviour unchanged.
        self.sandbox = _bool("AGY_SANDBOX", False)
        self.default_model = os.environ.get("AGY_MODEL", "")

        # ACP (Agent Client Protocol) server options (v1.5.0+)
        self.acp_auto_approve = _bool("AGY_ACP_AUTO_APPROVE", True)
        self.acp_sandbox = _bool("AGY_ACP_SANDBOX", False)
        self.acp_process_per_session = _bool("AGY_ACP_PROCESS_PER_SESSION", False)


CONFIG = Config()
