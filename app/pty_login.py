"""Interactive agy login over a pty, streamed to the browser.

agy-cli-manager's login_account requires a real TTY (it hands the terminal to a
live `agy` session). A web backend has none, so we run it under util-linux
`script`, which allocates a pty: the OAuth URL streams out to the browser and the
code the user pastes streams back in. This is the same mechanism agy-lab proved
on Railway. Linux/container only — `script` is not on Windows.
"""
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

from . import config

CFG = config.CONFIG
MAX_BUFFER = 512 * 1024


class PtySession:
    def __init__(self, sid: str, command: str, env: dict):
        self.id = sid
        self.command = command
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.running = True

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
_counter = 0
_registry_lock = threading.Lock()


def start_login(name: str) -> PtySession:
    """Launch `agy-cli-manager login <name>` inside a pty and return the session."""
    global _counter
    name = name.strip()
    if not name:
        raise ValueError("account name is required")

    cmd = shlex.join(
        [
            sys.executable,
            "-m",
            "agy_cli_manager.cli",
            "--root",
            str(CFG.manager_root),
            "login",
            name,
            "--agy-binary",
            CFG.agy_binary,
        ]
    )

    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    if CFG.fake_ssh:
        env["SSH_CONNECTION"] = "10.0.0.2 52344 10.0.0.1 22"
        env["SSH_CLIENT"] = "10.0.0.2 52344 22"
        env["SSH_TTY"] = "/dev/pts/0"

    with _registry_lock:
        _counter += 1
        sid = f"login{_counter}"
        session = PtySession(sid, cmd, env)
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
