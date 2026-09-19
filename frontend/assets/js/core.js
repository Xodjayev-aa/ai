/* ══════════════════════════════════════════════════════════════════════
   Aether — core
   DOM helpers, API client, toasts, confirm dialogs, markdown, theme.
   Loaded first; everything else hangs off the global `Aether` namespace.
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

const Aether = (window.Aether = window.Aether || {});
Aether.version = "4.0.0";

/* ─────────────────────────────── DOM ─────────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
Aether.$ = $;
Aether.$$ = $$;

Aether.el = function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "value") node.value = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
};

Aether.esc = function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
};

Aether.uuid = function uuid() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const rnd = (Math.random() * 16) | 0;
    const val = ch === "x" ? rnd : (rnd & 0x3) | 0x8;
    return val.toString(16);
  });
};

Aether.sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
Aether.clamp = (value, min, max) => Math.min(max, Math.max(min, value));

Aether.store = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(`aether:${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`aether:${key}`, JSON.stringify(value)); } catch { /* full */ }
  },
  del(key) { try { localStorage.removeItem(`aether:${key}`); } catch { /* ignore */ } },
};

/* ──────────────────────────── event bus ──────────────────────────── */

const listeners = new Map();
Aether.on = (event, fn) => {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
};
Aether.emit = (event, payload) => {
  for (const fn of listeners.get(event) || []) {
    try { fn(payload); } catch (err) { console.warn(`[aether] ${event} handler`, err); }
  }
};

/* ─────────────────────────── toast system ────────────────────────── */

Aether.toast = function toast(message, opts = {}) {
  const { type = "ok", action = null, timeout = type === "err" ? 8000 : 4200 } = opts;
  const box = $("#toasts") || document.body;
  const icons = {
    ok: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    err: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.4v.1"/>',
    warn: '<path d="M12 4.5l8.5 15h-17z"/><path d="M12 10v4M12 16.8v.1"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/>',
  };
  const el = Aether.el("div", { class: `toast ${type}`, role: "status" }, [
    Aether.el("span", { class: "toast-ic", html: `<svg class="ic" viewBox="0 0 24 24">${icons[type] || icons.info}</svg>` }),
    Aether.el("span", { class: "toast-msg", text: message }),
  ]);
  if (action) {
    el.append(Aether.el("button", {
      class: "toast-action",
      text: action.label,
      onclick: () => { action.onClick?.(); dismiss(); },
    }));
  }
  el.append(Aether.el("button", {
    class: "toast-x", "aria-label": "Dismiss",
    html: '<svg class="ic" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    onclick: () => dismiss(),
  }));
  let timer = null;
  function dismiss() {
    if (timer) clearTimeout(timer);
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 260);
  }
  if (timeout) timer = setTimeout(dismiss, timeout);
  box.append(el);
  return { dismiss, element: el };
};

/* ───────────────────────────── API client ────────────────────────── */

class ApiError extends Error {
  constructor(message, { status = 0, retryAfter = 0, payload = null, browserTts = false,
                        detail = undefined } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
    this.payload = payload;
    this.browserTts = browserTts;
    // `detail` mirrors FastAPI's envelope so call sites can keep writing
    // `err.detail || "fallback"` and still surface the server's real message.
    this.detail = detail === undefined || detail === null ? message : detail;
    this.retryable = status === 0 || status === 408 || status === 429 || status >= 500;
  }
}
Aether.ApiError = ApiError;

const TOKEN_KEY = "aether_token";
const RETURN_HASH_KEY = "aether_return_hash";

/* Read a JWT payload without verifying it (the server verifies; we only need
   the email claim to prefill the form after a storage wipe). */
