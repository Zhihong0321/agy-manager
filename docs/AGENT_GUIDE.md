# Agent Integration Guide: Antigravity Cloud Hub (`agy-web`)

> **Audience**: AI Coding Agents, automated subagents, and programmatic workflows.  
> **Service Role**: Cloud-hosted Google Antigravity CLI (`agy 1.5.0`) multi-account execution engine on Railway.

---

## 1. Overview & Connection Info

This cloud hub runs on Railway and hosts a pool of authenticated Antigravity accounts. It offloads all agent turns, reasoning, and tool executions to the cloud (zero local CPU/RAM load).

* **Cloud Base URL**: `https://my-agy.up.railway.app`
* **WebSocket Base URL**: `wss://my-agy.up.railway.app`
* **Auth Secret**: `vfr7106`
* **Authentication Method**: 
  * HTTP Header: `Authorization: Bearer vfr7106`
  * Query Parameter: `?token=vfr7106`

---

## 2. Quickstart for Agents (Shell / Subprocess)

If you have shell/bash access, use the included local runner `scripts/agy_remote.py`:

```bash
# Set environment variables once (optional but recommended)
export AGY_REMOTE_URL="https://my-agy.up.railway.app"
export AGY_REMOTE_TOKEN="vfr7106"

# 1. Inspect worker pool status & quotas
python scripts/agy_remote.py --accounts

# 2. Execute a prompt on the active worker
python scripts/agy_remote.py -p "Summarize README.md in 3 bullets"

# 3. Execute a prompt on a SPECIFIC worker
python scripts/agy_remote.py -a "zhihong@eternalgy.me" -p "Write an async worker in Python"

# 4. List models supported by a specific worker
python scripts/agy_remote.py --models -a "masterkey"

# 5. Live refresh quotas from Google servers
python scripts/agy_remote.py --refresh-quota -a "leanhhu0616"

# 6. Switch the active worker
python scripts/agy_remote.py --switch "leanhhu0616"
```

---

## 3. Direct HTTP REST API Reference

All requests must include the header `Authorization: Bearer vfr7106`.

### `POST /api/prompt` — Run a Turn / Task
Executes a prompt against a cloud worker.

* **Method**: `POST`
* **URL**: `https://my-agy.up.railway.app/api/prompt`
* **Headers**:
  ```http
  Authorization: Bearer vfr7106
  Content-Type: application/json
  ```
* **Request Body Schema**:
  ```json
  {
    "prompt": "Your instruction here",        // Required (string)
    "account": "zhihong@eternalgy.me",        // Optional: worker name (defaults to active worker)
    "model": "gemini-3.8-flash-high",         // Optional: model override
    "timeout": 180,                           // Optional: timeout in seconds (default: 180)
    "tools": true,                            // Optional: auto-approve tools (default: true for headless)
    "sandbox": false,                         // Optional: restrict tools in terminal sandbox (default: false)
    "cwd": "/storage/workspace"               // Optional: working directory on persistent volume
  }
  ```
* **Success Response (`200 OK`)**:
  ```json
  {
    "ok": true,
    "account": "zhihong@eternalgy.me",
    "cwd": "/storage/.agy-cli-manager/accounts/zhihong@eternalgy.me",
    "ms": 3210,
    "exit": 0,
    "answer": "Here is the response from agy..."
  }
  ```
* **Failure Response (`200 OK` with `ok: false` or `400/500`)**:
  ```json
  {
    "ok": false,
    "account": "zhihong@eternalgy.me",
    "cwd": "...",
    "ms": 1500,
    "exit": 1,
    "auth_failure": false,
    "error": "Error message from agy CLI"
  }
  ```

> **`/api/prompt` is cut off after ~5 minutes** by the Railway proxy (`HTTP 502: upstream error`),
> because it holds the connection open until agy finishes. Use it only for short prompts. For anything
> that may run longer, use the async jobs below.

---

### `POST /api/jobs` — Run a Task in the Background (submit, then poll)
Same body as `/api/prompt` (and `timeout` may be up to 3600 s), but it returns at once with a job id.
The agent runs in the background; no HTTP request stays open longer than ~25 s.

* **Submit**: `POST /api/jobs` -> `{"job_id": "...", "status": "queued", "account": "...", ...}`
  (`400` for an empty prompt or unknown account)
* **Poll**: `GET /api/jobs/{job_id}?wait=20` -> long-polls up to `wait` seconds (max 25) and returns as soon as
  the job finishes. `status` is `queued` -> `running` -> `done` | `failed`. When finished it carries the same
  fields as `/api/prompt`: `ok`, `answer` (on success) or `error` / `exit` / `auth_failure` (on failure), `ms`, `cwd`.
* **List**: `GET /api/jobs` -> all jobs from the last 24 h (no answers).
* Jobs for the same account run one after another; jobs for different accounts run in parallel
  (give each parallel job its own `account`).
* Finished jobs are kept 24 h (also across restarts). A job that was running when the server restarted
  or redeployed comes back as `failed` with `"server restarted while the job was running"`; resubmit it.
