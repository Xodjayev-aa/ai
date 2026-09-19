/* ═══════════════════ Aether PWA ═══════════════════ */
"use strict";

const $ = (sel) => document.querySelector(sel);
const API = {
  token: localStorage.getItem("aether_token") || "",
  headers(extra = {}) {
    return { "Authorization": `Bearer ${this.token}`, ...extra };
  },
};

/* ─────────────── icons (stroke SVG, no emoji chrome) ─────────────── */
const I = {
  spark: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M12 3l2.1 5.6L20 12l-5.9 3.4L12 21l-2.1-5.6L4 12l5.9-3.4z"/></svg>',
  user: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><circle cx="12" cy="8" r="3.2"/><path d="M5.5 19c1.5-2.9 3.9-4.4 6.5-4.4s5 1.5 6.5 4.4"/></svg>',
  copy: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  vol: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M4 9.5v5h3.5L12 19V5L7.5 9.5zM15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/></svg>',
  pin: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M9 4h6l-.7 6.2 3.2 3.3H6.5l3.2-3.3z"/><path d="M12 13.5V20"/></svg>',
  folder: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4.5l2 2.5H19A1.5 1.5 0 0 1 20.5 9.5v8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/></svg>',
  link: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M10 14a4.5 4.5 0 0 0 6.4.4l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.5 1.5"/><path d="M14 10a4.5 4.5 0 0 0-6.4-.4l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.5-1.5"/></svg>',
  down: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg>',
  trash: '<svg class="ic" viewBox="0 0 24 24" style="width:13px;height:13px"><path d="M4.5 6.5h15M9.5 6V4.5h5V6M6.5 6.5l1 13h9l1-13M10 10.5v5M14 10.5v5"/></svg>',
  check: '<svg class="ic" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  x: '<svg class="ic" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};

/* ─────────────── toasts (no alert chrome) ─────────────── */
function toast(msg, type = "ok") {
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.innerHTML = (type === "err" ? I.x : I.check) + "<span></span>";
  el.querySelector("span").textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 3200);
  setTimeout(() => el.remove(), 3600);
}

/* ─────────────── tiny markdown renderer (XSS-safe) ─────────────── */
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function renderMarkdown(src) {
  let text = escapeHtml(src);
  const blocks = [];
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const clean = code.replace(/\n$/, "");
    const label = escapeHtml(lang || "code");
    blocks.push(
      `<div class="codewrap"><div class="codehead"><span>${label}</span>` +
      `<button class="code-copy" data-code="${encodeURIComponent(clean)}">` +
      `${I.copy} COPY</button></div>` +
      `<pre><code>${clean}</code></pre></div>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  // inline code
  text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // headings
  text = text.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>")
             .replace(/^##\s+(.+)$/gm, "<h2>$1</h2>")
             .replace(/^#\s+(.+)$/gm, "<h1>$1</h1>");
  // bold / italic
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
             .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // links
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // lists
  text = text.replace(/((?:^[-*]\s+.+\n?)+)/gm, (m) => {
    const items = m.trim().split("\n").map((l) => `<li>${l.replace(/^[-*]\s+/, "")}</li>`).join("");
    return `<ul>${items}</ul>`;
  });
  text = text.replace(/((?:^\d+\.\s+.+\n?)+)/gm, (m) => {
    const items = m.trim().split("\n").map((l) => `<li>${l.replace(/^\d+\.\s+/, "")}</li>`).join("");
    return `<ol>${items}</ol>`;
  });
  // tables (simple)
  text = text.replace(/((?:^\|.+\|\n)+)/gm, (m) => {
    const rows = m.trim().split("\n").filter((r) => !/^\|[\s:|-]+\|$/.test(r));
    if (rows.length < 1) return m;
    const cells = (r) => r.split("|").slice(1, -1).map((c) => c.trim());
    const head = cells(rows[0]);
    const body = rows.slice(1).map((r) => cells(r));
    return `<table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
      `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  });
  // paragraphs for leftover lines
  text = text.split(/\n{2,}/).map((chunk) =>
    /^\s*<(h\d|ul|ol|pre|table|blockquote)/.test(chunk) ? chunk
      : chunk.trim() ? `<p>${chunk.replace(/\n/g, "<br>")}</p>` : ""
  ).join("");
  // restore code blocks
  text = text.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[i]);
  return text;
}
function stripMarkdown(s) {
  return s.replace(/```[\s\S]*?```/g, " code block ")
    .replace(/[#*_`>|]/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").trim();
}