Aether.decodeJwt = function decodeJwt(token) {
  try {
    const part = String(token || "").split(".")[1];
    if (!part) return null;
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch { return null; }
};

Aether.emailFromToken = function emailFromToken(token) {
  const payload = Aether.decodeJwt(token);
  return payload && typeof payload.sub === "string" ? payload.sub : "";
};

Aether.session = {
  get token() { return localStorage.getItem(TOKEN_KEY) || ""; },
  set token(value) {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  },
  get email() { return localStorage.getItem("aether_email") || ""; },
  set email(value) { localStorage.setItem("aether_email", value || ""); },
  clear() { this.token = ""; this.email = ""; },

  /* Signing out (or being signed out) must not lose the open conversation:
     its identity lives in location.hash, so stash it before showing the login
     screen and put it back after the next successful sign-in. */
  stashHash() {
    try {
      if (location.hash) sessionStorage.setItem(RETURN_HASH_KEY, location.hash);
    } catch { /* private mode */ }
    return location.hash;
  },
  restoreHash() {
    try {
      const hash = sessionStorage.getItem(RETURN_HASH_KEY);
      if (!hash) return "";
      sessionStorage.removeItem(RETURN_HASH_KEY);
      if (location.hash !== hash) location.hash = hash;
      Aether.emit("hash:restored", hash);
      return hash;
    } catch { return ""; }
  },
};

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = Aether.session.token;
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function parseError(resp) {
  let payload = null;
  try { payload = await resp.json(); } catch { /* not JSON */ }
  let detail = payload?.detail;
  let retryAfter = 0;
  let browserTts = false;
  if (detail && typeof detail === "object") {
    browserTts = Boolean(detail.browser_tts);
    retryAfter = Number(detail.retry_after) || 0;
    detail = detail.message || detail.detail || "Request failed";
  }
  if (!detail) {
    detail = resp.status === 429
      ? "Too many requests right now — give it a moment."
      : `Request failed (${resp.status})`;
  }
  const headerRetry = Number(resp.headers.get("retry-after")) || 0;
  const message = String(detail);
  return new ApiError(message, {
    status: resp.status,
    retryAfter: retryAfter || headerRetry,
    payload,
    browserTts,
    detail: message,
  });
}

Aether.api = async function api(path, opts = {}) {
  const { method = "GET", body, form, headers = {}, signal, timeout = 120000 } = opts;
  const controller = new AbortController();
  const onAbort = () => controller.abort(signal?.reason);
  if (signal) signal.addEventListener("abort", onAbort, { once: true });
  const timer = timeout ? setTimeout(() => controller.abort(new Error("timeout")), timeout) : null;
  try {
    const init = { method, headers: authHeaders(headers), signal: controller.signal };
    if (form) init.body = form;
    else if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
    const resp = await fetch(path, init);
    if (resp.status === 401 && !path.startsWith("/api/auth/")) {
      // Carry the server's detail so the shell can tell "the account is gone"
      // (storage was reset) apart from "this token is no longer valid".
      const expired = await parseError(resp);
      Aether.emit("session:expired", expired);
      throw expired;
    }
    if (!resp.ok) throw await parseError(resp);
    if (opts.raw) return resp;
    const type = resp.headers.get("content-type") || "";
    return type.includes("application/json") ? resp.json() : resp.text();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err?.name === "AbortError") {
      if (signal?.aborted) throw err;               // caller cancelled — quiet
      throw new ApiError("The request took too long. Free shared AI can be slow — try again.", { status: 408 });
    }
    throw new ApiError("You appear to be offline (or the server is unreachable).", { status: 0 });
  } finally {
    if (timer) clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", onAbort);
  }
};

/* Retry-once, for the few calls that must not be given up on too easily:
   a cold-start boot check or the first sign-in after a deploy. Only retries
   errors that can actually change (network, 408, 429, 5xx — including the
   server's "Warming up" 503); a 401/400 is final. */
Aether.apiRetry = async function apiRetry(path, opts = {}, { attempts = 2, delay = 250 } = {}) {
  let last = null;
  for (let attempt = 1; attempt <= Math.max(1, attempts); attempt += 1) {
    try {
      return await Aether.api(path, opts);
    } catch (err) {
      last = err;
      const retryable = err instanceof ApiError && err.retryable;
      if (!retryable || attempt >= attempts) throw err;
      await Aether.sleep(delay);
    }
  }
  throw last;
};

