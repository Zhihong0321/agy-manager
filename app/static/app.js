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
  el("btn-login-kill").classList.remove("hidden");
  el("login-input-row").classList.remove("hidden");
  try {
    const s = await api("/api/login", { method: "POST", body: JSON.stringify({ name }) });
    currentSid = s.id;
    el("login-out").textContent = "[started session " + s.id + "]\n";
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
      if (r.running) { pollLogin(); return; }
      currentSid = null;
      el("btn-login-kill").classList.add("hidden");
      toast("login session ended (exit " + r.exit_code + ")", r.exit_code === 0 ? "ok" : "err");
      loadStatus();
    } catch (e) { /* 401 handled in api(); otherwise stop */ currentSid = null; }
  }, 1000);
}

async function sendLoginInput() {
  if (!currentSid) return toast("no active login session", "err");
  const text = el("login-input").value;
  try {
    await api(`/api/login/${currentSid}/input`, { method: "POST", body: JSON.stringify({ text, newline: true }) });
    el("login-input").value = "";
    el("login-out").textContent += "\n> " + text + "\n";
  } catch (e) { toast(e.message, "err"); }
}

async function killLogin() {
  if (!currentSid) return;
  try { await api(`/api/login/${currentSid}/kill`, { method: "POST" }); } catch (e) {}
  currentSid = null;
  el("btn-login-kill").classList.add("hidden");
}

// --- prompt -----------------------------------------------------------------
async function runPrompt() {
  const prompt = el("prompt-text").value;
  if (!prompt.trim()) return toast("enter a prompt", "err");
  const account = el("prompt-account").value;
  const timeout = parseInt(el("prompt-timeout").value || "180", 10);
  const tools = el("prompt-tools").checked;
  const body = { prompt, timeout, tools };
  if (account && account !== "active") body.account = account;
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

  el("btn-prompt-run").addEventListener("click", runPrompt);

  el("btn-set-mode").addEventListener("click", () => act("/api/switch-mode", { mode: el("set-mode").value }, "mode set"));
  el("btn-set-policy").addEventListener("click", () => act("/api/switch-policy", {
    short_usage_threshold_percent: parseFloat(el("set-threshold").value),
    refresh_failure_threshold: parseInt(el("set-failthresh").value, 10),
    candidate_strategy: el("set-strategy").value,
  }, "policy saved"));
  el("btn-models").addEventListener("click", loadModels);
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  el("login-input-row").classList.add("hidden");
  if (getToken()) {
    api("/api/status").then(() => { showApp(); loadStatus(); setInterval(loadStatus, 15000); })
      .catch(() => showGate());
  } else {
    showGate();
  }
});
