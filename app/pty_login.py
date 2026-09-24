"""Interactive agy login over a pty, streamed to the browser.

agy's *interactive* mode is a full-screen TUI that emits ANSI screen-control
codes; a browser log box cannot render that. So we do NOT run the TUI. Instead we
run agy in **print mode** (`agy -p ...`) inside a pty: with no credential it prints
an OAuth URL and waits for the pasted code on the terminal — a line-based flow that
streams cleanly. On success agy writes the token file into a temp HOME, which we
then import into agy-cli-manager. This is the flow agy-lab proved on Railway.
Linux/container only — `script` is not on Windows.
"""
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import config
from . import manager_api

CFG = config.CONFIG
MAX_BUFFER = 512 * 1024

LOGIN_PROMPT = "Reply with exactly: OK"

# agy (and any TUI it briefly touches) emits screen-control codes. Strip them so
# the browser log shows readable text instead of escape-code soup. Offsets still
# count RAW bytes; only the displayed text is cleaned.
_ANSI = re.compile(
    r"\x1b\[[0-9;:?<>=]*[ -/]*[@-~]"         # CSI  (params may carry private markers > < = :)
    r"|\x1b[\]_^X][^\x1b\x07]*(?:\x07|\x1b\\)"  # OSC/APC/PM/SOS (ESC_G kitty, ESC] title)
    r"|\x1b[@-Z\\^_a-z]"                     # other two-char escapes
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f]"         # stray C0 except \t and \n
)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


class PtySession:
    def __init__(self, sid: str, command: str, env: dict, on_exit=None):
        self.id = sid
        self.command = command
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.running = True
        self.on_exit = on_exit

        self._chunks: list[bytes] = []
        self._bytes = 0
        self._dropped = 0
        self._lock = threading.Lock()

        self._proc = subprocess.Popen(
            ["script", "-q", "-f", "-c", command, "/dev/null"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(CFG.manager_root),
            bufsize=0,
            start_new_session=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        fd = self._proc.stdout.fileno()
        try:
            while True:
                try:
                    b = os.read(fd, 4096)
                except OSError:
                    break
                if not b:
                    break
                self._push(b)
        finally:
            self._finish(self._proc.wait())

    def _push(self, b: bytes) -> None:
        with self._lock:
            self._chunks.append(b)
            self._bytes += len(b)
            while self._bytes > MAX_BUFFER and len(self._chunks) > 1:
                self._dropped += len(self._chunks.pop(0))

    def _finish(self, code: int | None) -> None:
        if not self.running:
            return
        self.running = False
        self.exit_code = code
        self.ended_at = time.time()
        if self.on_exit:
            try:
                self.on_exit()
            except Exception as exc:  # never let a capture failure kill the reader
                self._push(f"\n[agy-web] capture failed: {exc}\n".encode())

    @property
    def total(self) -> int:
        with self._lock:
            return self._dropped + self._bytes

    def write(self, text: str, newline: bool = True) -> bool:
        if not self.running:
            return False
        data = (text + ("\n" if newline else "")).encode("utf-8", "replace")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def since(self, offset: int) -> dict:
        with self._lock:
            frm = max(offset, self._dropped)
            buf = b"".join(self._chunks)
            slice_ = buf[frm - self._dropped:]
            return {
                "data": slice_.decode("utf-8", "replace"),
                "offset": self._dropped + self._bytes,
                "truncated": frm > offset,
            }

    def kill(self) -> None:
        if not self.running:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGHUP)
        except (ProcessLookupError, OSError):
            pass
        threading.Timer(3.0, self._force_kill).start()

    def _force_kill(self) -> None:
        if not self.running:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    def view(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "running": self.running,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "bytes": self.total,
        }


_sessions: dict[str, PtySession] = {}
_login_results: dict[str, dict] = {}
_counter = 0
_registry_lock = threading.Lock()


def _capture(temp: Path, name: str, sid: str) -> None:
    """After the login session ends, import the temp HOME's token into the pool."""
    token = temp / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    if token.is_file():
        try:
            manager_api.import_current(name, str(temp))
            _login_results[sid] = {"ok": True, "account": name, "detail": "profile captured"}
        except Exception as exc:
            _login_results[sid] = {"ok": False, "account": name, "detail": f"import failed: {exc}"}
    else:
        _login_results[sid] = {
            "ok": False,
            "account": name,
            "detail": "agy exited without writing a token file (login incomplete or failed)",
        }
    shutil.rmtree(temp, ignore_errors=True)


def start_login(name: str) -> PtySession:
    """Run a headless agy OAuth login in a pty against a temp HOME."""
    global _counter
    name = name.strip()
    if not name:
        raise ValueError("account name is required")

    with _registry_lock:
        _counter += 1
        sid = f"login{_counter}"

    temp = CFG.manager_root / "login-tmp" / sid
    temp.mkdir(parents=True, exist_ok=True)

    agy = manager_api.agy_path()
    cmd = shlex.join(
        [
            agy,
            "-p",
            LOGIN_PROMPT,
            "--output-format",
            "text",
            "--print-timeout",
            f"{CFG.login_timeout}s",
        ]
    )

    env = dict(os.environ)
    env["HOME"] = str(temp)
    env["TERM"] = "xterm-256color"
    if CFG.fake_ssh:
        env["SSH_CONNECTION"] = "10.0.0.2 52344 10.0.0.1 22"
        env["SSH_CLIENT"] = "10.0.0.2 52344 22"
        env["SSH_TTY"] = "/dev/pts/0"

    session = PtySession(sid, cmd, env, on_exit=lambda: _capture(temp, name, sid))

    with _registry_lock:
        _sessions[sid] = session
        # Keep the last few finished sessions for post-mortem; drop older ones.
        for key in list(_sessions):
            if len(_sessions) <= 8:
                break
            if not _sessions[key].running:
                _sessions.pop(key, None)
    return session


def get(sid: str) -> PtySession | None:
    return _sessions.get(sid)


def list_sessions() -> list[dict]:
    return [s.view() for s in _sessions.values()]


def login_result(sid: str) -> dict | None:
    return _login_results.get(sid)