/* SSE streaming helper: parses data: frames, ignores keep-alive comments. */
Aether.stream = async function stream(path, body, { onEvent, signal } = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) throw await parseError(resp);
  if (!resp.body) throw new ApiError("Streaming is not supported in this browser.", { status: 0 });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const payload = frame.split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("");
        if (!payload || payload === "[DONE]") continue;
        try { onEvent?.(JSON.parse(payload)); }
        catch (err) { console.warn("[aether] bad SSE frame", payload.slice(0, 120), err); }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
};

/* ───────────────────────────── dialogs ───────────────────────────── */

Aether.confirm = function confirm({ title = "Are you sure?", message = "", okLabel = "Confirm",
                                    cancelLabel = "Cancel", danger = false } = {}) {
  return new Promise((resolve) => {
    const wrap = Aether.el("div", { class: "modal soft" }, [
      Aether.el("div", { class: "modal-inner dialog" }, [
        Aether.el("h3", { text: title }),
        message ? Aether.el("p", { class: "muted", text: message }) : null,
        Aether.el("div", { class: "dialog-actions" }, [
          Aether.el("button", { class: "btn ghost", text: cancelLabel, onclick: () => close(false) }),
          Aether.el("button", {
            class: `btn ${danger ? "danger" : "primary"}`, text: okLabel,
            onclick: () => close(true),
          }),
        ]),
      ]),
    ]);
    function close(value) {
      wrap.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    }
    function onKey(event) {
      if (event.key === "Escape") close(false);
      if (event.key === "Enter") close(true);
    }
    document.addEventListener("keydown", onKey);
    wrap.addEventListener("click", (event) => { if (event.target === wrap) close(false); });
    document.body.append(wrap);
    wrap.querySelector(".btn.primary, .btn.danger")?.focus();
  });
};

Aether.prompt = function prompt({ title, label = "", value = "", placeholder = "",
                                 multiline = false, okLabel = "Save" } = {}) {
  return new Promise((resolve) => {
    const input = multiline
      ? Aether.el("textarea", { rows: 3, placeholder, value })
      : Aether.el("input", { type: "text", placeholder, value });
    const wrap = Aether.el("div", { class: "modal soft" }, [
      Aether.el("div", { class: "modal-inner dialog" }, [
        Aether.el("h3", { text: title }),
        label ? Aether.el("p", { class: "muted small-text", text: label }) : null,
        input,
        Aether.el("div", { class: "dialog-actions" }, [
          Aether.el("button", { class: "btn ghost", text: "Cancel", onclick: () => close(null) }),
          Aether.el("button", { class: "btn primary", text: okLabel, onclick: () => close(input.value) }),
        ]),
      ]),
    ]);
    function close(value) {
      wrap.remove();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    }
    function onKey(event) {
      if (event.key === "Escape") close(null);
      if (event.key === "Enter" && !multiline) close(input.value);
    }
    document.addEventListener("keydown", onKey);
    document.body.append(wrap);
    input.focus();
    input.setSelectionRange?.(input.value.length, input.value.length);
  });
};

/* ───────────────────────────── markdown ──────────────────────────── */

const codeStore = [];

function inlineFormat(text) {
  return text
    .replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}

function renderTable(rows) {
  const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(1).map(cells);
  return `<div class="table-wrap"><table><thead><tr>${
    head.map((h) => `<th>${h}</th>`).join("")
  }</tr></thead><tbody>${
    body.map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")
  }</tbody></table></div>`;
}