* `404` on poll = unknown or expired job id.

```bash
JOB=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"prompt":"long task...","account":"leanhhu0616","timeout":1800}' $LAB/api/jobs | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
curl -s -H "Authorization: Bearer $TOKEN" "$LAB/api/jobs/$JOB?wait=20"     # repeat until status is done/failed
```

---

### `GET /api/status` — Worker Pool Status
Inspect all connected accounts, their health, quota percentages, and switch policies.

* **Method**: `GET`
* **URL**: `https://my-agy.up.railway.app/api/status`
* **Response Schema**:
  ```json
  {
    "active": "Nurul@eternalgy.me",
    "agy_version": "1.5.0",
    "switch_mode": "auto",
    "accounts": {
      "Nurul@eternalgy.me": {
        "status": "active",
        "health_status": "healthy",
        "enabled": true,
        "usage_windows": {
          "short": {"value": 100.0, "reset_at": "2026-11-05T11:07:03+00:00"},
          "weekly": {"value": 6.67, "reset_at": "2026-11-06T05:53:15+00:00"}
        },
        "identity": {"email": "nurul@eternalgy.me"}
      },
      "leanhhu0616": {
        "status": "standby",
        "health_status": "healthy",
        "enabled": true,
        "usage_windows": {
          "short": {"value": 100.0},
          "weekly": {"value": 100.0}
        },
        "identity": {"email": "leanhhu061212@gmail.com"}
      }
    }
  }
  ```

---

### `GET /api/models` — List Models Per Worker
Lists all models available to a specific account (entitlements vary per Google account).

* **Method**: `GET`
* **URL**: `https://my-agy.up.railway.app/api/models?name=masterkey`
* **Response Schema**:
  ```json
  {
    "account": "masterkey",
    "models": [
      {
        "name": "gemini-3.8-flash-high\tGemini 3.8 Flash (High)",
        "provider": "gemini-3.8-flash-high",
        "family": "Gemini 3.8 Flash",
        "variant": "High"
      },
      {
        "name": "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)",
        "provider": "claude-sonnet-4-6",
        "family": "Claude Sonnet 4.6",
        "variant": "Thinking"
      }
    ]
  }
  ```

---

### `POST /api/accounts/{name}/refresh-usage` — Live Quota Refresh
Forces an instant quota query against Google Code Assist servers for the named account.

* **Method**: `POST`
* **URL**: `https://my-agy.up.railway.app/api/accounts/Nurul@eternalgy.me/refresh-usage`
* **Body**: `{}`
* **Response**:
  ```json
  {
    "account": "Nurul@eternalgy.me",
    "project_id": "aicode-consumers",
    "short_usage_value": 100.0,
    "weekly_usage_value": 6.67,
    "short_reset_at": "2026-11-05T11:07:03+00:00",
    "weekly_reset_at": "2026-11-06T05:53:15+00:00"
  }
  ```

---

### `POST /api/switch` & `POST /api/switch-next` — Account Rotation
Manually activate a specific worker or cycle to the next eligible worker.

* **Switch Specific**:
  * `POST https://my-agy.up.railway.app/api/switch`
  * Body: `{"name": "leanhhu0616"}`
* **Switch Next**:
  * `POST https://my-agy.up.railway.app/api/switch-next`
  * Body: `{}`

---

## 4. ACP (Agent Client Protocol) WebSocket Bridge

If your agent system speaks the **Agent Client Protocol (ACP)** via JSON-RPC 2.0:

* **Endpoint**: `wss://my-agy.up.railway.app/api/acp/ws?token=vfr7106`
* **Target a Specific Worker**: `wss://my-agy.up.railway.app/api/acp/ws?token=vfr7106&account=zhihong@eternalgy.me`

### Handshake Sequence:
1. Connect via WebSocket.
2. Send JSON-RPC `initialize`:
   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "initialize",
     "params": {
       "protocolVersion": 1,
       "clientInfo": {"name": "subagent-client", "version": "1.0"}
     }
   }
   ```
3. Receive server capabilities (`loadSession`, `promptCapabilities`, `sessionCapabilities`).
4. Send session and prompt requests over the established stream.

---

## 5. Agent Best Practices & Decision Rules

1. **Worker Selection**:
   * Inspect `GET /api/status` before running large batch jobs.
   * Check `usage_windows.short.value` (higher % = more remaining quota).
   * Specify `"account": "<worker>"` in `POST /api/prompt` to balance work evenly across accounts.
2. **Auto-Failover**:
   * With `switch_mode: auto`, you can simply omit `"account"` and send prompts to the active worker. If it exhausts its quota or encounters rate limits, the cloud hub rotates to a standby worker automatically.
3. **Headless Execution Safety**:
   * Always keep `tools: true` (default) for headless operations; otherwise, if `agy` attempts to call an interactive tool, it will block until `timeout`.
   * If running untrusted code or commands, set `"sandbox": true` to enforce terminal isolation.
4. **Model Compatibility**:
   * Different workers may have access to different model tiers. Check `GET /api/models?name=<worker>` before forcing a specific model string.
