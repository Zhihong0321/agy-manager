#!/usr/bin/env python3
"""agy_remote: Remote client for agy-web cloud hub.

Connects your local machine (or editor like Zed) to your Railway-hosted
Antigravity CLI (agy), freeing your local PC's CPU and RAM.

Features:
  1. Check & list available workers/accounts:
     python scripts/agy_remote.py --accounts
     python scripts/agy_remote.py --status

  2. List available models (per worker/account):
     python scripts/agy_remote.py --models
     python scripts/agy_remote.py --models --account leanhhu0616

  3. Live refresh quota (per worker or all):
     python scripts/agy_remote.py --refresh-quota
     python scripts/agy_remote.py --refresh-quota --account Nurul@eternalgy.me

  4. Switch active worker:
     python scripts/agy_remote.py --switch leanhhu0616
     python scripts/agy_remote.py --switch-next

  5. Call a specific worker with a prompt:
     python scripts/agy_remote.py --account leanhhu0616 -p "Explain transformers"

  6. ACP Stdio Bridge (for Zed, JetBrains, or any ACP editor):
     python scripts/agy_remote.py --account leanhhu0616 --stdio

Environment Variables (optional shortcuts):
  AGY_REMOTE_URL    - e.g. https://my-agy.up.railway.app
  AGY_REMOTE_TOKEN  - e.g. vfr7106
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _get_http_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    if url.startswith("wss://"):
        return "https://" + url[6:]
    if url.startswith("ws://"):
        return "http://" + url[5:]
    if not url.startswith("http://") and not url.startswith("https://"):
        return "https://" + url
    return url


def _get_ws_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    if url.startswith("https://"):
        return "wss://" + url[8:]
    if url.startswith("http://"):
        return "ws://" + url[7:]
    if not url.startswith("ws://") and not url.startswith("wss://"):
        return "wss://" + url
    return url


def http_request(base_url: str, path: str, token: str, data: dict | None = None) -> dict:
    url = _get_http_url(base_url) + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            err_json = json.loads(raw)
            msg = err_json.get("error") or err_json.get("detail") or raw
        except Exception:
            msg = raw or str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {msg}")
    except Exception as exc:
        raise RuntimeError(f"Connection failed: {exc}")


async def run_acp_bridge(raw_url: str, token: str, account: str | None = None):
    """Bridge local stdio to cloud ACP WebSocket."""
    try:
        import websockets
    except ImportError:
        sys.stderr.write(
            "Error: 'websockets' package is required for ACP bridge mode.\n"
            "Install it via: pip install websockets\n"
        )
        sys.exit(1)

    ws_base = _get_ws_url(raw_url)
    ws_endpoint = f"{ws_base}/api/acp/ws?token={token}"
    if account:
        ws_endpoint += f"&account={urllib.parse.quote(account)}"

    target_desc = f"account [{account}]" if account else "active account"
    sys.stderr.write(f"[agy_remote] Connecting to cloud ACP bridge ({target_desc}) at {ws_base}...\n")

    try:
        async with websockets.connect(ws_endpoint) as ws:
            sys.stderr.write("[agy_remote] ACP connected! Bridging stdio.\n")

            loop = asyncio.get_running_loop()

            async def stdin_to_ws():
                while True:
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        break
                    await ws.send(line.strip())

            async def ws_to_stdout():
                async for message in ws:
                    sys.stdout.write(message + "\n")
                    sys.stdout.flush()

            tasks = [asyncio.create_task(stdin_to_ws()), asyncio.create_task(ws_to_stdout())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
    except Exception as exc:
        sys.stderr.write(f"[agy_remote] ACP connection error: {exc}\n")
        sys.exit(1)


def print_accounts_table(data: dict):
    active = data.get("active")
    print(f"Active Worker  : {active or 'none'}")
    print(f"agy CLI Version: {data.get('agy_version', 'unknown')}")
    print(f"Switch Mode    : {data.get('switch_mode', 'unknown')}")
    print("\nConnected Workers (Accounts Pool):")
    header = f"  {'Worker Name':<24} {'State':<9} {'Health':<9} {'Short Quota':<13} {'Weekly Quota':<13} {'Email'}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, acct in data.get("accounts", {}).items():
        is_active = (name == active)
        active_prefix = "* " if is_active else "  "
        status = "active" if is_active else acct.get("status", "standby")
        health = acct.get("health_status", "-")
        windows = acct.get("usage_windows", {})
        short_val = (windows.get("short") or {}).get("value")
        weekly_val = (windows.get("weekly") or {}).get("value")
        short_str = f"{short_val}%" if short_val is not None else (f"{acct.get('usage_value')}%" if acct.get('usage_value') is not None else "-")
        weekly_str = f"{weekly_val}%" if weekly_val is not None else "-"
        ident = (acct.get("identity") or {}).get("email") or (acct.get("identity") or {}).get("account_name") or "-"
        name_display = (active_prefix + name)[:24]
        print(f"  {name_display:<24} {status:<9} {health:<9} {short_str:<13} {weekly_str:<13} {ident}")


def print_models(data: dict):
    acct = data.get("account", "active")
    models = data.get("models", [])
    print(f"\nAvailable Models for Worker [{acct}]:")
    print(f"  {'Model ID':<28} {'Display Name'}")
    print(f"  {'-'*28} {'-'*30}")
    for m in models:
        raw_name = m.get("name", "")
        if "Fetching available models" in raw_name or not raw_name.strip():
            continue
        parts = raw_name.split("\t")
        model_id = parts[0].strip()
        display = parts[1].strip() if len(parts) > 1 else m.get("family", "")
        print(f"  {model_id:<28} {display}")


def refresh_account_quota(base_url: str, token: str, account_name: str) -> dict:
    enc_name = urllib.parse.quote(account_name)
    res = http_request(base_url, f"/api/accounts/{enc_name}/refresh-usage", token, data={})
    print(f"Refreshed Quota for Worker [{account_name}]:")
    print(f"  Project ID   : {res.get('project_id', 'unknown')}")
    print(f"  Short Quota  : {res.get('short_usage_value')}% ({res.get('short_usage_status')})")
    print(f"  Weekly Quota : {res.get('weekly_usage_value')}% ({res.get('weekly_usage_status')})")
    print(f"  Short Reset  : {res.get('short_reset_at', '-')}")
    print(f"  Weekly Reset : {res.get('weekly_reset_at', '-')}")
    return res


def main():
    parser = argparse.ArgumentParser(description="Remote client for agy-web on Railway")
    parser.add_argument("--url", default=os.environ.get("AGY_REMOTE_URL"), help="Base URL of agy-web service")
    parser.add_argument("--token", default=os.environ.get("AGY_REMOTE_TOKEN"), help="AGY_WEB_TOKEN secret")
    parser.add_argument("-a", "--account", help="Specific worker/account name to query or run as")
    parser.add_argument("-p", "--prompt", help="Execute a prompt headlessly on the cloud agy")
    parser.add_argument("--accounts", "--list", "-l", action="store_true", help="List all available workers and quotas")
    parser.add_argument("--status", action="store_true", help="Alias for --accounts")
    parser.add_argument("--models", action="store_true", help="List available models for the worker (or active)")
    parser.add_argument("--refresh-quota", action="store_true", help="Live refresh quota from Google servers for worker(s)")
    parser.add_argument("--switch", help="Switch the active worker to the specified account name")
    parser.add_argument("--switch-next", action="store_true", help="Rotate to the next eligible worker")
    parser.add_argument("--version", action="store_true", help="Print remote agy version")
    parser.add_argument("--stdio", action="store_true", help="Run in ACP stdio bridge mode (default if no other flag)")

    args = parser.parse_args()

    if not args.url:
        sys.stderr.write("Error: --url (or AGY_REMOTE_URL env var) is required.\n")
        sys.exit(1)
    if not args.token:
        sys.stderr.write("Error: --token (or AGY_REMOTE_TOKEN env var) is required.\n")
        sys.exit(1)

    if args.version:
        res = http_request(args.url, "/api/agy/version", args.token)
        print("Remote agy version:", res.get("version", "unknown"))
        return

    if args.accounts or args.status:
        res = http_request(args.url, "/api/status", args.token)
        print_accounts_table(res)
        return

    if args.switch:
        res = http_request(args.url, "/api/switch", args.token, data={"name": args.switch})
        print(f"Switched active worker to: {res.get('active', args.switch)}")
        return

    if args.switch_next:
        res = http_request(args.url, "/api/switch-next", args.token, data={})
        print(f"Rotated active worker to: {res.get('active')}")
        return

    if args.models:
        path = f"/api/models?name={urllib.parse.quote(args.account)}" if args.account else "/api/models"
        res = http_request(args.url, path, args.token)
        print_models(res)
        return

    if args.refresh_quota:
        if args.account:
            refresh_account_quota(args.url, args.token, args.account)
        else:
            status_data = http_request(args.url, "/api/status", args.token)
            accounts = list(status_data.get("accounts", {}).keys())
            if not accounts:
                print("No accounts in pool to refresh.")
                return
            for acct_name in accounts:
                refresh_account_quota(args.url, args.token, acct_name)
                print()
            updated = http_request(args.url, "/api/status", args.token)
            print_accounts_table(updated)
        return

    if args.prompt:
        payload = {"prompt": args.prompt}
        if args.account:
            payload["account"] = args.account
        worker_target = args.account or "active worker"
        print(f"[cloud agy: running prompt on {worker_target}...]")
        # Async job: a blocking /api/prompt is cut with HTTP 502 after ~5 min by the proxy.
        try:
            job = http_request(args.url, "/api/jobs", args.token, data=payload)
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc) and "HTTP 405" not in str(exc):
                raise
            job = None  # older hub without /api/jobs
        if job is None:
            res = http_request(args.url, "/api/prompt", args.token, data=payload)
        else:
            import time
            res = job
            while res.get("status") not in ("done", "failed"):
                res = http_request(args.url, f"/api/jobs/{job['job_id']}?wait=20", args.token)
                if res.get("status") not in ("done", "failed"):
                    time.sleep(0.2)
        if res.get("ok"):
            print(res.get("answer", ""))
        else:
            sys.stderr.write(f"Error ({res.get('account')}): {res.get('error')}\n")
            sys.exit(res.get("exit", 1))
        return

    # Default mode: ACP Stdio Bridge
    asyncio.run(run_acp_bridge(args.url, args.token, args.account))


if __name__ == "__main__":
    main()
