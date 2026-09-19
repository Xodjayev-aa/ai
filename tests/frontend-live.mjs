/**
 * Live end-to-end frontend test: runs the real app (jsdom) against a running
 * Aether server over real HTTP — real auth, real database, real streaming.
 *
 *   AETHER_BASE=http://127.0.0.1:8000 node tests/frontend-live.mjs
 *
 * AI providers are unreachable inside the build sandbox, so the chat leg
 * asserts the *error handling* (clear message + Retry) instead of an answer;
 * that is the same code path a rate-limited free tier hits in production.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { JSDOM, VirtualConsole } = require("/home/user/node_modules/jsdom");

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.AETHER_BASE || "http://127.0.0.1:8000";
const html = readFileSync(join(root, "frontend/index.html"), "utf8");
const SCRIPTS = [
  "core.js", "chat.js", "voice.js", "slides.js", "images.js",
  "settings.js", "tools.js", "admin.js", "app.js",
];

const failures = [];
const errors = [];
function check(name, ok, detail = "") {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(name);
}

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => errors.push(e.message));
virtualConsole.on("error", (m) => errors.push(String(m)));

const dom = new JSDOM(html, {
  url: `${BASE}/`, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole,
});
const { window } = dom;

// Real fetch, with relative URLs resolved against the server.
const realFetch = globalThis.fetch;
window.fetch = (url, options) => {
  const target = String(url).startsWith("/") ? BASE + url : String(url);
  return realFetch(target, options);
};
window.HTMLElement.prototype.scrollIntoView = () => {};
window.URL.createObjectURL = () => "blob:fake";
window.URL.revokeObjectURL = () => {};
window.Audio = class { constructor() {} play() { return Promise.resolve(); } pause() {} };
class FakeRecognition {
  constructor() { window.__rec = this; }
  start() { this.onstart?.(); }
  stop() { this.onend?.(); }
  abort() {}
}
window.SpeechRecognition = FakeRecognition;
window.webkitSpeechRecognition = FakeRecognition;
window.speechSynthesis = { speak() {}, cancel() {}, getVoices: () => [], onvoiceschanged: null };
window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };

for (const name of SCRIPTS) {
  try {
    window.eval(readFileSync(join(root, "frontend/assets/js", name), "utf8"));
  } catch (error) {
    errors.push(`${name}: ${error.message}`);
  }
}

const $ = (sel) => window.document.querySelector(sel);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 15000) {
  const deadline = Date.now() + ms;
  for (;;) {
    if (fn()) return true;
    if (Date.now() > deadline) return false;
    await wait(60);
  }
}

async function run() {
  check("scripts evaluated without exceptions", errors.length === 0, errors.join(" | "));
  await until(() => !$("#auth-view").classList.contains("hidden"));
  check("auth screen is shown when signed out", !$("#auth-view").classList.contains("hidden"));

  // ── register through the real UI ────────────────────────────────
  const email = `smoke-${Date.now()}@example.com`;
  $("#tab-register").click();
  $("#auth-email").value = email;
  $("#auth-password").value = "supersecret1";
  $("#auth-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  const entered = await until(() => !$("#app-view").classList.contains("hidden"));
  check("registration signs the user in", entered, $("#auth-error")?.textContent || "");
  check("session token stored", Boolean(window.localStorage.getItem("aether_token")));

  // ── shell state from the real API ───────────────────────────────
  const meterReady = await until(() => ($("#sb-meter-text")?.textContent || "").includes("300"), 15000);
  check("usage meter loaded from /api/usage", meterReady, $("#sb-meter-text")?.textContent);
  const pillReady = await until(() => ($("#mode-pill")?.textContent || "").trim().length > 0, 15000);
  check("status pill loaded from /api/ai/status", pillReady, $("#mode-pill")?.textContent);
  check("storage banner stays hidden when storage is healthy",
        $("#storage-banner").classList.contains("hidden"), $("#storage-banner")?.textContent);
  check("personas loaded", window.document.querySelectorAll("#persona-select option").length >= 1);
  check("empty-state hero renders in chat", Boolean($("#messages .hero")));

  // ── chat: streaming leg (provider unreachable → must fail loudly) ─
  $("#chat-input").value = "Hello Aether, say hi in five words.";
  $("#chat-input").dispatchEvent(new window.Event("input"));
  $("#send-btn").click();
  const resolved = await until(() =>
    window.document.querySelector("#messages .error-card") ||
    window.document.querySelector("#messages .msg.assistant .content")?.textContent?.trim(), 45000);
  check("send produces an answer or a visible error", resolved);
  const card = window.document.querySelector("#messages .error-card");
  if (card) {
    check("error card explains the failure", (card.textContent || "").length > 10, card.textContent.slice(0, 70));
    check("error card offers Retry", Boolean(card.querySelector(".retry-btn, button")));
  } else {
    check("answer rendered with action buttons",
          window.document.querySelectorAll(".msg.assistant .msg-act").length >= 3);
  }
  check("user message is in the transcript", Boolean(window.document.querySelector(".msg.user")));
  check("conversation persisted server-side", await (async () => {
    const res = await realFetch(`${BASE}/api/conversations`, {
      headers: { authorization: `Bearer ${window.localStorage.getItem("aether_token")}` },
    });
    const list = await res.json();
    return Array.isArray(list) && list.length >= 1;
  })());
  check("usage counted the request", await (async () => {
    const res = await realFetch(`${BASE}/api/usage`, {
      headers: { authorization: `Bearer ${window.localStorage.getItem("aether_token")}` },
    });
    const usage = await res.json();
    return usage.used_today >= 1;
  })());

  // ── teach mode through the real API ─────────────────────────────
  const teach = await window.Aether.api("/api/teach/start", { method: "POST", body: {} });
  check("teach interview starts", Boolean(teach.session_id) && teach.questions.length === 5,
        `${teach.questions?.length} questions`);
  const answered = await window.Aether.api("/api/teach/answer", {
    method: "POST",
    body: { session_id: teach.session_id, question_id: teach.questions[0].id, answer: "Call me Sam." },
  });
  check("teach answer is stored as a memory",
        ["ok", "saved"].includes(answered.status) && answered.saved_count >= 1,
        JSON.stringify(answered).slice(0, 90));
  const memories = await window.Aether.api("/api/settings/memory");
  check("memory list reflects the answer",
        (memories.memories || []).some((m) => (m.content || "").includes("Sam")));

  // ── voice catalogue + a real TTS attempt ────────────────────────
  const voices = await window.Aether.api("/api/voice/voices");
  check("voice catalogue lists neural voices", voices.voices.length >= 4, `${voices.voices.length} voices`);
  check("voice catalogue documents browser fallback", Boolean(voices.browser_fallback));
  try {
    const blob = await window.Aether.api("/api/voice/tts", {
      method: "POST", body: { text: "Hello from Aether.", voice: "nova" }, raw: true,
    });
    check("keyless TTS returns audio", blob.size > 500, `${blob.size} bytes`);
  } catch (error) {
    check("TTS failure asks the browser to take over",
          Boolean(error.browserTts), String(error.detail || error.message).slice(0, 90));
  }

  // ── HD voice chain: hd → legacy → {browser_tts} ─────────────────
  const hdInfo = voices.hd || {};
  check("HD catalogue is advertised", hdInfo.enabled === true && (hdInfo.voices || []).length > 0,
        `${(hdInfo.voices || []).length} HD voices`);
  check("HD catalogue leads with uz, en, ru, tr",
        ["uz", "en", "ru", "tr"].every((code, i) => hdInfo.languages?.[i]?.code === code),
        (hdInfo.languages || []).slice(0, 4).map((l) => l.code).join(","));
  const ttsFetch = async (body) => window.fetch("/api/voice/tts", {
    method: "POST",
    headers: { "content-type": "application/json",
               authorization: `Bearer ${window.Aether.session.token}` },
    body: JSON.stringify(body),
  });
  const hdResp = await ttsFetch({ text: "Salom, sinov.", voice: "uz-UZ-SardorNeural",
                                  speed: 1.0, provider: "auto" });
  const hdHeader = hdResp.headers.get("x-aether-tts") || "";
  if (hdResp.status === 200) {
    const size = (await hdResp.arrayBuffer()).byteLength;
    check("HD voice answers through the chain", ["hd", "legacy"].includes(hdHeader) && size > 500,
          `${hdHeader} · ${size} bytes`);
    check("X-Aether-TTS names the engine that answered", Boolean(hdHeader), hdHeader);
  } else {
    // No internet in the sandbox: the chain must end in the documented 503.
    const payload = await hdResp.json();
    check("HD failure falls through to {browser_tts}",
          hdResp.status === 503 && payload?.detail?.browser_tts === true,
          `${hdResp.status} ${JSON.stringify(payload?.detail?.message)}`);
  }
  const browserOnly = await ttsFetch({ text: "Browser voices.", voice: "nova",
                                       provider: "browser" });
  const browserPayload = browserOnly.json ? await browserOnly.json() : {};
  check("provider=browser skips the servers",
        browserOnly.status === 503 && browserPayload?.detail?.browser_tts === true,
        `${browserOnly.status}`);

  // ── new settings flags round-trip against the live server ──────
  const livePrefs = await window.Aether.api("/api/settings/prefs");
  check("live server exposes the voice prefs",
        livePrefs.voice_hd === true && livePrefs.voice_language === "auto"
        && livePrefs.exam?.enabled === false,
        JSON.stringify(livePrefs).slice(0, 120));
  const storedPrefs = await window.Aether.api("/api/settings/prefs", {
    method: "PUT",
    body: { voice_language: "uz", voice_name: "uz-UZ-SardorNeural",
            allow_strong_language: true, exam: { enabled: true, preset: "ielts", topic: "" } },
  });
  check("live prefs persist what was sent",
        storedPrefs.voice_language === "uz" && storedPrefs.voice_name === "uz-UZ-SardorNeural"
        && storedPrefs.allow_strong_language === true && storedPrefs.exam.enabled === true,
        JSON.stringify(storedPrefs.exam));
  await window.Aether.api("/api/settings/prefs", {
    method: "PUT",
    body: { voice_language: "auto", voice_name: "", allow_strong_language: false,
            exam: { enabled: false, preset: "ielts", topic: "" } },
  });

  // ── images pane: real request, graceful failure ─────────────────
  $("#nav-images").click();
  $("#image-prompt").value = "a small blue hexagon on dark charcoal";
  $("#image-go").click();
  const imageOutcome = await until(() =>
    window.document.querySelector("#image-stage img") ||
    window.document.querySelector("#image-results .image-failed"), 90000);
  check("image generation either renders or reports clearly", imageOutcome);
  check("no 'via Pollinations' copy anywhere in the UI",
        !(window.document.body.textContent || "").toLowerCase().includes("via pollinations"));

  // ── presentations: real plan/slide calls ────────────────────────
  $("#nav-slides").click();
  $("#slide-topic").value = "Three facts about honey bees";
  $("#slide-count").value = "3";
  $("#slide-go").click();
  const deckOutcome = await until(() =>
    window.document.querySelectorAll(".slide-row").length > 0 ||
    window.document.querySelector("#slides-pane .error-card"), 60000);
  check("deck plan reached the UI", deckOutcome,
        `${window.document.querySelectorAll(".slide-row").length} rows`);

  // ── settings + theme + export ───────────────────────────────────
  $("#nav-settings").click();
  await until(() => ($("#usage-detail")?.textContent || "").includes("300"), 8000);
  check("settings shows the usage meter", ($("#usage-detail")?.textContent || "").includes("300"));
  check("settings shows the free-tier caveat",
        (window.document.body.textContent || "").toLowerCase().includes("short waits"));
  check("settings keeps user personas intact", Boolean($("#persona-list")));

  // ── conversations API round-trip through the UI ─────────────────
  const conv = await window.Aether.api("/api/conversations", { method: "POST", body: { title: "Smoke" } });
  check("new conversation created", Boolean(conv.id));
  await window.Aether.api(`/api/conversations/${conv.id}`, { method: "DELETE" }).then(
    () => check("conversation deleted", true),
    (e) => check("conversation deleted", false, JSON.stringify(e.detail || e.message)));

  // ── logout ──────────────────────────────────────────────────────
  window.Aether.session.clear();
  check("token cleared on logout", !window.localStorage.getItem("aether_token"));

  check("no runtime errors during the live run", errors.length === 0, errors.join(" | "));
  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join(", ")}` : "\nall live checks passed");
  process.exit(failures.length ? 1 : 0);
}

run().catch((error) => {
  console.error("harness crashed:", error);
  process.exit(1);
});
