#!/usr/bin/env python3
"""agy_remote: Remote client for agy-web cloud hub.

Connects your local machine (or editor like Zed) to your Railway-hosted
Antigravity CLI (agy), freeing your local PC's CPU and RAM.

Usage:
  1. ACP Stdio Bridge (for Zed, JetBrains, or any ACP editor):
     python scripts/agy_remote.py --url wss://your-service.up.railway.app --token <AGY_WEB_TOKEN>

  2. Direct Prompt (run a prompt on cloud agy from local CLI):
     python scripts/agy_remote.py --url https://your-service.up.railway.app --token <AGY_WEB_TOKEN> -p "List files"

  3. Cloud Status:
     python scripts/agy_remote.py --url https://your-service.up.railway.app --token <AGY_WEB_TOKEN> --status

Environment Variables (optional shortcuts):
  AGY_REMOTE_URL    - e.g. https://your-service.up.railway.app
  AGY_REMOTE_TOKEN  - your AGY_WEB_TOKEN
"""
import argparse
import asyncio
import json
import os
import sys
import urllib.error
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
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
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
        ws_endpoint += f"&account={account}"

    sys.stderr.write(f"[agy_remote] Connecting to cloud ACP bridge at {ws_base}...\n")

    try:
        async with websockets.connect(ws_endpoint) as ws:
            sys.stderr.write("[agy_remote] ACP connected! Bridging stdio.\n")

            loop = asyncio.get_running_loop()

            async def stdin_to_ws():
                # Read line from stdin in thread to avoid blocking loop
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


def main():
    parser = argparse.ArgumentParser(description="Remote client for agy-web on Railway")
    parser.add_argument("--url", default=os.environ.get("AGY_REMOTE_URL"), help="Base URL of agy-web service")
    parser.add_argument("--token", default=os.environ.get("AGY_REMOTE_TOKEN"), help="AGY_WEB_TOKEN secret")
    parser.add_argument("-p", "--prompt", help="Execute a prompt headlessly on the cloud agy")
    parser.add_argument("--account", help="Specific account name to use (default: active account)")
    parser.add_argument("--status", action="store_true", help="Print remote cloud status and accounts")
    parser.add_argument("--version", action="store_true", help="Print remote agy version")
    parser.add_argument("--stdio", action="store_true", help="Run in ACP stdio bridge mode (default if no -p/--status)")

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

    if args.status:
        res = http_request(args.url, "/api/status", args.token)
        print(f"Active Account : {res.get('active', 'none')}")
        print(f"agy CLI Version: {res.get('agy_version', 'unknown')}")
        print(f"Switch Mode    : {res.get('switch_mode', 'unknown')}")
        print("Accounts Pool  :")
        for name, acct in res.get("accounts", {}).items():
            active_mark = "*" if name == res.get("active") else " "
            status = acct.get("status", "standby")
            quota = acct.get("usage_value")
            quota_str = f"{quota}%" if quota is not None else "-"
            print(f"  {active_mark} {name:<12} status={status:<9} quota={quota_str}")
        return

    if args.prompt:
        payload = {"prompt": args.prompt}
        if args.account:
            payload["account"] = args.account
        print(f"[cloud agy: running prompt on {args.account or 'active account'}...]")
        res = http_request(args.url, "/api/prompt", args.token, data=payload)
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
