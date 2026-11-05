"use strict";

const el = (id) => document.getElementById(id);
const TOKEN_KEY = "agyweb_token";

let currentSid = null;
let loginOffset = 0;
let loginTimer = null;
let lastStatus = null;

// --- token gate -------------------------------------------------------------
const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);

function showGate(msg) {
  el("app").classList.add("hidden");
  el("gate").classList.remove("hidden");
  if (msg) {
    el("gate-error").textContent = msg;
    el("gate-error").classList.remove("hidden");
  }
}
function showApp() {
  el("gate").classList.add("hidden");
  el("app").classList.remove("hidden");
}

// --- api --------------------------------------------------------------------
async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const tok = getToken();
  if (tok) headers["Authorization"] = "Bearer " + tok;
  let res;
  try {
    res = await fetch(path, Object.assign({}, opts, { headers }));
  } catch (e) {
    setConn("unreachable");
    throw new Error("network: " + e.message);
  }
  if (res.status === 401) { showGate("Token rejected by server."); throw new Error("401"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || res.statusText || ("HTTP " + res.status));
  setConn("ok");
  return data;
}

function setConn(state) {
  const c = el("hdr-conn");
  if (!c) return;
  c.textContent = state === "ok" ? "● connected" : "● " + state;
  c.style.color = state === "ok" ? "var(--green)" : "var(--red)";
}

let toastTimer = null;
function toast(msg, kind = "") {
  const t = el("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

// --- time helpers -----------------------------------------------------------
function ago(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return String(iso);
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}
function until(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return String(iso);
  const s = Math.round((t - Date.now()) / 1000);
  if (s <= 0) return "now";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}
function inCooldown(acct) {
  return acct.cooldown_until && new Date(acct.cooldown_until).getTime() > Date.now();
}

// --- status / accounts ------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    lastStatus = s;
    renderHeader(s);
    renderAccounts(s);
    renderPromptAccounts(s);
    renderSettings(s);
    renderServer(s);
  } catch (e) {
    if (e.message !== "401") toast(e.message, "err");
  }
}

function renderHeader(s) {
  el("hdr-version").textContent = s.agy_version ? ("v" + s.agy_version) : "—";
  el("hdr-active").textContent = s.active || "none";
  el("hdr-mode").textContent = s.switch_mode || "—";
  const lw = s.log_watch || {};
  el("hdr-watch").textContent = "log-watch: " + (lw.state || lw.status || (lw.watching ? "on" : "idle"));
}

function stateBadge(name, acct, active) {
  if (acct.enabled === false) return ["disabled", "b-disabled"];
  if (inCooldown(acct)) return ["cooldown", "b-cooldown"];
  if (name === active) return ["active", "b-active"];
  if (acct.health_status && /bad|failed|exhaust/i.test(acct.health_status)) return [acct.health_status, "b-bad"];
  return [acct.status || "standby", "b-standby"];
}