/* ─────────────── auth ─────────────── */
let authMode = "login";
$("#tab-login").onclick = () => switchTab("login");
$("#tab-register").onclick = () => switchTab("register");
function switchTab(mode) {
  authMode = mode;
  $("#tab-login").classList.toggle("active", mode === "login");
  $("#tab-register").classList.toggle("active", mode === "register");
  $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
  $("#auth-password").autocomplete = mode === "login" ? "current-password" : "new-password";
}
$("#auth-form").onsubmit = async (e) => {
  e.preventDefault();
  const errEl = $("#auth-error");
  errEl.classList.add("hidden");
  try {
    const resp = await fetch(`/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("#auth-email").value.trim(),
        password: $("#auth-password").value,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Something went wrong");
    API.token = data.access_token;
    localStorage.setItem("aether_token", API.token);
    localStorage.setItem("aether_email", $("#auth-email").value.trim().toLowerCase());
    enterApp();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
};
$("#logout-btn").onclick = () => {
  localStorage.removeItem("aether_token");
  location.reload();
};

/* ─────────────── app boot ─────────────── */
let currentConv = null;
let conversations = [];
let me = null;

async function enterApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#user-email").textContent = localStorage.getItem("aether_email") || "";
  try {
    const resp = await fetch("/api/auth/me", { headers: API.headers() });
    if (resp.ok) {
      me = await resp.json();
      $("#nav-admin").classList.toggle("hidden", !me.is_admin);
    }
  } catch {}
  await Promise.all([loadConversations(), loadStatus()]);
}

async function loadStatus() {
  const pill = $("#mode-pill");
  try {
    const resp = await fetch("/api/ai/status");
    const data = await resp.json();
    const n = data.providers_configured;
    pill.textContent = data.database_mode === "turso" ? "Turso" : "Local DB";
    pill.className = "pill " + (n > 0 ? "ok" : "warn");
    pill.title = `${n} AI provider(s) configured · DB: ${data.database_mode}\n` +
      (data.providers || []).map((p) =>
        `${p.name} (${p.key}): ${p.disabled ? "disabled" : p.cooling_down ? "cooling down" : "ready"}`
      ).join("\n") || "No providers configured";
    if (n === 0) pill.textContent = "No AI keys";
  } catch { pill.textContent = "Offline"; pill.className = "pill warn"; }
}

function setTitle(t) {
  document.title = t && t !== "New chat" ? `${t} · Aether` : "Aether";
}

/* ─────────────── conversations ─────────────── */
async function loadConversations() {
  const resp = await fetch("/api/conversations", { headers: API.headers() });
  if (resp.status === 401) return logout401();
  conversations = await resp.json();
  const list = $("#conv-list");
  list.innerHTML = "";
  conversations.forEach((c) => {
    const div = document.createElement("div");
    div.className = "conv-item" + (c.id === currentConv ? " active" : "");
    div.innerHTML = `<span class="title"></span><span class="acts">
      <button class="pin" title="Pin">${I.pin}</button>
      <button class="fold" title="Folder">${I.folder}</button>
      <button class="share" title="Share read-only link">${I.link}</button>
      <button class="exp" title="Export .md">${I.down}</button>
      <button class="del danger" title="Delete">${I.trash}</button></span>`;
    div.querySelector(".title").textContent = (c.pinned ? "· " : "") + c.title +
      (c.folder ? `  (${c.folder})` : "");
    div.onclick = () => openConversation(c.id);
    div.querySelector(".pin").onclick = (e) => { e.stopPropagation();
      fetch(`/api/conversations/${c.id}`, { method: "PATCH",
        headers: API.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ pinned: !c.pinned }) }).then(loadConversations); };
    div.querySelector(".fold").onclick = async (e) => { e.stopPropagation();
      const f = prompt("Folder name (empty = General):", c.folder || "");
      if (f === null) return;
      await fetch(`/api/conversations/${c.id}`, { method: "PATCH",
        headers: API.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ folder: f }) });
      loadConversations(); };
    div.querySelector(".share").onclick = async (e) => { e.stopPropagation();
      const r = await fetch(`/api/conversations/${c.id}/share`, { method: "POST", headers: API.headers() });
      const d = await r.json();
      if (!r.ok) return toast(d.detail || "Could not share", "err");
      const url = location.origin + d.path;
      try { await navigator.clipboard.writeText(url); toast("Read-only link copied"); }
      catch { prompt("Share this link:", url); } };
    div.querySelector(".exp").onclick = (e) => { e.stopPropagation();
      window.open(`/api/conversations/${c.id}/export?fmt=md`, "_blank"); };
    div.querySelector(".del").onclick = async () => {
      if (!confirm("Delete this conversation?")) return;
      await fetch(`/api/conversations/${c.id}`, { method: "DELETE", headers: API.headers() });
      if (currentConv === c.id) newChat();
      loadConversations();
    };
    list.appendChild(div);
  });
}

$("#new-chat").onclick = () => { newChat(); closeSidebar(); };

let searchTimer = null;
$("#chat-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const box = $("#search-results");
  if (q.length < 2) { box.classList.add("hidden"); return; }
  searchTimer = setTimeout(async () => {
    const resp = await fetch(`/api/chats/search?q=${encodeURIComponent(q)}`,
      { headers: API.headers() });
    if (!resp.ok) { box.classList.add("hidden"); return; }
    const hits = await resp.json();
    box.innerHTML = hits.length
      ? hits.map((h) => `<div class="search-hit" data-conv="${h.conversation_id}">
          <b>${escapeHtml(h.title)}</b>
          <span class="muted small-text">${escapeHtml(h.content.slice(0, 90))}…</span></div>`).join("")
      : '<div class="muted small-text" style="padding:8px">No matches.</div>';
    box.classList.remove("hidden");
    box.querySelectorAll(".search-hit").forEach((el) => {
      el.onclick = () => { box.classList.add("hidden"); $("#chat-search").value = "";
        openConversation(el.dataset.conv); closeSidebar(); };
    });
  }, 300);
});
function newChat() {
  currentConv = null;
  setTitle("New chat");
  $("#messages").innerHTML = `<div class="hero"><h2>What should we dig into?</h2>
    <p class="muted">Ask anything — answers are drafted by one AI and polished by another when it counts.</p></div>`;
  document.querySelectorAll(".conv-item").forEach((el) => el.classList.remove("active"));
}

async function openConversation(id) {
  currentConv = id;
  const c = conversations.find((x) => x.id === id);
  setTitle(c ? c.title : "Chat");
  const resp = await fetch(`/api/conversations/${id}`, { headers: API.headers() });
  if (!resp.ok) return;
  const conv = await resp.json();
  const box = $("#messages");
  box.innerHTML = "";
  (conv.messages || []).forEach((m) => appendMessage(m.role, m.content, m.meta));
  loadConversations();
  closeSidebar();
  scrollBottom(true);
}

/* ─────────────── chat rendering ─────────────── */
function appendMessage(role, content, meta = {}, live = false) {
  const box = $("#messages");
  const hero = box.querySelector(".hero");
  if (hero) hero.remove();
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  if (role === "user") {
    wrap.innerHTML = `<div class="bubble"></div>`;
  } else {
    wrap.innerHTML = `
      <div class="avatar">${I.spark}</div>
      <div class="bubble">
        <div class="meta"><span class="who">Aether</span>
          ${meta && meta.polished ? '<span class="badge polished">Polished</span>' : ""}
          <span class="acts"></span></div>
        <div class="content"></div>
      </div>`;
  }
  const contentEl = wrap.querySelector(".content");
  if (role === "user") contentEl.textContent = content;
  else if (live) contentEl.innerHTML = content;
  else contentEl.innerHTML = renderMarkdown(content);
  if (role === "assistant" && !live) addAssistantActions(wrap, content);
  box.appendChild(wrap);
  scrollBottom();
  return { wrap, contentEl };
}

function addAssistantActions(wrap, rawText) {
  const acts = wrap.querySelector(".acts");
  acts.innerHTML = `
    <button class="copy" title="Copy">${I.copy}</button>
    <button class="speak" title="Read aloud">${I.vol}</button>`;
  acts.querySelector(".copy").onclick = () => navigator.clipboard.writeText(rawText);
  acts.querySelector(".speak").onclick = (e) => speak(rawText, e.target);
}

function scrollBottom(force) {
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
  if (nearBottom || force) box.scrollTop = box.scrollHeight;
}

document.querySelectorAll(".chip-sugg").forEach((b) => {
  b.onclick = () => {
    const input = $("#chat-input");
    input.value = b.dataset.fill;
    autoGrow(input);
    input.focus();
  };
});

/* ─────────────── answer mode: ai | research ─────────────── */
let answerMode = "ai";
function setMode(m) {
  answerMode = m;
  $("#mode-ai").classList.toggle("active", m === "ai");
  $("#mode-research").classList.toggle("active", m === "research");
  updateToolsDot();
}
function updateToolsDot() {
  const dot = $("#tools-dot");
  if (!dot) return;
  const on = answerMode === "research" || $("#force-polish").checked ||
             $("#reasoning-toggle").checked || !!$("#persona-select").value;
  dot.classList.toggle("hidden", !on);
}
$("#mode-ai").onclick = () => setMode("ai");
$("#mode-research").onclick = () => setMode("research");

/* ─────────────── SSE chat streaming ─────────────── */
let streaming = false;
async function sendMessage() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || streaming) return;
  streaming = true;
  $("#send-btn").disabled = true;
  input.value = "";
  autoGrow(input);
  attachedDoc = null; attachedImage = null; renderChips();

  appendMessage("user", text);
  const { wrap, contentEl } = appendMessage("assistant", "", {}, true);
  contentEl.innerHTML = '<span class="thinking"><i></i><i></i><i></i></span>';
  const phaseBar = $("#phase-bar");
  let buffer = "";
  let polished = false;

  try {
    if (!currentConv) {
      const resp = await fetch("/api/conversations", {
        method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ title: text.slice(0, 60) }),
      });
      currentConv = (await resp.json()).id;
      setTitle(text.slice(0, 48));
      loadConversations();
    }
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: API.headers({ "Content-Type": "application/json", "Accept": "text/event-stream" }),
      body: JSON.stringify({
        conversation_id: currentConv,
        message: text,
        force_polish: $("#force-polish").checked,
        mode: answerMode,
        reasoning: $("#reasoning-toggle").checked,
        persona_id: $("#persona-select").value || null,
        doc_id: attachedDoc ? attachedDoc.doc_id : null,
        image_b64: attachedImage,
      }),
    });
    if (resp.status === 429) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.detail || "Daily limit reached");
    }
    if (!resp.ok || !resp.body) throw new Error(`Request failed (${resp.status})`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let sbuf = "";
    outer: while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      sbuf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = sbuf.indexOf("\n\n")) !== -1) {
        const raw = sbuf.slice(0, idx); sbuf = sbuf.slice(idx + 2);
        if (!raw.startsWith("data:")) continue;
        const payload = raw.slice(5).trim();
        if (payload === "[DONE]") break outer;
        let ev; try { ev = JSON.parse(payload); } catch { continue; }
        if (ev.type === "phase") {
          const label = ev.phase === "polish"
            ? "Second AI is polishing the answer"
            : ev.phase === "research"
            ? "Searching Wikipedia & DuckDuckGo"
            : "Drafting";
          phaseBar.innerHTML = '<span class="spin"></span><span></span>';
          phaseBar.querySelector("span:last-child").textContent = label + "…";
          phaseBar.classList.remove("hidden");
          if (ev.phase === "polish") { buffer = ""; polished = true; }
        } else if (ev.type === "delta") {
          buffer += ev.text;
          contentEl.innerHTML = renderMarkdown(buffer);
          contentEl.classList.add("cursor-blink");
          scrollBottom();
        } else if (ev.type === "final") {
          buffer = ev.text; polished = ev.polished;
        } else if (ev.type === "error") {
          throw new Error(ev.detail);
        } else if (ev.type === "done") {
          loadConversations();
        }
      }
    }
    contentEl.classList.remove("cursor-blink");
    contentEl.innerHTML = renderMarkdown(buffer);
    addAssistantActions(wrap, buffer);
    const metaEl = wrap.querySelector(".meta");
    if (polished && !metaEl.querySelector(".badge"))
      metaEl.insertAdjacentHTML("afterbegin", '<span class="badge polished">Polished</span>');
  } catch (err) {
    contentEl.classList.remove("cursor-blink");
    contentEl.innerHTML = `<p style="color:var(--err)">${escapeHtml(err.message || "Something went wrong")}</p>`;
  } finally {
    phaseBar.classList.add("hidden");
    streaming = false;
    $("#send-btn").disabled = false;
    scrollBottom();
  }
}
$("#send-btn").onclick = sendMessage;
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
function autoGrow(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 160) + "px"; }
$("#chat-input").addEventListener("input", (e) => autoGrow(e.target));

/* ─────────────── voice input ─────────────── */
let mediaRecorder = null, chunks = [];
$("#mic-btn").onclick = async () => {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", ""]
      .find((m) => !m || MediaRecorder.isTypeSupported(m));
    mediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    mediaRecorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const micOriginal = $("#mic-btn").innerHTML;
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      $("#mic-btn").classList.remove("recording");
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
      await transcribeBlob(blob);
    };
    mediaRecorder.start();
    $("#mic-btn").classList.add("recording");
  } catch { toast("Microphone permission denied.", "err"); }
};
async function transcribeBlob(blob) {
  const btn = $("#mic-btn");
  const original = btn.innerHTML;
  btn.innerHTML = "…";
  try {
    const form = new FormData();
    form.append("file", blob, "speech.webm");
    const resp = await fetch("/api/voice/transcribe", {
      method: "POST", headers: API.headers(), body: form,
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.text) {
      const input = $("#chat-input");
      input.value = (input.value ? input.value + " " : "") + data.text;
      autoGrow(input);
      input.focus();
    } else {
      // Server STT unavailable -> keyless browser speech recognition.
      browserSTT();
    }
  } finally { btn.innerHTML = original; }
}
function browserSTT() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast("Voice input needs a server STT key or a Chromium browser.", "err"); return; }
  const rec = new SR();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onresult = (e) => {
    const said = e.results[0][0].transcript;
    const input = $("#chat-input");
    input.value = (input.value ? input.value + " " : "") + said;
    autoGrow(input);
    input.focus();
  };
  rec.onerror = () => toast("Browser speech recognition failed.", "err");
  rec.start();
}

/* ─────────────── text-to-speech ─────────────── */
let currentAudio = null;
async function speak(text, btn) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  const original = btn.innerHTML;
  btn.innerHTML = "…";
  const clean = stripMarkdown(text).slice(0, 800);
  try {
    const resp = await fetch("/api/voice/tts", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text: clean }),
    });
    if (resp.ok) {
      const blob = await resp.blob();
      currentAudio = new Audio(URL.createObjectURL(blob));
      currentAudio.onended = () => (btn.innerHTML = original);
      currentAudio.onerror = () => { browserSpeak(clean, btn, original); };
      btn.textContent = "⏸";
      currentAudio.play();
      return;
    }
  } catch { /* fall through to browser TTS */ }
  browserSpeak(clean, btn);
}
function browserSpeak(text, btn, original) {
  if (!window.speechSynthesis) { btn.innerHTML = original || I.vol; return; }
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  u.onend = () => (btn.innerHTML = original || I.vol);
  window.speechSynthesis.speak(u);
  btn.textContent = "⏸";
}

/* ─────────────── images ─────────────── */
$("#nav-images").onclick = () => { showPane("images"); closeSidebar(); };
$("#image-go").onclick = async () => {
  const prompt = $("#image-prompt").value.trim();
  if (!prompt) return;
  const [w, h] = $("#image-size").value.split("x").map(Number);
  const results = $("#image-results");
  const card = document.createElement("div");
  card.className = "image-card";
  card.innerHTML = `<img alt="loading"><div class="cap"><span class="muted small-text">Generating…</span></div>`;
  results.prepend(card);
  try {
    const resp = await fetch("/api/images/generate", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ prompt, width: w, height: h }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Generation failed");
    const img = card.querySelector("img");
    img.onload = () => (card.querySelector(".cap span").textContent = prompt.slice(0, 80));
    img.onerror = () => (card.querySelector(".cap span").textContent = "Failed to load — try again");
    img.src = data.url;
    card.querySelector(".cap").insertAdjacentHTML("beforeend",
      `<a href="${data.url}" download target="_blank" rel="noopener">open ⬈</a>`);
  } catch (err) {
    card.querySelector(".cap span").textContent = err.message;
  }
};

/* ─────────────── presentations ─────────────── */
let currentDeck = null;
$("#nav-slides").onclick = () => { showPane("slides"); closeSidebar(); };
$("#slide-go").onclick = async () => {
  const topic = $("#slide-topic").value.trim();
  if (!topic) return;
  const status = $("#slide-status");
  status.classList.remove("hidden");
  status.textContent = "AI is writing your deck…";
  try {
    const resp = await fetch("/api/presentations/generate", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        topic, num_slides: Number($("#slide-count").value) || 8,
        audience: $("#slide-audience").value.trim() || "general audience",
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not build deck");
    currentDeck = data;
    status.textContent = `“${data.title}” — ${data.slides.length} slides ready`;
    openDeck(data);
  } catch (err) { status.textContent = err.message; }
};

function openDeck(deck) {
  currentDeck = deck;
  $("#deck-title").textContent = deck.title;
  window.__slideIdx = 0;
  renderSlide();
  $("#slides-modal").classList.remove("hidden");
}
function renderSlide() {
  const deck = currentDeck;
  const i = window.__slideIdx;
  const s = deck.slides[i];
  const stage = $("#slide-stage");
  stage.innerHTML = i === -1
    ? `<h3>${escapeHtml(deck.title)}</h3><p style="opacity:.7">${escapeHtml(deck.subtitle || "")}</p>`
    : `<h3>${escapeHtml(s.title)}</h3>
       <ul>${(s.bullets || []).map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>
       ${s.notes ? `<div class="slide-notes">${escapeHtml(s.notes)}</div>` : ""}`;
  $("#slide-counter").textContent = i === -1 ? `Title · ${deck.slides.length + 1} slides`
    : `${i + 1} / ${deck.slides.length}`;
}
$("#slide-prev").onclick = () => {
  const min = -1;
  if (window.__slideIdx > min) { window.__slideIdx--; renderSlide(); }
};
$("#slide-next").onclick = () => {
  if (window.__slideIdx < currentDeck.slides.length - 1) { window.__slideIdx++; renderSlide(); }
};
$("#slides-close").onclick = () => $("#slides-modal").classList.add("hidden");
$("#deck-theme").onchange = () => {}; // theme affects pptx export only
$("#deck-pptx").onclick = async () => {
  if (!currentDeck) return;
  const btn = $("#deck-pptx");
  btn.textContent = "⏳ Building…";
  try {
    const resp = await fetch("/api/presentations/pptx", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ outline: currentDeck, theme: $("#deck-theme").value }),
    });
    if (!resp.ok) throw new Error("Export failed");
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (currentDeck.title || "presentation").replace(/[^\w\- ]+/g, "").trim() + ".pptx";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) { toast(err.message, "err"); }
  finally { btn.textContent = "⬇ Export .pptx"; }
};

$("#deck-html").onclick = async () => {
  if (!currentDeck) return;
  const btn = $("#deck-html");
  btn.textContent = "⏳ Building…";
  try {
    const resp = await fetch("/api/presentations/html", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ outline: currentDeck, theme: $("#deck-theme").value }),
    });
    if (!resp.ok) throw new Error("HTML export failed");
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (currentDeck.title || "presentation").replace(/[^\w\- ]+/g, "").trim() + "-deck.html";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) { toast(err.message, "err"); }
  finally { btn.textContent = "HTML deck"; }
};


/* ─────────────── change password ─────────────── */
$("#password-btn").onclick = () => {
  $("#pw-error").classList.add("hidden");
  $("#password-form").reset();
  $("#password-modal").classList.remove("hidden");
};
$("#password-close").onclick = () => $("#password-modal").classList.add("hidden");
$("#password-form").onsubmit = async (e) => {
  e.preventDefault();
  const err = $("#pw-error");
  err.classList.add("hidden");
  const cur = $("#pw-current").value, nw = $("#pw-new").value, cf = $("#pw-confirm").value;
  if (nw !== cf) {
    err.textContent = "New passwords do not match.";
    err.classList.remove("hidden");
    return;
  }
  try {
    const resp = await fetch("/api/auth/password", {
      method: "POST",
      headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ current_password: cur, new_password: nw }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || "Could not update password");
    $("#password-modal").classList.add("hidden");
    toast("Password updated");
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
  }
};

/* ─────────────── settings pane ─────────────── */
$("#nav-settings").onclick = () => { showPane("settings"); loadSettings(); closeSidebar(); };
async function loadSettings() {
  try {
    const [ins, mem, per] = await Promise.all([
      fetch("/api/settings/instructions", { headers: API.headers() }).then(r => r.json()),
      fetch("/api/settings/memory", { headers: API.headers() }).then(r => r.json()),
      fetch("/api/settings/personas", { headers: API.headers() }).then(r => r.json()),
    ]);
    $("#instructions").value = ins.text || "";
    $("#auto-memory").checked = !!mem.auto;
    $("#memory-list").innerHTML = (mem.memories || []).map((m) =>
      `<div class="mem-row"><span>${escapeHtml(m.content)}</span>
       <button data-id="${m.id}" class="mem-del">${I.x}</button></div>`).join("")
      || '<p class="muted small-text">No memories yet — chat and they will appear.</p>';
    $("#memory-list").querySelectorAll(".mem-del").forEach((b) => {
      b.onclick = () => fetch("/api/settings/memory/" + b.dataset.id,
        { method: "DELETE", headers: API.headers() }).then(loadSettings);
    });
    renderPersonas(per || []);
  } catch {}
}
function renderPersonas(list) {
  $("#persona-list").innerHTML = list.map((p) =>
    `<div class="mem-row"><span><b>${escapeHtml(p.name)}</b> — <span class="muted">${escapeHtml(p.prompt.slice(0, 80))}…</span></span>
     <button data-id="${p.id}" class="per-del">${I.x}</button></div>`).join("")
    || '<p class="muted small-text">No personas yet.</p>';
  $("#persona-list").querySelectorAll(".per-del").forEach((b) => {
    b.onclick = () => fetch("/api/settings/personas/" + b.dataset.id,
      { method: "DELETE", headers: API.headers() }).then(loadSettings);
  });
  const sel = $("#persona-select");
  sel.innerHTML = '<option value="">Persona</option>' +
    list.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
}
$("#instructions-save").onclick = async () => {
  const r = await fetch("/api/settings/instructions", { method: "PUT",
    headers: API.headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ text: $("#instructions").value }) });
  r.ok ? toast("Instructions saved") : toast("Could not save.", "err");
};
$("#auto-memory").onchange = async (e) => {
  await fetch("/api/settings/memory/auto", { method: "PUT",
    headers: API.headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ enabled: e.target.checked }) });
};
$("#persona-add").onclick = async () => {
  const name = $("#persona-name").value.trim(), prompt = $("#persona-prompt").value.trim();
  if (!name || !prompt) return toast("Name and prompt are required.", "err");
  const r = await fetch("/api/settings/personas", { method: "POST",
    headers: API.headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name, prompt }) });
  if (!r.ok) return toast((await r.json().catch(() => ({}))).detail || "Failed", "err");
  $("#persona-name").value = ""; $("#persona-prompt").value = "";
  toast("Persona added");
  loadSettings();
};
$("#export-all").onclick = () => window.open("/api/settings/export", "_blank");

/* ─────────────── news pane ─────────────── */
$("#nav-news").onclick = () => { showPane("news"); closeSidebar(); };
$("#news-go").onclick = async () => {
  const box = $("#news-results");
  box.innerHTML = '<p class="muted" style="padding:0 18px">Loading…</p>';
  try {
    const q = $("#news-topic").value.trim();
    const resp = await fetch("/api/news" + (q ? `?q=${encodeURIComponent(q)}` : ""),
      { headers: API.headers() });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Failed");
    box.innerHTML = data.items.map((n) =>
      `<div class="news-item"><a href="${escapeHtml(n.link)}" target="_blank" rel="noopener">
       ${escapeHtml(n.title)}</a><span class="muted small-text">${escapeHtml(n.source)} · ${escapeHtml(n.published)}</span></div>`).join("")
      || '<p class="muted" style="padding:0 18px">No items.</p>';
  } catch (err) {
    box.innerHTML = `<p class="muted" style="padding:0 18px">${escapeHtml(err.message)}</p>`;
  }
};

/* ─────────────── tasks pane ─────────────── */
$("#nav-tasks").onclick = () => { showPane("tasks"); loadTasks(); closeSidebar(); };
const hourSel = $("#task-hour");
for (let h = 0; h < 24; h++) {
  const o = document.createElement("option");
  o.value = h; o.textContent = `⏰ ${String(h).padStart(2, "0")}:00 UTC`;
  hourSel.appendChild(o);
}
hourSel.value = 6;
async function loadTasks() {
  const resp = await fetch("/api/tasks", { headers: API.headers() });
  const tasks = await resp.json().catch(() => []);
  $("#task-list").innerHTML = tasks.map((t) =>
    `<div class="mem-row"><span><b>${escapeHtml(t.prompt.slice(0, 60))}</b>
     <br><span class="muted small-text">daily at ${String(t.hour_utc).padStart(2, "0")}:00 UTC · ${t.last_run ? "last run " + t.last_run : "not run yet"}</span></span>
     <button data-id="${t.id}" class="task-del">${I.x}</button></div>`).join("")
    || '<p class="muted small-text">No tasks yet.</p>';
  $("#task-list").querySelectorAll(".task-del").forEach((b) => {
    b.onclick = () => fetch("/api/tasks/" + b.dataset.id,
      { method: "DELETE", headers: API.headers() }).then(loadTasks);
  });
}
$("#task-add").onclick = async () => {
  const prompt = $("#task-prompt").value.trim();
  if (!prompt) return;
  const r = await fetch("/api/tasks", { method: "POST",
    headers: API.headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ prompt, hour_utc: Number(hourSel.value) }) });
  if (!r.ok) return toast((await r.json().catch(() => ({}))).detail || "Failed", "err");
  $("#task-prompt").value = "";
  toast("Task scheduled");
  loadTasks();
};

/* ─────────────── attachments: PDF + image ─────────────── */
let attachedDoc = null;   // {doc_id, name}
let attachedImage = null; // dataURL
function renderChips() {
  const box = $("#attachments");
  const chips = [];
  if (attachedDoc) chips.push(`<span class="chip">PDF · ${escapeHtml(attachedDoc.name)} <button data-k="doc">${I.x}</button></span>`);
  if (attachedImage) chips.push(`<span class="chip">Image <button data-k="img">${I.x}</button></span>`);
  box.innerHTML = chips.join("");
  box.classList.toggle("hidden", !chips.length);
  box.querySelector('[data-k="doc"]')?.addEventListener("click", () => { attachedDoc = null; renderChips(); });
  box.querySelector('[data-k="img"]')?.addEventListener("click", () => { attachedImage = null; renderChips(); });
}
$("#tool-pdf").onclick = () => { $("#tools-menu").classList.add("hidden"); $("#pdf-input").click(); };
$("#tool-image").onclick = () => { $("#tools-menu").classList.add("hidden"); $("#image-input").click(); };
$("#pdf-input").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const form = new FormData();
  form.append("file", f);
  const r = await fetch("/api/files/extract-pdf", { method: "POST", headers: API.headers(), body: form });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) return toast(d.detail || "Could not read PDF", "err");
  attachedDoc = { doc_id: d.doc_id, name: d.name };
  renderChips();
  e.target.value = "";
};
$("#image-input").onchange = (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 4 * 1024 * 1024) return toast("Image too large (max 4 MB)", "err");
  const reader = new FileReader();
  reader.onload = () => { attachedImage = reader.result; renderChips(); };
  reader.readAsDataURL(f);
  e.target.value = "";
};

/* ─────────────── reasoning toggle ─────────────── */

/* ─────────────── misc UI ─────────────── */
function showPane(name) {
  $("#chat-pane").classList.toggle("hidden", name !== "chat");
  $("#images-pane").classList.toggle("hidden", name !== "images");
  $("#slides-pane").classList.toggle("hidden", name !== "slides");
  const admin = $("#admin-pane");
  if (admin) admin.classList.toggle("hidden", name !== "admin");
  for (const [id, key] of [["settings-pane", "settings"], ["news-pane", "news"],
                           ["tasks-pane", "tasks"]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", name !== key);
  }
  if (name === "chat") { $("#chat-pane").classList.remove("hidden"); }
}
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
}
const isMobile = () => window.matchMedia("(max-width: 860px)").matches;
$("#menu-btn").onclick = () => {
  if (isMobile()) {
    $("#sidebar").classList.add("open");
    $("#scrim").classList.add("show");
  } else {
    $("#app-view").classList.toggle("sb-collapsed");
  }
};
$("#sb-close").onclick = () => {
  if (isMobile()) closeSidebar();
  else $("#app-view").classList.add("sb-collapsed");
};
$("#scrim").onclick = closeSidebar;
$("#user-btn").onclick = () => {
  if (isMobile() || $("#app-view").classList.contains("sb-collapsed")) {
    if (isMobile()) { $("#sidebar").classList.add("open"); $("#scrim").classList.add("show"); }
    else $("#app-view").classList.remove("sb-collapsed");
  } else {
    showPane("settings");
    if (typeof loadSettings === "function") loadSettings();
  }
};
function logout401() {
  localStorage.removeItem("aether_token");
  fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.reload();
}

document.getElementById("messages").addEventListener("click", async (e) => {
  const btn = e.target.closest(".code-copy");
  if (!btn) return;
  try {
    await navigator.clipboard.writeText(decodeURIComponent(btn.dataset.code));
    btn.textContent = "COPIED";
    setTimeout(() => { btn.innerHTML = I.copy + " COPY"; }, 1400);
  } catch {}
});

/* ─────────────── nav: chat + tools menu ─────────────── */
$("#nav-chat").onclick = () => { showPane("chat"); closeSidebar(); };
$("#tools-btn").onclick = (e) => {
  e.stopPropagation();
  $("#tools-menu").classList.toggle("hidden");
};
document.addEventListener("click", (e) => {
  if (!e.target.closest(".tools-anchor")) $("#tools-menu").classList.add("hidden");
});
function syncSwitch(swId, inputId) {
  document.getElementById(swId).classList.toggle("on",
    document.getElementById(inputId).checked);
}
$("#tool-deep").onclick = () => {
  const cb = $("#force-polish");
  cb.checked = !cb.checked;
  syncSwitch("sw-deep", "force-polish");
  updateToolsDot();
};
$("#tool-reason").onclick = () => {
  const cb = $("#reasoning-toggle");
  cb.checked = !cb.checked;
  syncSwitch("sw-reason", "reasoning-toggle");
  updateToolsDot();
};
$("#persona-select").addEventListener("change", updateToolsDot);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#tools-menu").classList.add("hidden");
});

/* ─────────────── boot ─────────────── */
syncSwitch("sw-deep", "force-polish");
syncSwitch("sw-reason", "reasoning-toggle");
function showOffline() {
  $("#auth-view").classList.remove("hidden");
  $("#retry-btn").classList.remove("hidden");
  const err = $("#auth-error");
  err.textContent = "Can't reach the server right now — your session is saved.";
  err.classList.remove("hidden");
}
$("#retry-btn").onclick = () => location.reload();

(async function init() {
  if (API.token) {
    try {
      const resp = await fetch("/api/auth/me", { headers: API.headers() });
      if (resp.ok) { enterApp(); return; }
      if (resp.status !== 401) { showOffline(); return; } // server hiccup — keep session
      localStorage.removeItem("aether_token");            // genuinely invalid session
    } catch {
      showOffline();                                      // network error — keep session
      return;
    }
  }
  $("#auth-view").classList.remove("hidden");
})();

/* ─────────────── admin dashboard ─────────────── */
$("#nav-admin").onclick = () => { showPane("admin"); loadAdmin(); closeSidebar(); };
async function loadAdmin() {
  const body = $("#admin-body");
  if (!body) return;
  try {
    const resp = await fetch("/api/admin/overview", { headers: API.headers() });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || "Admin only");
    renderAdmin(await resp.json());
  } catch (err) {
    body.innerHTML = '<p class="muted" style="padding:20px">' + escapeHtml(err.message) + '</p>';
  }
}
function renderAdmin(data) {
  const s = data.stats || {};
  $("#admin-cards").innerHTML = [
    ["Users", (data.users || []).length],
    ["Conversations", s.conversations],
    ["Messages", s.messages],
    ["AI requests today", s.requests_today],
    ["AI requests total", s.requests_total],
  ].map(([k, v]) => '<div class="stat-card"><div class="stat-v">' + escapeHtml(String(v ?? 0)) +
    '</div><div class="stat-k">' + k + '</div></div>').join("");

  const ownerEmail = (data.ai && data.ai.owner) || "";
  const ut = $("#admin-users");
  ut.innerHTML = "<tr><th>Email</th><th>Chats</th><th>Msgs</th><th>Role</th><th></th></tr>" +
    (data.users || []).map((u) => {
      const isOwner = u.email === ownerEmail;
      const role = u.is_admin ? (isOwner ? "owner" : "admin") : "user";
      const actions = [];
      if (!isOwner && u.id !== (me && me.id)) {
        actions.push('<button data-act="role" data-id="' + u.id + '" data-val="' + (u.is_admin ? 0 : 1) + '">' +
          (u.is_admin ? "demote" : "promote") + '</button>');
        actions.push('<button data-act="deluser" data-id="' + u.id + '" data-email="' + escapeHtml(u.email) + '">delete</button>');
      }
      return "<tr><td>" + escapeHtml(u.email) + "</td><td>" + u.conversations + "</td><td>" + u.messages +
        '</td><td><span class="badge' + (u.is_admin ? " polished" : "") + '">' + role + "</span></td><td class='rowacts'>" +
        actions.join(" ") + "</td></tr>";
    }).join("");
  ut.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = async () => {
      const act = b.dataset.act, id = b.dataset.id;
      if (act === "deluser") {
        if (!confirm("Delete user " + b.dataset.email + " and ALL their data?")) return;
        await fetch("/api/admin/users/" + id, { method: "DELETE", headers: API.headers() });
      } else if (act === "role") {
        await fetch("/api/admin/users/" + id + "/admin", {
          method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
          body: JSON.stringify({ is_admin: b.dataset.val === "1" }) });
      }
      loadAdmin();
    };
  });

  const ct = $("#admin-convs");
  ct.innerHTML = "<tr><th>Title</th><th>User</th><th>Msgs</th><th></th></tr>" +
    (data.conversations || []).map((c) =>
      "<tr><td>" + escapeHtml((c.title || "").slice(0, 40)) + "</td><td>" + escapeHtml(c.user_email || "?") +
      "</td><td>" + c.message_count + '</td><td class="rowacts"><button data-id="' + c.id +
      '">delete</button></td></tr>').join("");
  ct.querySelectorAll("button[data-id]").forEach((b) => {
    b.onclick = async () => {
      if (!confirm("Delete this conversation?")) return;
      await fetch("/api/admin/conversations/" + b.dataset.id, { method: "DELETE", headers: API.headers() });
      loadAdmin();
    };
  });

  const ai = data.ai || {};
  $("#admin-ai").innerHTML =
    '<p class="small-text">DB mode: <b>' + escapeHtml(ai.database_mode || "?") + '</b></p>' +
    '<p class="small-text muted">Providers (rotate in order, auto-failover):</p>' +
    (ai.providers || []).map((p) =>
      '<div class="prov-row ' + (p.disabled ? "bad" : "") + '"><span>' + escapeHtml(p.name) +
      '</span><span class="muted small-text">' + escapeHtml(p.key) + " · " +
      (p.disabled ? "disabled" : p.cooling_down ? "cooling" : "ready") +
      " · ok " + p.ok + " / fail " + p.failed + "</span></div>").join("") +
    '<p class="small-text muted" style="margin-top:8px">Features: ' +
    Object.entries(ai.features || {}).map(([k, v]) => k + ": " + (Array.isArray(v) ? v.length : v)).join(" · ") +
    '</p><button id="admin-refresh" class="btn ghost small-btn" style="margin-top:10px">↻ Refresh</button>';
  const rf = $("#admin-refresh");
  if (rf) rf.onclick = loadAdmin;
}