Aether.markdown = function markdown(source) {
  if (!source) return "";
  codeStore.length = 0;
  let text = String(source).replace(/\r\n?/g, "\n");

  // 1. fenced code first, so nothing inside it is re-formatted
  text = text.replace(/```([\w+#.-]*)[ \t]*\n?([\s\S]*?)```/g, (_m, lang, code) => {
    codeStore.push({ lang: (lang || "text").toLowerCase(), code: code.replace(/\n$/, "") });
    return `\u0000C${codeStore.length - 1}\u0000`;
  });

  // 2. escape everything that is left
  text = Aether.esc(text);

  // 3. block level
  const lines = text.split("\n");
  const out = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) {
      out.push(`<p>${paragraph.map(inlineFormat).join("<br>")}</p>`);
      paragraph = [];
    }
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\u0000C\d+\u0000$/.test(line.trim())) { flush(); out.push(line.trim()); continue; }
    if (!line.trim()) { flush(); continue; }
    let match = /^(#{1,6})\s+(.*)$/.exec(line);
    if (match) {
      flush();
      const level = Math.min(6, match[1].length + 1);
      out.push(`<h${level}>${inlineFormat(match[2])}</h${level}>`);
      continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flush(); out.push("<hr>"); continue; }
    if (/^\s*>\s?/.test(line)) {
      flush();
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      i -= 1;
      out.push(`<blockquote>${inlineFormat(quote.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) rows.push(lines[i].trim());
        i += 1;
      }
      i -= 1;
      if (rows.length) out.push(renderTable(rows));
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || ordered) {
      flush();
      const tag = bullet ? "ul" : "ol";
      const items = [];
      while (i < lines.length) {
        const current = lines[i];
        const m = bullet ? /^\s*[-*+]\s+(.*)$/.exec(current)
                         : /^\s*\d+[.)]\s+(.*)$/.exec(current);
        if (!m) break;
        const task = /^\[( |x|X)\]\s+(.*)$/.exec(m[1]);
        if (task) {
          const checked = task[1].toLowerCase() === "x";
          items.push(`<li class="task${checked ? " done" : ""}">`
            + `<span class="task-box">${checked ? "✓" : ""}</span>`
            + `${inlineFormat(task[2])}</li>`);
        } else {
          items.push(`<li>${inlineFormat(m[1])}</li>`);
        }
        i += 1;
      }
      i -= 1;
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }
    paragraph.push(line);
  }
  flush();
  text = out.join("\n");

  // 4. restore code blocks
  text = text.replace(/\u0000C(\d+)\u0000/g, (_m, index) => {
    const block = codeStore[Number(index)];
    if (!block) return "";
    return `<div class="code-card" data-code-idx="${index}">`
      + `<div class="code-bar"><span class="code-lang">${Aether.esc(block.lang)}</span>`
      + `<button class="code-copy" type="button" data-code-idx="${index}">Copy</button></div>`
      + `<pre><code>${Aether.esc(block.code)}</code></pre></div>`;
  });
  return text;
};

Aether.markdownCode = (index) => codeStore[index]?.code ?? "";

Aether.stripMarkdown = function stripMarkdown(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " code block ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_>|#~]/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
};

/* ─────────────────────────── small helpers ───────────────────────── */

Aether.copy = async function copy(text, message = "Copied to clipboard") {
  try {
    await navigator.clipboard.writeText(text);
    Aether.toast(message);
    return true;
  } catch {
    const area = Aether.el("textarea", { value: text, class: "sr-only" });
    document.body.append(area);
    area.select();
    const ok = document.execCommand?.("copy");
    area.remove();
    Aether.toast(ok ? message : "Could not copy — select the text manually",
                 { type: ok ? "ok" : "err" });
    return Boolean(ok);
  }
};

Aether.download = function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = Aether.el("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
};

Aether.timeAgo = function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(then).toLocaleDateString();
};

Aether.formatCountdown = (seconds) => {
  const value = Math.max(0, Math.ceil(seconds));
  if (value < 60) return `${value}s`;
  const mins = Math.floor(value / 60);
  return `${mins}m ${String(value % 60).padStart(2, "0")}s`;
};

Aether.clockTime = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/* ───────────────────────────── theming ───────────────────────────── */

Aether.theme = {
  get() { return document.documentElement.dataset.theme || "dark"; },
  apply(theme) {
    const next = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    Aether.store.set("theme", next);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", next === "light" ? "#f7f7f9" : "#1e1e21");
    Aether.emit("theme:changed", next);
  },
  toggle() {
    const next = this.get() === "dark" ? "light" : "dark";
    this.apply(next);
    return next;
  },
  init() {
    const saved = Aether.store.get("theme", "dark");
    this.apply(saved);
  },
};

/* ─────────────────────────── icons ──────────────────────────────── */

Aether.icon = function icon(name, size = 16) {
  const paths = {
    logo: '<path d="M12 3c.9 4.6 4.4 8.1 9 9-4.6.9-8.1 4.4-9 9-.9-4.6-4.4-8.1-9-9 4.6-.9 8.1-4.4 9-9z"/>',
    chat: '<path d="M20.5 11.5a8 8 0 1 1-3.4-6.5L20.5 4l-1 3.6a8 8 0 0 1 1 3.9z"/>',
    phone: '<path d="M6.5 3.5h3l1.5 4-2 1.4a11 11 0 0 0 6.1 6.1l1.4-2 4 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.5 5.7a2 2 0 0 1 2-2.2z"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4.5 17l4.8-4.6 3.4 3.2 2.8-2.6 4 3.8"/>',
    slides: '<rect x="3.5" y="4.5" width="17" height="12" rx="2"/><path d="M12 16.5V20M8.5 20h7"/>',
    news: '<path d="M4.5 5.5h12a1 1 0 0 1 1 1v11a2 2 0 0 0 2 2h-13a2 2 0 0 1-2-2v-11a1 1 0 0 1 1-1z"/><path d="M17.5 9h1.5a1 1 0 0 1 1 1v7.5a1.8 1.8 0 0 1-1.8 1.8M7.5 9h6M7.5 12.5h6M7.5 16h4"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.14-1.4l2-1.55-2-3.46-2.35.95a7 7 0 0 0-2.42-1.4L13.7 2.6h-3.4l-.39 2.54a7 7 0 0 0-2.42 1.4l-2.35-.95-2 3.46 2 1.55A7 7 0 0 0 5 12c0 .48.05.94.14 1.4l-2 1.55 2 3.46 2.35-.95a7 7 0 0 0 2.42 1.4l.39 2.54h3.4l.39-2.54a7 7 0 0 0 2.42-1.4l2.35.95 2-3.46-2-1.55c.09-.46.14-.92.14-1.4z"/>',
    shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 7.9-7.5 9.5-4.3-1.6-7.5-4.9-7.5-9.5V6z"/><path d="M9.2 12.2l2 2 3.6-4"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    speaker: '<path d="M4 9.5v5h3.5L12 19V5L7.5 9.5zM15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/>',
    spark: '<path d="M12 3l2.1 5.6L20 12l-5.9 3.4L12 21l-2.1-5.6L4 12l5.9-3.4z"/>',
    user: '<circle cx="12" cy="8" r="3.2"/><path d="M5.5 19c1.5-2.9 3.9-4.4 6.5-4.4s5 1.5 6.5 4.4"/>',
    pin: '<path d="M9 4h6l-.7 6.2 3.2 3.3H6.5l3.2-3.3z"/><path d="M12 13.5V20"/>',
    folder: '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4.5l2 2.5H19A1.5 1.5 0 0 1 20.5 9.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    link: '<path d="M10 14a4.5 4.5 0 0 0 6.4.4l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.5 1.5"/><path d="M14 10a4.5 4.5 0 0 0-6.4-.4l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.5-1.5"/>',
    download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    trash: '<path d="M4.5 6.5h15M9.5 6V4.5h5V6M6.5 6.5l1 13h9l1-13M10 10.5v5M14 10.5v5"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    send: '<path d="M12 19V6M6.5 11.5L12 6l5.5 5.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
    refresh: '<path d="M20 11.5A8 8 0 1 0 18.6 16M20 5.5v6h-6"/>',
    edit: '<path d="M4.5 19.5h4L19 9a2.1 2.1 0 0 0-3-3L5.5 16.5z"/><path d="M14.5 6.5l3 3"/>',
    teach: '<path d="M3.5 6.5l8.5-3 8.5 3-8.5 3z"/><path d="M6.5 9.5v5c0 1.4 2.5 2.5 5.5 2.5s5.5-1.1 5.5-2.5v-5"/><path d="M20.5 6.5v6"/>',
    mic: '<rect x="9.5" y="3.5" width="5" height="10.5" rx="2.5"/><path d="M6 11.5a6 6 0 0 0 12 0M12 17.5V21M9 21h6"/>',
    micOff: '<path d="M9.5 5a2.5 2.5 0 0 1 5 0v6a2.5 2.5 0 0 1-3.6 2.2M6.4 10.8A6 6 0 0 0 12 17.5M12 17.5V21M9 21h6M4.5 4.5l15 15"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M18.8 5.2l-1.4 1.4M6.6 17.4L5.2 18.8"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-3.8-3.8"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    chevron: '<path d="M9 6l6 6-6 6"/>',
    upload: '<path d="M12 20V9M7 13.5l5-5 5 5M5 4h14"/>',
    pdf: '<path d="M20 11.5l-7.8 7.8a5 5 0 0 1-7-7L13 4.4a3.3 3.3 0 0 1 4.7 4.7l-7.8 7.8a1.7 1.7 0 0 1-2.4-2.4l7.1-7"/>',
    camera: '<path d="M4.5 8.5A2.5 2.5 0 0 1 7 6h1.5l1.2-1.8h4.6L15.5 6H17a2.5 2.5 0 0 1 2.5 2.5v8A2.5 2.5 0 0 1 17 19H7a2.5 2.5 0 0 1-2.5-2.5z"/><circle cx="12" cy="12.2" r="3.2"/>',
    star: '<path d="M12 4l2.4 5.2 5.6.7-4.2 3.9 1.1 5.7L12 16.8 7.1 19.5l1.1-5.7L4 9.9l5.6-.7z"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.2 2.3 3.3 5.2 3.3 8.5S14.2 18.7 12 20.5C9.8 18.2 8.7 15.3 8.7 12S9.8 5.8 12 3.5z"/>',
    logout: '<path d="M14 4.5H7a1.5 1.5 0 0 0-1.5 1.5v12A1.5 1.5 0 0 0 7 19.5h7M10 12h10.5M17 8.5l3.5 3.5-3.5 3.5"/>',
    lock: '<rect x="5" y="10.5" width="14" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    key: '<circle cx="8" cy="8" r="4"/><path d="M11 11l8 8M16 16l-2 2M19 19l-1.5 1.5"/>',
    bolt: '<path d="M13 3L5.5 13.5H11l-1 7.5L18.5 10H13z"/>',
    wand: '<path d="M5 19l9-9M14.5 5.5l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 13l.7 1.4 1.4.7-1.4.7-.7 1.4-.7-1.4-1.4-.7 1.4-.7z"/>',
  };
  return `<svg class="ic" viewBox="0 0 24 24" width="${size}" height="${size}">${paths[name] || paths.spark}</svg>`;
};

Aether.ifIcon = (name, size = 16) =>
  `<svg class="ic" viewBox="0 0 24 24" style="width:${size}px;height:${size}px">${name}</svg>`;

/* ───────────────────────── loading skeletons ─────────────────────── */

Aether.skeleton = function skeleton(kind = "message") {
  if (kind === "card") return '<div class="skeleton skeleton-card"></div>';
  if (kind === "line") return '<div class="skeleton skeleton-line"></div>';
  return '<div class="skeleton-msg">'
    + '<div class="skeleton skeleton-avatar"></div>'
    + '<div class="skeleton-lines">'
    + '<div class="skeleton skeleton-line" style="width:42%"></div>'
    + '<div class="skeleton skeleton-line" style="width:88%"></div>'
    + '<div class="skeleton skeleton-line" style="width:64%"></div>'
    + "</div></div>";
};

Aether.typingDots = () => '<span class="thinking"><i></i><i></i><i></i></span>';