function renderAccounts(s) {
  const tbody = el("accounts-table").querySelector("tbody");
  tbody.innerHTML = "";
  const names = Object.keys(s.accounts || {});
  el("accounts-empty").classList.toggle("hidden", names.length > 0);
  for (const name of names) {
    const a = s.accounts[name];
    const [label, cls] = stateBadge(name, a, s.active);
    const short = (a.usage_windows && a.usage_windows.short) || {};
    const quota = short.value != null ? short.value + "%" : (a.usage_value != null ? a.usage_value + "%" : "—");
    const resetIso = short.reset_at || a.reset_at;
    const ident = (a.identity && (a.identity.email || a.identity.account_name)) || "";

    const tr = document.createElement("tr");
    if (name === s.active) tr.className = "active-row";
    tr.appendChild(td(name + (name === s.active ? " ★" : "")));
    tr.appendChild(tdBadge(label, cls));
    tr.appendChild(td(a.health_status || "—"));
    tr.appendChild(td(quota + (a.usage_status && a.usage_status !== "unknown" ? " (" + a.usage_status + ")" : "")));
    tr.appendChild(td(until(resetIso)));
    tr.appendChild(td(ident));
    tr.appendChild(td(ago(a.last_live_check_at)));

    const acts = document.createElement("td");
    const box = document.createElement("div");
    box.className = "acts";
    if (name !== s.active) box.appendChild(btn("activate", () => act("/api/switch", { name }, "switched to " + name)));
    box.appendChild(btn(a.enabled === false ? "enable" : "disable",
      () => api(`/api/accounts/${enc(name)}/enable`, { method: "POST", body: JSON.stringify({ enabled: a.enabled === false }) })
        .then(() => { toast((a.enabled === false ? "enabled " : "disabled ") + name); loadStatus(); })
        .catch((e) => toast(e.message, "err"))));
    box.appendChild(btn("refresh", () => act(`/api/accounts/${enc(name)}/refresh-usage`, {}, "refreshed " + name, "POST")));
    if (inCooldown(a) || /bad|failed/i.test(a.health_status || ""))
      box.appendChild(btn("clear", () => act(`/api/accounts/${enc(name)}/clear-bad`, {}, "cleared " + name, "POST")));
    else
      box.appendChild(btn("mark bad", () => act(`/api/accounts/${enc(name)}/mark-bad`, { reason: "manual", cooldown_minutes: 60 }, "marked bad", "POST")));
    box.appendChild(btn("delete", () => {
      if (!confirm(`Delete account "${name}"? Its saved login is gone for good.`)) return;
      api(`/api/accounts/${enc(name)}`, { method: "DELETE" }).then(() => { toast("deleted " + name); loadStatus(); }).catch((e) => toast(e.message, "err"));
    }, "danger"));
    acts.appendChild(box);
    tr.appendChild(acts);
    tbody.appendChild(tr);
  }
}

const enc = encodeURIComponent;
function td(text) { const c = document.createElement("td"); c.textContent = text == null ? "—" : String(text); return c; }
function tdBadge(text, cls) { const c = document.createElement("td"); const b = document.createElement("span"); b.className = "badge " + cls; b.textContent = text; c.appendChild(b); return c; }
function btn(label, onClick, extraCls = "") { const b = document.createElement("button"); b.textContent = label; if (extraCls) b.className = extraCls; b.addEventListener("click", onClick); return b; }

async function act(path, body, okMsg, method = "POST") {
  try { await api(path, { method, body: JSON.stringify(body) }); toast(okMsg || "ok"); loadStatus(); }
  catch (e) { toast(e.message, "err"); }
}

function renderPromptAccounts(s) {
  const sel = el("prompt-account");
  const prev = sel.value;
  sel.innerHTML = "";
  const optActive = document.createElement("option");
  optActive.value = "active"; optActive.textContent = "(active" + (s.active ? ": " + s.active : "") + ")";
  sel.appendChild(optActive);
  for (const name of Object.keys(s.accounts || {})) {
    const o = document.createElement("option"); o.value = name; o.textContent = name; sel.appendChild(o);
  }
  if (prev) sel.value = prev;
}

function renderSettings(s) {
  if (s.switch_mode) el("set-mode").value = s.switch_mode;
  const p = s.switch_policy || {};
  if (p.short_usage_threshold_percent != null) el("set-threshold").value = p.short_usage_threshold_percent;
  if (p.refresh_failure_threshold != null) el("set-failthresh").value = p.refresh_failure_threshold;
  if (p.candidate_strategy) el("set-strategy").value = p.candidate_strategy;
  if (s.agy_version) el("cli-version").textContent = s.agy_version;

  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsInput = el("acp-ws-url");
  if (wsInput) wsInput.value = `${wsProto}//${location.host}/api/acp/ws?token=${getToken()}`;
}

async function updateCli() {
  const out = el("cli-update-out");
  out.classList.remove("hidden");
  out.textContent = "Checking / updating agy CLI…";
  try {
    const r = await api("/api/agy/update", { method: "POST" });
    out.textContent = `[exit ${r.exit}] version: ${r.version}\n\n${r.output || ""}`;
    toast(r.ok ? `agy is up to date (${r.version})` : "update finished with warnings");
    loadStatus();
  } catch (e) {
    out.textContent = "ERROR: " + e.message;
    toast(e.message, "err");
  }
}

