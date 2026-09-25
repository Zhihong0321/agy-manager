# agy-web

A web UI + HTTP API for running **multiple Antigravity CLI (`agy`) accounts** from
one server, and for sending prompts to `agy` over HTTP. Built for a Railway
container.

It is a thin web layer over [`zcop/agy-cli-manager`](https://github.com/zcop/agy-cli-manager),
which owns the account pool, quota tracking and failover. `agy-web` adds:

1. **A management UI** — auth/login, the multi-account pool, and per-account status
   (active/standby/cooldown/disabled, health, short-window quota, reset countdown,
   identity, proxy).
2. **A prompt API** — `POST /api/prompt {prompt}` runs `agy -p` as the active (or a
   named) account and returns the answer.

## Why a container (and not native Windows)

`agy-cli-manager` swaps accounts by copying a credential **file**
(`.gemini/antigravity-cli/antigravity-oauth-token`). That file only exists where agy
has **no OS keyring** to fall back on — i.e. a Linux container. On Windows agy stores
the token in Credential Manager (`gemini:antigravity`) and writes no file, so the
manager cannot see it. **Deploy this on Railway/Linux.** (This is the same regime
`agy-lab` already proved: agy-in-container writes the token file and it survives a
redeploy on the volume.)

## Architecture

```
browser ── bearer token ──> FastAPI (app/server.py)
                              ├─ /api/*        account ops -> agy_cli_manager Python API
                              ├─ /api/prompt   -> subprocess: HOME=<account> agy -p "..."
                              ├─ /api/login    -> pty: `script -qc "agy-cli-manager login"`
                              └─ /             -> static single-page UI
state on the /data volume:
  /data/.agy-cli-manager/{accounts/<name>/.gemini, runtime/, state.json}
  /data/.gemini            (live_dir: the home agy itself reads)
  /data/.local/bin/agy     (installed at runtime by entrypoint.sh)
```

- **Prompt** (`manager_api.run_prompt`): resolves the account's profile dir, runs
  `agy -p <prompt> --print-timeout <n>s --output-format text` with `HOME` pointed at
  that profile, so it runs as that account without disturbing the shared runtime.
  Because a headless agy blocks on permission prompts and agy has no per-tool allow
  flag, `--dangerously-skip-permissions` is **on by default** (`AGY_AUTO_APPROVE`);
  `tools` overrides per request. `sandbox`, `model` and `cwd` are also settable.
- **Login** (`pty_login.py`): `agy-cli-manager login` requires a real TTY (it hands
  the terminal to a live `agy`). We run it under util-linux `script` to allocate a
  pty, stream its output to the browser, and stream the pasted OAuth code back in —
  the mechanism `agy-lab` uses. `SSH_*` vars are set so agy prints a URL instead of
  trying to launch a browser.

## Requirements

- Python 3.10+
- `agy` on PATH (or `AGY_BINARY`); installed automatically at container start
- util-linux `script` (for interactive login) — in the Docker image

## Local development

```bash
cd agy-web
python -m venv .venv && . .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# or, against a local clone of the manager:
# pip install -e ../agy-cli-manager

export AGY_WEB_TOKEN="dev-token-at-least-16-chars"
export AGY_MANAGER_ROOT="$HOME/.agy-cli-manager"
export AGY_MANAGER_LIVE_DIR="$HOME/.gemini"
# export AGY_BINARY=/path/to/agy        # defaults to `which agy`
python -m app.server                     # http://localhost:8080
```

> On **Windows** the account/prompt paths that shell out to `agy` will not behave
> (wincred, and `script` is absent), but the HTTP layer, auth, UI, and manager
> state all run. Full end-to-end needs Linux.

## Railway deploy

1. **New service → deploy from this repo.** The Dockerfile is at the repo root.
2. **Volume → mount at `/data`.** Everything persistent lives there. Without it,
   accounts vanish on every redeploy.
3. **Variables:**
   - `AGY_WEB_TOKEN` — 16+ random chars. **The service refuses to start without it.**
   - The rest default correctly for `/data` (see below); override only if needed.
4. Deploy, open the service URL, paste the token, work the UI.

`railway.json` sets the Dockerfile builder and `/healthz` healthcheck.

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `AGY_WEB_TOKEN` | — (required, ≥16) | Bearer token for every `/api/*` route |
| `PORT` | `8080` | Listen port (Railway injects this) |
| `AGY_MANAGER_ROOT` | `~/.agy-cli-manager` | Manager state + account profiles |
| `AGY_MANAGER_LIVE_DIR` | `~/.gemini` | Live agy home the active profile syncs into |
| `AGY_BINARY` | `which agy` | Path to the agy binary |
| `AGY_PROMPT_TIMEOUT` | `180` | Default per-prompt timeout (seconds) |
| `AGY_LOGIN_TIMEOUT` | `600` | Interactive login timeout (seconds) |
| `AGY_FAKE_SSH` | `true` | Set `SSH_*` so agy prints an OAuth URL (no browser) |
| `AGY_AUTO_APPROVE` | `true` | Default `--dangerously-skip-permissions` on `/api/prompt` (headless needs it) |
| `AGY_SANDBOX` | `false` | Default `--sandbox`, narrowing what auto-approved tools may touch |
| `AGY_MODEL` | *(unset)* | Default `--model` for prompts |

In the image these resolve to `/data/...` because `HOME=/data`.

## API

All `/api/*` need `Authorization: Bearer $AGY_WEB_TOKEN` (or `?token=`). `/healthz`
and `/` are open.

| | |
|---|---|
| `GET /healthz` | liveness (Railway healthcheck) |
| `GET /api/status` | active, switch mode/policy/runtime, log-watch, every account's state |
| `POST /api/prompt` | `{prompt, account?, timeout?, tools?}` → `{ok, answer\|error, account, ms}` |
| `POST /api/login` | `{name}` → start interactive login, returns a pty session id |
| `GET /api/login/{sid}?offset=N` | stream login output from a byte offset |
| `POST /api/login/{sid}/input` | `{text, newline?}` — paste the OAuth code / answer prompts |
| `POST /api/login/{sid}/kill` | end the login session |
| `GET /api/login` | list login sessions |
| `POST /api/switch` | `{name}` — make an account active |
| `POST /api/switch-next` | rotate to the next eligible account |
| `POST /api/ensure-active` | `{force?}` — recover/preflight an active account |
| `POST /api/rotate` | `{reason, cooldown_minutes, force_switch}` — failover now |
| `POST /api/accounts/{name}/enable` | `{enabled}` |
| `POST /api/accounts/{name}/mark-bad` | `{reason, cooldown_minutes}` |
| `POST /api/accounts/{name}/clear-bad` | clear cooldown/bad state |
| `POST /api/accounts/{name}/refresh-usage` | live quota refresh (runs agy) |
| `DELETE /api/accounts/{name}` | remove the account + its saved profile |
| `POST /api/import-current` | `{name, source?}` — seed from an existing `.gemini` home |
| `GET /api/models?name=` | `agy models` for an account |
| `GET /api/verify` | verify saved accounts' auth/runtime usability |
| `GET/POST /api/switch-policy` | read/set thresholds + candidate strategy |
| `POST /api/switch-mode` | `{mode: auto\|manual}` |
| `POST /api/set-live-dir` | `{dir}` |
| `POST /api/apply-active` | re-sync the active profile into live_dir |

### Example

```bash
LAB=https://your-service.up.railway.app
TOK=$AGY_WEB_TOKEN
curl -s -H "Authorization: Bearer $TOK" "$LAB/api/status"
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"prompt":"Reply with exactly: OK"}' "$LAB/api/prompt"
```

### Headless / API usage

A remote `agy` **blocks on tool-permission prompts** that nobody can answer, and agy
exposes **no per-tool allow flag** — auto-approval is all-or-nothing
(`--dangerously-skip-permissions`). So for headless/API use the service defaults to
auto-approve (`AGY_AUTO_APPROVE=true`); pass `tools:false` to disable, or
`sandbox:true` to add `--sandbox` and narrow what auto-approved tools may touch.

```bash
# plain run (auto-approve on by default)
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"prompt":"Summarise this repo in 3 bullets"}' "$LAB/api/prompt"

# auto-approve + sandbox + workspace + model + longer timeout
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"prompt":"List the files in the cwd and report counts","sandbox":true,"cwd":"/data/work","timeout":300}' \
     "$LAB/api/prompt"

# explicit no-tools run (hangs if agy wants a tool — avoid headless)
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
     -d '{"prompt":"Reply with exactly: OK","tools":false}' "$LAB/api/prompt"
```

Fields: `prompt` (required), `account` (default active), `timeout` (s), `tools`
(auto-approve override), `sandbox`, `cwd` (workspace; `HOME` still selects the
account), `model`. Response: `{ok, answer|error, account, ms, exit, cwd}`.

Notes:
- `cwd` should be a directory agy already trusts; a brand-new cwd can trigger a
  trust prompt that headless cannot answer (the run then ends at `timeout`).
- Runs are synchronous and bounded by `timeout`.

## Enrolling accounts

**Interactive (UI → “Add account”)**: enter a label, **Start login**. The terminal
panel streams the real `agy` session — complete onboarding, open the OAuth URL,
paste the code back into the input box, then exit agy. The profile is captured
under the detected account. Repeat per Google account.

**Import**: if you already have a `.gemini` home with a token file,
`POST /api/import-current {name, source}`.

Each account is one Google login. After enrollment, switching is instant (a file
copy) and `watch`/auto-mode fails over on `Individual quota reached`.

## Security

- The server **will not start** without `AGY_WEB_TOKEN` ≥16 chars; there is no dev
  bypass. An open `/api/prompt` on a public hostname is remote code execution via
  agy's tools.
- `/api/prompt` auto-approves tools by default (`AGY_AUTO_APPROVE=true`) because a
  headless agy cannot answer permission prompts. That is **full tool auto-approval
  with nobody watching** — pair it with `sandbox:true` and keep `cwd` scoped. Set
  `AGY_AUTO_APPROVE=false` to force callers to opt in per request with `tools:true`.
- Saved profiles contain live Google refresh tokens, in plaintext, on the volume.
  Treat `/data` as a secret store.
- Consumer Google accounts driven headless from a datacenter IP are the pattern that
  gets accounts flagged. Per-account proxies are supported by the manager
  (`proxy-set`) if that bites.

## Not verified locally (needs the container)

Validated on a workstation: HTTP layer, bearer auth, static UI, manager state,
import/switch/enable/disable/mark-bad/clear-bad/delete, switch-policy, and prompt
**error handling**. Not exercised locally because they need Linux + a real agy file
login: actual `agy -p` answers, the pty interactive login, and live quota refresh.
Test those on Railway.
