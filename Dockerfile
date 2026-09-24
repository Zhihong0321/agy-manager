# agy-web: web UI + HTTP API for multi-account Antigravity CLI (agy), wrapping
# agy-cli-manager. agy is NOT baked into the image — it installs at runtime into
# $HOME/.local/bin on the volume, because (1) its credential lands under the same
# $HOME so binary + token share one persistence story, and (2) `agy update`
# self-modifies the binary. Same reasoning agy-lab uses.
FROM python:3.12-slim

# util-linux -> `script`, which gives the interactive agy login a real pty
# tini      -> real PID 1: reaps agy/script children, forwards SIGTERM on redeploy
# curl      -> fetches the agy installer at runtime
# procps    -> ps/kill for debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates util-linux tini procps \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/data \
    PATH=/data/.local/bin:/usr/local/bin:/usr/bin:/bin \
    AGY_MANAGER_ROOT=/data/.agy-cli-manager \
    AGY_MANAGER_LIVE_DIR=/data/.gemini \
    AGY_BINARY=/data/.local/bin/agy \
    PORT=8080 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "./entrypoint.sh"]
CMD ["python", "-m", "app.server"]