function renderServer(s) {
  el("server-out").textContent = JSON.stringify({
    root: s.root, live_dir: s.live_dir, agy_binary: s.agy_binary,
    agy_resolved: s.agy_resolved, switch_runtime: s.switch_runtime,
  }, null, 2);
}

// --- login (pty stream) -----------------------------------------------------
async function startLogin() {
  const name = el("login-name").value.trim();
  if (!name) return toast("enter an account label", "err");
  el("login-out").textContent = "";
  loginOffset = 0;
  el("login-result-banner").className = "login-result hidden";
  el("login-result-banner").textContent = "";
  el("step-2-box").classList.add("hidden");
  el("step-3-box").classList.add("hidden");
  el("login-input").value = "";
  el("btn-open-auth-link").href = "#";
  el("btn-login-kill").classList.remove("hidden");
  try {
    const s = await api("/api/login", { method: "POST", body: JSON.stringify({ name }) });
    currentSid = s.id;
    el("login-out").textContent = "[started cloud login session " + s.id + "]\n";
    pollLogin();
  } catch (e) { toast(e.message, "err"); }
}

function pollLogin() {
  clearTimeout(loginTimer);
  loginTimer = setTimeout(async () => {
    if (!currentSid) return;
    try {
      const r = await api(`/api/login/${currentSid}?offset=${loginOffset}`);
      if (r.data) {
        const out = el("login-out");
        out.textContent += r.data;
        out.scrollTop = out.scrollHeight;
      }
      loginOffset = r.offset;

      // When the cloud process outputs the OAuth URL, display Step 2 and Step 3
      if (r.auth_url && el("step-2-box").classList.contains("hidden")) {
        el("btn-open-auth-link").href = r.auth_url;
        el("step-2-box").classList.remove("hidden");
        el("step-3-box").classList.remove("hidden");
        el("login-input").focus();
        toast("Auth link received from cloud! Open it to sign in.", "ok");
      }

      if (r.running) { pollLogin(); return; }

      // Finished
      currentSid = null;
      el("btn-login-kill").classList.add("hidden");
      el("step-2-box").classList.add("hidden");
      el("step-3-box").classList.add("hidden");

      const banner = el("login-result-banner");
      banner.classList.remove("hidden");
      if (r.result && r.result.ok) {
        banner.className = "login-result ok";
        banner.textContent = `✓ Successfully authenticated as "${r.result.account}"! The account is now active on your cloud hub.`;
        toast(`Account "${r.result.account}" ready!`, "ok");
      } else {
        banner.className = "login-result err";
        const detail = (r.result && r.result.detail) || ("Process exited with code " + r.exit_code);
        banner.textContent = `❌ Login incomplete or failed: ${detail}`;
        toast("Login failed", "err");
      }
      loadStatus();
    } catch (e) { currentSid = null; }
  }, 1000);
}

async function sendLoginInput() {
  if (!currentSid) return toast("no active login session", "err");
  const text = el("login-input").value.trim();
  if (!text) return toast("paste the authorization code or URL first", "err");
  try {
    el("login-out").textContent += "\n> [Sending authorization code to cloud...]\n";
    await api(`/api/login/${currentSid}/input`, { method: "POST", body: JSON.stringify({ text, newline: true }) });
    el("login-input").value = "";
    toast("Code submitted to cloud! Finalizing login...", "ok");
  } catch (e) { toast(e.message, "err"); }
}

async function killLogin() {
  if (!currentSid) return;
  try { await api(`/api/login/${currentSid}/kill`, { method: "POST" }); } catch (e) {}
  currentSid = null;
  el("btn-login-kill").classList.add("hidden");
  el("step-2-box").classList.add("hidden");
  el("step-3-box").classList.add("hidden");
  toast("Login session cancelled");
}

