/* ═══════════════════ Aether PWA ═══════════════════ */
"use strict";

const $ = (sel) => document.querySelector(sel);
const API = {
  token: localStorage.getItem("aether_token") || "",
  headers(extra = {}) {
    return { "Authorization": `Bearer ${this.token}`, ...extra };
  },
};

/* ─────────────── tiny markdown renderer (XSS-safe) ─────────────── */
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function renderMarkdown(src) {
  let text = escapeHtml(src);
  const blocks = [];
  // fenced code blocks
  text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code class="lang">${code.replace(/\n$/, "")}</code></pre>`);
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
    pill.textContent = data.database_mode === "turso" ? "☁️ Turso" : "💾 local DB";
    pill.className = "pill " + (n > 0 ? "ok" : "warn");
    pill.title = `${n} AI provider(s) configured · DB: ${data.database_mode}\n` +
      (data.providers || []).map((p) =>
        `${p.name} (${p.key}): ${p.disabled ? "disabled" : p.cooling_down ? "cooling down" : "ready"}`
      ).join("\n") || "No providers configured";
    if (n === 0) pill.textContent = "⚠️ no AI keys";
  } catch { pill.textContent = "⚠️ offline"; pill.className = "pill warn"; }
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
    div.innerHTML = `<span class="title"></span><button class="del" title="Delete">🗑</button>`;
    div.querySelector(".title").textContent = c.title;
    div.onclick = (e) => { if (!e.target.classList.contains("del")) openConversation(c.id); };
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
function newChat() {
  currentConv = null;
  $("#messages").innerHTML = `<div class="hero"><h2>What can I help with?</h2>
    <p class="muted">Ask anything · use 🎤 to speak · answers draft with one AI and get polished by another when it counts</p></div>`;
  document.querySelectorAll(".conv-item").forEach((el) => el.classList.remove("active"));
}

async function openConversation(id) {
  currentConv = id;
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
  wrap.innerHTML = `
    <div class="avatar">${role === "user" ? "🧑" : "✨"}</div>
    <div class="bubble">
      <div class="meta"><span>${role === "user" ? "You" : "Aether"}</span>
        ${meta && meta.polished ? '<span class="badge polished">✨ polished</span>' : ""}
        <span class="acts"></span></div>
      <div class="content ${live ? "typing" : ""}"></div>
    </div>`;
  const contentEl = wrap.querySelector(".content");
  if (live) contentEl.textContent = content;
  else contentEl.innerHTML = renderMarkdown(content);
  if (role === "assistant" && !live) addAssistantActions(wrap, content);
  box.appendChild(wrap);
  scrollBottom();
  return { wrap, contentEl };
}

function addAssistantActions(wrap, rawText) {
  const acts = wrap.querySelector(".acts");
  acts.innerHTML = `
    <button class="copy" title="Copy">📋</button>
    <button class="speak" title="Read aloud">🔊</button>`;
  acts.querySelector(".copy").onclick = () => navigator.clipboard.writeText(rawText);
  acts.querySelector(".speak").onclick = (e) => speak(rawText, e.target);
}

function scrollBottom(force) {
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
  if (nearBottom || force) box.scrollTop = box.scrollHeight;
}

/* ─────────────── answer mode: ai | research ─────────────── */
let answerMode = "ai";
function setMode(m) {
  answerMode = m;
  $("#mode-ai").classList.toggle("active", m === "ai");
  $("#mode-research").classList.toggle("active", m === "research");
  $("#force-polish").parentElement.style.display = m === "research" ? "none" : "flex";
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

  appendMessage("user", text);
  const { wrap, contentEl } = appendMessage("assistant", "", {}, true);
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
          phaseBar.textContent = ev.phase === "polish"
            ? "✨ Second AI is polishing the answer…"
            : ev.phase === "research"
            ? "📚 Searching Wikipedia & DuckDuckGo…"
            : "⚡ Drafting…";
          phaseBar.classList.remove("hidden");
          if (ev.phase === "polish") { buffer = ""; polished = true; }
        } else if (ev.type === "delta") {
          buffer += ev.text;
          contentEl.innerHTML = renderMarkdown(buffer) + '<span class="typing"></span>';
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
    contentEl.classList.remove("typing");
    contentEl.innerHTML = renderMarkdown(buffer);
    addAssistantActions(wrap, buffer);
    const metaEl = wrap.querySelector(".meta");
    if (polished && !metaEl.querySelector(".badge"))
      metaEl.insertAdjacentHTML("afterbegin", '<span class="badge polished">✨ polished</span>');
  } catch (err) {
    contentEl.classList.remove("typing");
    contentEl.innerHTML = `<p style="color:var(--err)">⚠️ ${escapeHtml(err.message || "Something went wrong")}</p>`;
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
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      $("#mic-btn").classList.remove("recording");
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
      await transcribeBlob(blob);
    };
    mediaRecorder.start();
    $("#mic-btn").classList.add("recording");
  } catch { alert("Microphone permission denied."); }
};
async function transcribeBlob(blob) {
  const btn = $("#mic-btn");
  btn.textContent = "⏳";
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
  } finally { btn.textContent = "🎤"; }
}
function browserSTT() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert("Voice input needs a server STT key or a Chromium browser."); return; }
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
  rec.onerror = () => alert("Browser speech recognition failed.");
  rec.start();
}

/* ─────────────── text-to-speech ─────────────── */
let currentAudio = null;
async function speak(text, btn) {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  btn.textContent = "⏳";
  const clean = stripMarkdown(text).slice(0, 800);
  try {
    const resp = await fetch("/api/voice/tts", {
      method: "POST", headers: API.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text: clean }),
    });
    if (resp.ok) {
      const blob = await resp.blob();
      currentAudio = new Audio(URL.createObjectURL(blob));
      currentAudio.onended = () => (btn.textContent = "🔊");
      currentAudio.onerror = () => { browserSpeak(clean, btn); };
      btn.textContent = "⏸";
      currentAudio.play();
      return;
    }
  } catch { /* fall through to browser TTS */ }
  browserSpeak(clean, btn);
}
function browserSpeak(text, btn) {
  if (!window.speechSynthesis) { btn.textContent = "🔊"; return; }
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.02;
  u.onend = () => (btn.textContent = "🔊");
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
    img.onerror = () => (card.querySelector(".cap span").textContent = "⚠️ failed to load — try again");
    img.src = data.url;
    card.querySelector(".cap").insertAdjacentHTML("beforeend",
      `<a href="${data.url}" download target="_blank" rel="noopener">open ⬈</a>`);
  } catch (err) {
    card.querySelector(".cap span").textContent = `⚠️ ${err.message}`;
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
  status.textContent = "🧠 AI is writing your deck…";
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
    status.textContent = `✅ “${data.title}” — ${data.slides.length} slides ready`;
    openDeck(data);
  } catch (err) { status.textContent = `⚠️ ${err.message}`; }
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
       ${s.notes ? `<div class="slide-notes">🗒 ${escapeHtml(s.notes)}</div>` : ""}`;
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
  } catch (err) { alert(err.message); }
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
  } catch (err) { alert(err.message); }
  finally { btn.textContent = "🌐 HTML deck"; }
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
    alert("✅ Password updated.");
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
  }
};

/* ─────────────── misc UI ─────────────── */
function showPane(name) {
  $("#chat-pane").classList.toggle("hidden", name !== "chat");
  $("#images-pane").classList.toggle("hidden", name !== "images");
  $("#slides-pane").classList.toggle("hidden", name !== "slides");
  const admin = $("#admin-pane");
  if (admin) admin.classList.toggle("hidden", name !== "admin");
}
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
}
$("#menu-btn").onclick = () => {
  $("#sidebar").classList.toggle("open");
  $("#scrim").classList.toggle("show");
};
$("#scrim").onclick = closeSidebar;
$("#user-btn").onclick = () => {
  const s = $("#sidebar");
  s.classList.add("open"); $("#scrim").classList.add("show");
};
function logout401() {
  localStorage.removeItem("aether_token");
  fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.reload();
}

/* ─────────────── boot ─────────────── */
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
    body.innerHTML = '<p class="muted" style="padding:20px">⚠️ ' + escapeHtml(err.message) + '</p>';
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