// --- prompt -----------------------------------------------------------------
async function runPrompt() {
  const prompt = el("prompt-text").value;
  if (!prompt.trim()) return toast("enter a prompt", "err");
  const account = el("prompt-account").value;
  const timeout = parseInt(el("prompt-timeout").value || "180", 10);
  const body = {
    prompt,
    timeout,
    tools: el("prompt-tools").checked,
    sandbox: el("prompt-sandbox").checked,
  };
  if (account && account !== "active") body.account = account;
  const model = el("prompt-model").value.trim();
  if (model) body.model = model;
  const cwd = el("prompt-cwd").value.trim();
  if (cwd) body.cwd = cwd;
  el("prompt-out").textContent = "running…";
  el("prompt-meta").textContent = "";
  try {
    const r = await api("/api/prompt", { method: "POST", body: JSON.stringify(body) });
    if (r.ok) {
      el("prompt-out").textContent = r.answer || "(empty response)";
      el("prompt-meta").textContent = `${r.account} · ${r.ms}ms · exit ${r.exit}`;
    } else {
      el("prompt-out").textContent = "ERROR: " + (r.error || "failed") +
        (r.auth_failure ? "\n\n(auth failure — re-login or switch account)" : "");
      el("prompt-meta").textContent = r.account || "";
    }
  } catch (e) { el("prompt-out").textContent = "ERROR: " + e.message; }
}

// --- settings actions -------------------------------------------------------
async function loadModels() {
  el("models-out").textContent = "loading…";
  try {
    const m = await api("/api/models");
    el("models-out").textContent = JSON.stringify(m, null, 2);
  } catch (e) { el("models-out").textContent = "ERROR: " + e.message; }
}

// --- tabs -------------------------------------------------------------------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  for (const p of ["accounts", "add", "prompt", "settings"])
    el("tab-" + p).classList.toggle("hidden", p !== name);
}

// --- init -------------------------------------------------------------------
function wire() {
  el("gate-form").addEventListener("submit", (e) => {
    e.preventDefault();
    setToken(el("gate-token").value.trim());
    el("gate-token").value = "";
    api("/api/status").then(() => { showApp(); loadStatus(); }).catch((err) => showGate(err.message));
  });
  el("btn-logout").addEventListener("click", () => { localStorage.removeItem(TOKEN_KEY); showGate(); });

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  el("btn-refresh").addEventListener("click", loadStatus);
  el("btn-switch-next").addEventListener("click", () => act("/api/switch-next", {}, "switched"));
  el("btn-ensure").addEventListener("click", () => act("/api/ensure-active", { force: false }, "ensured"));

  el("btn-login-start").addEventListener("click", startLogin);
  el("btn-login-send").addEventListener("click", sendLoginInput);
  el("btn-login-kill").addEventListener("click", killLogin);
  el("login-input").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendLoginInput(); } });

  el("btn-copy-auth-link").addEventListener("click", () => {
    const link = el("btn-open-auth-link").href;
    if (!link || link === "#") return toast("no link yet", "err");
    navigator.clipboard.writeText(link)
      .then(() => toast("Auth link copied to clipboard!", "ok"))
      .catch(() => toast("Failed to copy link", "err"));
  });

  el("btn-toggle-terminal").addEventListener("click", () => {
    el("login-out").classList.toggle("hidden");
  });

  el("btn-prompt-run").addEventListener("click", runPrompt);

  el("btn-set-mode").addEventListener("click", () => act("/api/switch-mode", { mode: el("set-mode").value }, "mode set"));
  el("btn-set-policy").addEventListener("click", () => act("/api/switch-policy", {
    short_usage_threshold_percent: parseFloat(el("set-threshold").value),
    refresh_failure_threshold: parseInt(el("set-failthresh").value, 10),
    candidate_strategy: el("set-strategy").value,
  }, "policy saved"));
  el("btn-models").addEventListener("click", loadModels);
  el("btn-update-cli").addEventListener("click", updateCli);
  el("btn-copy-ws").addEventListener("click", () => {
    const input = el("acp-ws-url");
    if (!input) return;
    navigator.clipboard.writeText(input.value)
      .then(() => toast("WebSocket URL copied!", "ok"))
      .catch(() => {
        input.select();
        document.execCommand("copy");
        toast("WebSocket URL copied!", "ok");
      });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  if (getToken()) {
    api("/api/status").then(() => { showApp(); loadStatus(); setInterval(loadStatus, 15000); })
      .catch(() => showGate());
  } else {
    showGate();
  }
});
