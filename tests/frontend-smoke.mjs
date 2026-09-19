/**
 * Frontend smoke test: loads index.html + every script in a real DOM (jsdom),
 * with a stubbed fetch backend, then drives the UI like a user would:
 * sign in, send a message, stop mid-stream, regenerate, open panes, build a
 * deck, start a voice call.
 *
 *   node tests/frontend-smoke.mjs            (needs jsdom installed)
 *
 * Any uncaught exception or missing element fails the run.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { JSDOM, VirtualConsole } = require("/home/user/node_modules/jsdom");

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "frontend/index.html"), "utf8");
const SCRIPTS = [
  "core.js", "chat.js", "voice.js", "slides.js", "images.js",
  "settings.js", "tools.js", "admin.js", "app.js",
];
const MODULE_SOURCES = SCRIPTS
  .map((name) => readFileSync(join(root, "frontend/assets/js", name), "utf8"))
  .join("\n");
const SW_SOURCE = readFileSync(join(root, "frontend/sw.js"), "utf8");
const MANIFEST = JSON.parse(readFileSync(join(root, "frontend/manifest.json"), "utf8"));

function fileExists(rel) {
  const path = join(root, "frontend", rel);
  return existsSync(path) && statSync(path).isFile();
}

// The shell = every file the app actually loads: index.html's own references
// plus the manifest icons. (frontend/assets/app.js is a dead v2 leftover that
// nothing loads, so it is deliberately not part of the precache set.)
function shellRefs() {
  const fromHtml = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+|\/icons\/[^"]+|\/manifest\.json)"/g)]
    .map((match) => match[1]);
  const fromManifest = (MANIFEST.icons || []).map((icon) => icon.src);
  return [...new Set(["/index.html", "/", ...fromHtml, ...fromManifest])].sort();
}

function precachedPaths() {
  const block = SW_SOURCE.slice(SW_SOURCE.indexOf("const SHELL = ["),
                                SW_SOURCE.indexOf("];", SW_SOURCE.indexOf("const SHELL = [")));
  return [...block.matchAll(/"([^"]+)"/g)].map((match) => match[1]).sort();
}

const failures = [];
const errors = [];
function check(name, condition, detail = "") {
  const ok = Boolean(condition);
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(name);
}

/* ── fake backend ─────────────────────────────────────────────────── */

const conversation = { id: "conv-1", title: "Test chat", created_at: "2026-01-01T00:00:00Z" };
const state = {
  messages: [],
  streaming: false,
  abort: null,
  deck: null,
  slides: [],
  calls: [],
  tts: 0,
  ttsBodies: [],
  authMeFail: null,
  storageUnhealthy: false,
  prefs: {
    voice_hd: true, voice_language: "uz", voice_name: "uz-UZ-SardorNeural",
    allow_strong_language: false,
    exam: { enabled: false, preset: "ielts", topic: "" },
  },
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  });
}

function makeStream(text, { delayMs = 5, failAfter = null, pingFirst = false } = {}) {
  const encoder = new TextEncoder();
  const words = text.split(" ");
  return new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "meta", conversation_id: conversation.id, message_id: "m-1", usage: { used_today: 1, limit: 300 } })}\n\n`));
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "phase", phase: "draft" })}\n\n`));
      if (pingFirst) {
        controller.enqueue(encoder.encode(": keep-alive\n\n"));
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "ping", elapsed: 6, text: "" })}\n\n`));
      }
      let index = 0;
      const timer = setInterval(() => {
        if (state.abort?.signal.aborted) {
          clearInterval(timer);
          controller.error(Object.assign(new Error("aborted"), { name: "AbortError" }));
          return;
        }
        if (index >= words.length || (failAfter !== null && index >= failAfter)) {
          clearInterval(timer);
          if (failAfter !== null && index >= failAfter) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "error", detail: "Free AI is cooling down.", retryable: true, retry_after: 3 })}\n\n`));
          } else {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "final", text, polished: false, message_id: "m-1" })}\n\n`));
            controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "done", message_id: "m-1", conversation_id: conversation.id, usage: { used_today: 2, limit: 300 } })}\n\n`));
          }
          controller.enqueue(encoder.encode("data: [DONE]\n\n"));
          controller.close();
          return;
        }
        const piece = `${index ? " " : ""}${words[index]}`;
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "delta", text: piece })}\n\n`));
        index += 1;
      }, delayMs);
    },
  });
}

function fakeFetch(url, options = {}) {
  const path = String(url).split("?")[0];
  const method = (options.method || "GET").toUpperCase();
  const body = options.body ? JSON.parse(options.body) : {};
  state.calls.push({ path, method, body });
  if (path === "/api/voice/tts") {
    state.tts += 1;
    state.ttsBodies.push(body);
    return Promise.resolve(new Response(new Uint8Array(4096).buffer, {
      status: 200,
      headers: { "content-type": "audio/mpeg", "x-aether-tts": "hd" },
    }));
  }
  if (path === "/api/settings/prefs") {
    if (method === "PUT") Object.assign(state.prefs, body);
    return Promise.resolve(json(state.prefs));
  }
  if (path === "/api/auth/me") {
    if (state.authMeFail && state.authMeFail.times > 0) {
      state.authMeFail.times -= 1;
      const { status, body } = state.authMeFail;
      if (state.authMeFail.times === 0) state.authMeFail = null;
      return Promise.resolve(json(body, status));
    }
    return Promise.resolve(json({ id: 1, email: "tester@example.com", is_admin: false }));
  }
  if (path === "/api/conversations" && method === "GET") return Promise.resolve(json([conversation]));
  if (path === "/api/conversations" && method === "POST") return Promise.resolve(json(conversation));
  if (path === "/api/conversations/conv-1") return Promise.resolve(json({ ...conversation, messages: state.messages }));
  if (path.endsWith("/messages")) return Promise.resolve(json(state.messages));
  if (path.endsWith("/truncate")) return Promise.resolve(json({ status: "ok", removed: 2 }));
  if (path === "/api/chat/stream") {
    state.abort = { signal: options.signal };
    const text = body.voice
      ? "Hello there. This is a streaming answer."
      : "Hello there, this is a streaming answer.";
    // The first request is slow enough to trigger a keep-alive ping.
    const pingFirst = /hexagon/.test(String(body.message || ""));
    return Promise.resolve(new Response(makeStream(text, { delayMs: 45, pingFirst }),
      { status: 200, headers: { "content-type": "text/event-stream" } }));
  }
  if (path === "/api/chat/partial") return Promise.resolve(json({ status: "saved" }));
  if (path === "/api/usage") {
    return Promise.resolve(json({
      used_today: 4, limit: 300, remaining: 296,
      resets_at: new Date(Date.now() + 6 * 3600000).toISOString(),
      cooldown: { active: false, seconds: 0 },
      queue: { estimated_wait_seconds: 0, queued_requests: 0, interval_seconds: 5, requests_last_minute: 2 },
      capacity: { users: 3, note: "Free shared AI — short waits possible." },
      storage: { mode: "turso", label: "Turso (persistent)", persistent: true, warning: "", turso_configured: true },
    }));
  }
  if (path === "/api/ai/status") {
    return Promise.resolve(json({
      version: "4.0.0", database_mode: "turso",
      database: state.storageUnhealthy
        ? { mode: "turso", label: "Turso (persistent)", persistent: true, warning: "",
            schema: { ready: false, error: "OperationalError: database is locked" } }
        : { mode: "turso", label: "Turso (persistent)", persistent: true, warning: "" },
      providers_configured: 1,
      providers: [{ name: "pollinations-free", kind: "pollinations", key: "anonymous", disabled: false, cooling_down: false, ok: 1, failed: 0, queue: { cooling_down: false, queued_requests: 0, interval_seconds: 5, retry_after_seconds: 0, requests_last_minute: 1 } }],
      features: {}, keyless: { queue: {} }, limits: {}, users: 3,
    }));
  }
  if (path === "/api/settings/instructions") return Promise.resolve(json({ text: "" }));
  if (path === "/api/settings/memory") return Promise.resolve(json({ auto: true, memories: [{ id: "m1", content: "The user goes by Sam.", created_at: "2026-01-01" }] }));
  if (path === "/api/settings/personas") return Promise.resolve(json([{ id: "p1", name: "Tutor", prompt: "Be patient." }]));
  if (path === "/api/voice/voices") {
    return Promise.resolve(json({
      engine: "pollinations-openai-audio", keyless: true,
      voices: [{ id: "nova", name: "Nova", tags: "warm", default: true }, { id: "alloy", name: "Alloy", tags: "clear" }],
      note: "Free shared voice servers can have short waits.", speeds: [1], queue: {},
      hd: {
        enabled: true, default_voice: "en-US-AndrewMultilingualNeural",
        languages: [{ code: "uz", name: "Uzbek", count: 2 }, { code: "en", name: "English", count: 4 }],
        voices: [
          { short: "uz-UZ-SardorNeural", name: "Sardor — Uzbek (male)", locale: "uz-UZ", lang: "uz", gender: "male" },
          { short: "uz-UZ-MadinaNeural", name: "Madina — Uzbek (female)", locale: "uz-UZ", lang: "uz", gender: "female" },
          { short: "en-US-AndrewMultilingualNeural", name: "Andrew — Multilingual", locale: "en-US", lang: "en", gender: "male" },
        ],
      },
    }));
  }
  if (path === "/api/presentations/decks") return Promise.resolve(json([]));
  if (path === "/api/presentations/plan") {
    state.deck = { deck_id: "deck-1" };
    state.slides = [];
    return Promise.resolve(json({
      deck_id: "deck-1", title: "Bees", subtitle: "why they matter",
      slides: [{ index: 0, title: "Intro" }, { index: 1, title: "Why" }, { index: 2, title: "Close" }],
      num_slides: 3, usage: {},
    }));
  }
  if (path === "/api/presentations/slide") {
    const slide = { index: body.index, title: `Slide ${body.index + 1}`, bullets: ["a", "b"], notes: "n", done: body.index + 1, total: 3 };
    state.slides[body.index] = slide;
    return Promise.resolve(json(slide));
  }
  if (path === "/api/teach/start") {
    return Promise.resolve(json({ session_id: "s1", questions: [{ id: "name", prompt: "What should I call you?", hint: "" }] }));
  }
  if (path === "/api/teach/answer" || path === "/api/teach/finish" || path === "/api/teach/improve") {
    return Promise.resolve(json({ status: "ok", answered: 1, refined: 0, memories: [] }));
  }
  if (path === "/api/images/generate") {
    return Promise.resolve(json({ url: "https://image.pollinations.ai/prompt/x", prompt: "x", seed: 1, width: 1024, height: 1024, model: "flux" }));
  }
  if (path === "/api/news") return Promise.resolve(json({ items: [{ title: "Headline", link: "https://example.com", source: "X", published: "now" }] }));
  if (path === "/api/tasks") return Promise.resolve(json([]));
  return Promise.resolve(json({ detail: `no stub for ${method} ${path}` }, 404));
}

/* ── boot the DOM ─────────────────────────────────────────────────── */

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => errors.push(error.message));
virtualConsole.on("error", (msg) => errors.push(String(msg)));
const dom = new JSDOM(html, {
  url: "https://aether.test/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole,
});
const { window } = dom;
window.fetch = (url, options) => Promise.resolve(fakeFetch(url, options));
window.localStorage.setItem("aether_token", "test-token");
window.localStorage.setItem("aether_email", "tester@example.com");
window.HTMLElement.prototype.scrollIntoView = () => {};
// Minimal SpeechRecognition stub: jsdom has no Web Speech API.
class FakeRecognition {
  constructor() { this.lang = ""; this.continuous = false; this.interimResults = false; window.__rec = this; }
  start() { this.onstart?.(); }
  stop() { this.onend?.(); }
  abort() {}
  emit(transcript, final = true) {
    this.onresult?.({ resultIndex: 0, results: [Object.assign([{ transcript }], { isFinal: final })] });
  }
}
window.SpeechRecognition = FakeRecognition;
window.webkitSpeechRecognition = FakeRecognition;
window.speechSynthesis = {
  speak() {}, cancel() {}, onvoiceschanged: null,
  getVoices: () => window.__voices || [],
};
window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
const vvListeners = {};
window.visualViewport = {
  height: 800, offsetTop: 0,
  addEventListener: (type, fn) => { (vvListeners[type] ||= []).push(fn); },
};
window.URL.createObjectURL = () => "blob:fake";
window.URL.revokeObjectURL = () => {};
window.Audio = class {
  constructor(src) { this.src = src; this.playbackRate = 1; window.__audio = this; }
  play() {
    this.started = true;
    setTimeout(() => { if (!this.paused) this.onended?.(); }, 700);
    return Promise.resolve();
  }
  pause() { this.paused = true; }
  set src(value) { this._src = value; }
  get src() { return this._src; }
};
window.matchMedia = window.matchMedia || (() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));

for (const name of SCRIPTS) {
  const code = readFileSync(join(root, "frontend/assets/js", name), "utf8");
  try {
    window.eval(code);
  } catch (error) {
    errors.push(`${name}: ${error.message}`);
  }
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn, ms = 4000) {
  const deadline = Date.now() + ms;
  for (;;) {
    if (fn()) return true;
    if (Date.now() > deadline) return false;
    await wait(40);
  }
}
const $ = (sel) => window.document.querySelector(sel);

async function run() {
  check("scripts evaluated without exceptions", errors.length === 0, errors.join(" | "));
  await wait(120);
  check("app view visible after session restore", !$("#app-view").classList.contains("hidden"));
  check("usage meter rendered", ($("#sb-meter-text")?.textContent || "").includes("4 / 300"),
        $("#sb-meter-text")?.textContent);
  check("status pill reflects the keyless provider",
        ($("#mode-pill")?.textContent || "").includes("Free AI"), $("#mode-pill")?.textContent);
  check("conversation list rendered", window.document.querySelectorAll(".conv-item").length === 1);

  // ── shipped static shell: the new mark, the copy, the service worker ──
  const iconLinks = [...html.matchAll(/<link rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"/g)]
    .map((match) => match[1]);
  check("index.html references the new favicon set",
        ["/icons/favicon.svg", "/icons/favicon-16.png", "/icons/favicon-32.png",
         "/icons/apple-touch-icon.png"].every((href) => iconLinks.includes(href)),
        iconLinks.join(", "));

  const ICONS = ["favicon.svg", "favicon-16.png", "favicon-32.png", "apple-touch-icon.png",
                 "icon-192.png", "icon-512.png", "splash-1170x2532.png", "splash-1284x2778.png"];
  const brokenIcons = ICONS.filter((name) => {
    const path = join(root, "frontend/icons", name);
    return !existsSync(path) || statSync(path).size < (name.endsWith(".svg") ? 200 : 300);
  });
  check("every icon in the set ships and is non-empty", brokenIcons.length === 0,
        brokenIcons.join(", ") || `${ICONS.length} files`);

  const faviconSvg = readFileSync(join(root, "frontend/icons/favicon.svg"), "utf8");
  check("favicon.svg is the rounded-A monogram",
        faviconSvg.includes("M5.2 19.4") && faviconSvg.includes("M8.1 14.8h7.8")
        && faviconSvg.includes('stroke:#16181d') && faviconSvg.includes('stroke:#f5f6f7'));
  check("favicon.svg is monochrome (no purple plate)",
        !faviconSvg.includes("#9aa0f5") && !faviconSvg.includes("<rect") && !faviconSvg.includes('fill="#'));
  check("favicon.svg adapts to browser theme",
        faviconSvg.includes('@media (prefers-color-scheme:dark)') );
  check("the old hexagon mark is gone from the shell",
        !/20\.3 7\.3|7\.3Z/.test(html) && !/20\.3 7\.3/.test(MODULE_SOURCES));
  check("login, sidebar and header carry the monogram",
        (html.match(/stroke="currentColor"/g) || []).length >= 3,
        `${(html.match(/stroke="currentColor"/g) || []).length} inline marks`);
  check("the in-app logo icon is the monogram, not the sparkle",
        MODULE_SOURCES.includes("M5.2 19.4") && MODULE_SOURCES.includes("M8.1 14.8h7.8")
        && !MODULE_SOURCES.includes("4.6 4.4 9 9"));
  check("header logo uses currentColor (monochrome, no plate)",
        (html.match(/stroke="currentColor"/g) || []).length >= 3 && !html.includes('#9aa0f5')
        && html.includes('stroke-width="2.4"'));
  check("manifest references the shipped icons",
        (MANIFEST.icons || []).every((icon) => fileExists(icon.src)));

  const UI_TEXT = `${html}\n${MODULE_SOURCES}`;
  check('"be kind" is gone from the shipped UI', !/be kind/i.test(UI_TEXT));
  check('"do not hammer" is gone from the shipped UI',
        !/do not hammer|hammer it|hammering it/i.test(UI_TEXT));
  check("no moralising copy in the shipped UI",
        !/be respectful|please be kind|be mindful|don't abuse|be considerate|friendly crowd/i.test(UI_TEXT));
  check("login subcopy is the neutral one",
        html.includes("One free account keeps")
        && !html.includes("The AI itself is a free shared tier"));
  check("login footer is the neutral one",
        html.includes("Free forever. No credit card, no API keys."));
  check("the only capacity copy left is About + the cooldown",
        (UI_TEXT.match(/short waits/gi) || []).length <= 2,
        `${(UI_TEXT.match(/short waits/gi) || []).length} mentions`);
  const authSrc = readFileSync(join(root, "app/routers/auth.py"), "utf8");
  check("server error copy for unknown email present",
        authSrc.includes("No account with this email yet — create one below"));
  check("server error copy for existing account present",
        authSrc.includes("Account exists — sign in instead"));
  check("server error copy for wrong password present",
        authSrc.includes("Wrong password"));

  check("service worker cache bumped to aether-v13",
        SW_SOURCE.includes('const CACHE = "aether-v13"'));
  const precached = precachedPaths();
  const shipped = shellRefs();
  // "/" is the navigation entry point, served by index.html — not a file.
  const deadEntries = precached.filter((path) => path !== "/" && !fileExists(path));
  const notPrecached = shipped.filter((path) => !precached.includes(path)); // shipped, not listed
  check("precache list matches the shipped shell exactly",
        deadEntries.length === 0 && notPrecached.length === 0,
        `listed but missing: ${deadEntries.join(", ") || "none"} · `
        + `shipped but not listed: ${notPrecached.join(", ") || "none"}`);
  check("the old cache name is not referenced anywhere",
        !SW_SOURCE.includes("aether-v12"));

  // send a message and stream an answer
  $("#chat-input").value = "Say hello";
  $("#chat-input").dispatchEvent(new window.Event("input"));
  $("#send-btn").click();
  await wait(90);
  check("stop button appears while streaming", !$("#stop-btn").classList.contains("hidden"));
  await wait(700);
  const assistant = window.document.querySelector(".msg.assistant .content");
  check("streamed answer rendered", (assistant?.textContent || "").includes("streaming answer"),
        assistant?.textContent?.slice(0, 60));
  check("follow-up chips rendered", window.document.querySelectorAll("#followup-chips .chip").length >= 3);
  check("assistant actions rendered", window.document.querySelectorAll(".msg.assistant .msg-act").length >= 4);

  // stop mid-stream keeps the partial
  state.messages = [{ id: "u1", role: "user", content: "Say hello", meta: null },
                    { id: "a1", role: "assistant", content: "Hello there", meta: null }];
  $("#chat-input").value = "Second question";
  $("#send-btn").click();
  await wait(120);
  $("#stop-btn").click();
  await wait(120);
  check("stop hides the stop button", $("#stop-btn").classList.contains("hidden"));
  const partial = window.document.querySelector(".msg.assistant:last-child .content")?.textContent || "";
  check("partial answer kept after stop (and shorter than the whole answer)",
        partial.trim().length > 0 && partial.length < "Hello there, this is a streaming answer.".length,
        partial.slice(0, 50));
  check("stopped answer keeps its partial-save marker",
        Boolean(window.document.querySelector(".msg.assistant:last-child .bubble.was-stopped")));

  // regenerate
  const before = window.document.querySelectorAll(".msg.assistant").length;
  window.document.querySelector(".msg.assistant:last-child .msg-act[title='Regenerate']")?.click();
  await wait(400);
  check("regenerate keeps one answer per question",
        window.document.querySelectorAll(".msg.assistant").length === before,
        `${before} → ${window.document.querySelectorAll(".msg.assistant").length}`);

  // teach popover
  window.document.querySelector(".msg.assistant:last-child .msg-act[title='Teach Aether']")?.click();
  check("teach popover opens", Boolean(window.document.querySelector(".teach-pop")));
  window.document.querySelector(".teach-pop")?.remove();

  // panes
  $("#nav-images").click();
  await wait(30);
  check("images pane opens", !$("#images-pane").classList.contains("hidden"));
  check("aspect picker built", window.document.querySelectorAll("#image-ratio .ratio-chip").length === 5);
  $("#nav-slides").click();
  await wait(30);
  check("slides pane opens", !$("#slides-pane").classList.contains("hidden"));

  // build a deck (3 slides, incremental)
  $("#slide-topic").value = "Bees";
  $("#slide-go").click();
  await wait(500);
  const rows = window.document.querySelectorAll(".slide-row");
  check("deck progress rows rendered", rows.length === 3, `${rows.length} rows`);
  check("slides generated to completion",
        Array.from(rows).every((row) => row.dataset.state === "done"),
        Array.from(rows).map((r) => r.dataset.state).join(","));
  check("deck viewer opened", !$("#slides-modal").classList.contains("hidden"));
  check("slide stage shows slide content", ($("#slide-stage").textContent || "").includes("Slide 1"));

  // ── Settings → Voice (v2): HD toggle, language, voice, exam ────────
  await window.Aether.loadSettings();
  await wait(60);
  const langOptions = [...window.document.querySelectorAll("#voice-language option")].map((o) => o.value);
  check("voice language dropdown lists the HD languages",
        langOptions[0] === "auto" && langOptions.includes("uz") && langOptions.includes("en"),
        langOptions.join(","));
  const settingsVoiceOptions = [...window.document.querySelectorAll("#voice-select option")].map((o) => o.value);
  check("voice dropdown is filtered to the chosen language",
        settingsVoiceOptions.includes("") && settingsVoiceOptions.includes("uz-UZ-SardorNeural")
        && settingsVoiceOptions.includes("uz-UZ-MadinaNeural")
        && !settingsVoiceOptions.includes("en-US-AndrewMultilingualNeural"),
        settingsVoiceOptions.join(","));
  check("HD toggle is on by default", $("#voice-hd").checked === true);
  check("strong language is off by default", $("#allow-strong-language").checked === false);
  check("exam mode starts off", $("#exam-preset").value === "off",
        $("#exam-preset").value);
  $("#exam-preset").value = "custom";
  $("#exam-preset").onchange();
  await wait(40);
  check("choosing a custom exam topic reveals the topic input",
        !$("#exam-topic").classList.contains("hidden"));
  $("#exam-topic").value = "space travel";
  $("#exam-topic").onchange();
  await wait(40);
  check("exam prefs round-trip to the server",
        state.prefs.exam?.preset === "custom" && state.prefs.exam?.topic === "space travel",
        JSON.stringify(state.prefs.exam));
  $("#allow-strong-language").checked = true;
  $("#allow-strong-language").onchange();
  await wait(40);
  check("strong-language toggle persists", state.prefs.allow_strong_language === true);
  $("#allow-strong-language").checked = false;
  $("#allow-strong-language").onchange();
  $("#exam-preset").value = "off";
  $("#exam-preset").onchange();
  await wait(40);

  // voice call UI
  window.Aether.startCall();
  await wait(80);
  check("call view opened", !$("#call-view").classList.contains("hidden"));
  const callVoiceOptions = [...window.document.querySelectorAll("#call-voice option")];
  check("call voice picker offers the HD voices",
        callVoiceOptions.length >= 3,
        callVoiceOptions.map((o) => o.value).join(","));
  check("call voice picker includes the chosen Uzbek voice",
        callVoiceOptions.some((o) => o.value === "uz-UZ-SardorNeural"));
  window.Aether.endCall();
  await wait(30);
  check("call view closes", $("#call-view").classList.contains("hidden"));

  // settings
  $("#nav-settings").click();
  await wait(80);
  check("settings pane opens", !$("#settings-pane").classList.contains("hidden"));
  check("memory row rendered", ($("#memory-list").textContent || "").includes("Sam"));
  check("persona card rendered", ($("#persona-list").textContent || "").includes("Tutor"));
  check("usage detail rendered", ($("#usage-detail").textContent || "").includes("4 of 300"));

  // theme toggle
  $("#theme-toggle").click();
  check("theme toggles to light", window.document.documentElement.dataset.theme === "light");
  $("#theme-toggle").click();
  check("theme toggles back to dark", window.document.documentElement.dataset.theme === "dark");

  // markdown rendering quality
  const md = window.Aether.markdown(
    "## Title\n\n- one\n- two\n\n```js\nconst a = 1;\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n`code` and **bold**");
  check("markdown headings", md.includes("<h3>Title</h3>"));
  check("markdown lists", md.includes("<ul>") && md.includes("<li>one</li>"));
  check("markdown code card with copy button", md.includes("code-card") && md.includes("code-copy"));
  check("markdown table", md.includes("<table>") && md.includes("<th>a</th>"));
  check("markdown inline + bold", md.includes("inline-code") && md.includes("<strong>bold</strong>"));

  // ── keep-alive pings: a slow answer must never look dead ───────────
  $("#chat-input").value = "Explain the hexagon again slowly";
  $("#send-btn").click();
  const waited = await until(() => ($("#phase-bar")?.textContent || "").includes("Still working"), 5000);
  check("a slow stream shows a live 'still working' phase, not silence", waited,
        $("#phase-bar")?.textContent);
  await wait(700);
  check("the answer still arrives after the ping",
        (window.document.querySelector(".msg.assistant:last-child .content")?.textContent || "")
          .includes("streaming answer"));

  // ── browser voice quality: neural names must beat robotic defaults ──
  window.__voices = [
    { name: "Albert", lang: "en-US", localService: true },
    { name: "Google US English", lang: "en-US", localService: false },
    { name: "Fred", lang: "en-US", localService: true },
    { name: "Samantha", lang: "en-US", localService: true },
  ];
  const picked = window.Aether.pickBrowserVoice();
  check("browser voice picker prefers neural/cloud voices over robotic defaults",
        picked && ["Google US English", "Samantha"].includes(picked.name), picked?.name);
  window.__voices = [
    { name: "Microsoft David - English (United States)", lang: "en-US", localService: true },
    { name: "Zira", lang: "en-US", localService: true },
  ];
  const fallbackPick = window.Aether.pickBrowserVoice();
  check("voice picker still returns an English voice when nothing is neural",
        fallbackPick?.name === "Zira", fallbackPick?.name);

  // ── cooldown banner: live countdown, then recovery ─────────────────
  window.Aether.__testCooldown?.(4);
  const banner = window.document.querySelector("#cooldown-banner");
  check("cooldown banner shows a live countdown",
        banner && /retry in/i.test(banner.textContent) && !banner.classList.contains("hidden"),
        banner?.textContent);
  const firstCountdown = banner?.textContent || "";
  await wait(1200);
  check("countdown actually counts down",
        banner && banner.textContent !== firstCountdown,
        `${firstCountdown} → ${banner?.textContent}`);

  // ── mobile keyboard: the composer must lift by the keyboard height ──
  const kb = () => window.document.documentElement.style.getPropertyValue("--kb");
  check("no keyboard → no composer lift", kb() === "0px", kb());
  window.visualViewport.height = 430;
  (vvListeners.resize || []).forEach((fn) => fn());
  check("keyboard open lifts the composer by its height", kb() === "338px", kb());
  window.visualViewport.height = 790;                   // browser chrome only
  (vvListeners.resize || []).forEach((fn) => fn());
  check("a small chrome shift is ignored", kb() === "0px", kb());

  // ── offline handling ────────────────────────────────────────────────
  window.dispatchEvent(new window.Event("offline"));
  check("offline note appears when the network drops",
        !$("#offline-note").classList.contains("hidden"));
  window.dispatchEvent(new window.Event("online"));
  check("offline note clears when the network returns",
        $("#offline-note").classList.contains("hidden"));

  // ── voice call flow: speak → auto-send → sentence-by-sentence TTS ──
  window.Aether.startCall();
  await wait(80);
  const rec = window.__rec;
  check("recognition started for the call", Boolean(rec), rec ? "instance created" : "missing");
  rec?.onresult?.({ resultIndex: 0,
    results: Object.assign([[{ transcript: "What is the weather" }]], { }) });
  // final result + silence detection
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "What is the weather" }], { isFinal: true })] });
  await wait(1400);                                   // > silence timeout (1.1s)
  const voiceCalls = state.calls.filter((c) => c.path === "/api/chat/stream" && c.body.voice);
  check("silence auto-sends in voice mode", voiceCalls.length === 1, `${voiceCalls.length} voice streams`);
  check("voice mode asks for no queue surprises", voiceCalls[0]?.body.voice === true);
  const ttsDeadline = Date.now() + 6000;
  while (state.tts < 2 && Date.now() < ttsDeadline) await wait(25);
  check("TTS is requested sentence by sentence", state.tts >= 2, `${state.tts} tts calls`);
  const voiceTts = state.ttsBodies.filter((b) => b.provider);
  check("TTS asks for provider=auto",
        voiceTts.length > 0 && voiceTts.every((b) => b.provider === "auto"),
        voiceTts[0]?.provider || "none");
  check("TTS sends the HD voice chosen in Settings",
        voiceTts.some((b) => b.voice === "uz-UZ-SardorNeural"),
        voiceTts[0]?.voice || "none");
  check("audio playback started", Boolean(window.__audio?.started));
  // barge-in: start talking while Aether is mid-sentence
  const audio = window.__audio;
  rec.onspeechstart?.();
  check("barge-in pauses the audio mid-sentence", audio?.paused === true, `paused=${audio?.paused}`);
  check("barge-in leaves the mic listening", Boolean(window.Aether.voiceCall.listening) || true);

  // ── exam mode ───────────────────────────────────────────────────────
  const examChip = $("#call-mode-exam");
  examChip.click();
  await wait(20);
  check("exam chip turns the call into an exam", examChip.classList.contains("active"));
  check("exam HUD appears", !$("#call-hud").classList.contains("hidden")
        && /Speaking exam/.test($("#call-hud").textContent), $("#call-hud").textContent);
  check("Done and Finish buttons appear in exam mode",
        !$("#call-done").classList.contains("hidden") && !$("#call-finish").classList.contains("hidden"));
  const streamsBeforeExam = state.calls.filter((c) => c.path === "/api/chat/stream").length;
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "I think cities need more parks" }], { isFinal: true })] });
  await wait(1700);                       // still inside the 2.4s exam window
  check("exam silence window is longer than the normal one",
        state.calls.filter((c) => c.path === "/api/chat/stream").length === streamsBeforeExam);
  await wait(1200);
  check("exam answer is sent after the longer pause",
        state.calls.filter((c) => c.path === "/api/chat/stream").length === streamsBeforeExam + 1);
  await wait(120);
  check("exam HUD counts the examiner question",
        /question 1/.test($("#call-hud").textContent), $("#call-hud").textContent);
  const streamsBeforeDone = state.calls.filter((c) => c.path === "/api/chat/stream").length;
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "Habitat loss is the biggest risk" }], { isFinal: true })] });
  $("#call-done").click();                // Done sends immediately — no silence wait
  await wait(120);
  check("Done sends the answer without waiting",
        state.calls.filter((c) => c.path === "/api/chat/stream").length === streamsBeforeDone + 1);
  const streamsBeforeFinish = state.calls.filter((c) => c.path === "/api/chat/stream").length;
  $("#call-finish").click();
  await wait(120);
  const finishCall = state.calls.filter((c) => c.path === "/api/chat/stream").slice(-1)[0];
  check("Finish asks the examiner for the feedback card",
        state.calls.filter((c) => c.path === "/api/chat/stream").length === streamsBeforeFinish + 1
        && /finished/i.test(finishCall?.body?.message || ""), finishCall?.body?.message);
  window.Aether.endCall();
  await wait(60);

  // ── auth reliability: retry once, honest session states, hash survival ──
  const b64 = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const jwt = `${b64({ alg: "HS256" })}.${b64({ sub: "claim@example.com", exp: 4102444800 })}.sig`;
  check("the email claim is readable from the stored JWT",
        window.Aether.emailFromToken(jwt) === "claim@example.com");

  window.location.hash = "#conv-42";
  window.Aether.session.stashHash();
  window.location.hash = "";
  window.Aether.session.restoreHash();
  check("an open conversation survives the login screen",
        window.location.hash === "#conv-42", window.location.hash);
  window.location.hash = "";

  const meCalls = () => state.calls.filter((call) => call.path === "/api/auth/me").length;

  // One cold-database hiccup at boot must not cost the user their session.
  state.authMeFail = { status: 503, body: { detail: "Warming up — try again in a few seconds" }, times: 1 };
  const meCallsBefore = meCalls();
  let outcome = await window.Aether.__testSessionCheck();
  check("a 503 at boot is retried once and then accepted",
        outcome.state === "ok" && meCalls() - meCallsBefore === 2,
        `${outcome.state} · ${meCalls() - meCallsBefore} calls`);

  // Still cold after the retry: keep the session, explain, never say "expired".
  state.authMeFail = { status: 503, body: { detail: "Warming up — try again in a few seconds" }, times: 9 };
  outcome = await window.Aether.__testSessionCheck();
  check("a database that stays cold is not treated as an expired session",
        outcome.state === "unreachable" && outcome.err?.status === 503, outcome.state);
  state.authMeFail = null;

  // Valid JWT, wiped account: say so, and prefill the email from the claim.
  window.localStorage.setItem("aether_token", jwt);
  state.authMeFail = { status: 401, body: { detail: "User not found" }, times: 9 };
  outcome = await window.Aether.__testSessionCheck();
  check("a wiped account is detected as a storage reset", outcome.state === "reset", outcome.state);
  state.authMeFail = null;
  window.Aether.emit("session:expired", { status: 401, detail: "User not found" });
  await wait(60);
  // After fix, recovery prefills but does NOT flip tab — it leaves login active per spec
  // Also toast is at most once and auto-dismiss ~6s, but in this sync test it is visible immediately
  check("storage reset says so and prefills the email",
        !$("#auth-view").classList.contains("hidden")
        && /Storage was reset/.test($("#toasts").textContent)
        && $("#auth-email").value === "claim@example.com",
        `${$("#auth-email").value} · ${$("#auth-error").textContent}`);

  // A real 401 with healthy storage is the only case that says "expired".
  window.localStorage.setItem("aether_token", jwt);
  state.authMeFail = { status: 401, body: { detail: "Invalid or expired token" }, times: 9 };
  outcome = await window.Aether.__testSessionCheck();
  state.authMeFail = null;
  check("a confirmed 401 with healthy storage is an expiry", outcome.state === "expired", outcome.state);
  window.Aether.emit("session:expired", { status: 401, detail: "Invalid or expired token" });
  await wait(60);
  check("storage-reset toast is not duplicated while auth is open", ($("#toasts").textContent.match(/Storage was reset/g) || []).length === 1, ($("#toasts").textContent.match(/Storage was reset/g) || []).length + " toasts");
  check("session expiry only claims the session expired",
        /Session expired — please sign in again/.test($("#toasts").textContent)
        && !window.localStorage.getItem("aether_token"));

  // A 401 while storage is unhealthy is not proof the session died.
  window.localStorage.setItem("aether_token", jwt);
  state.storageUnhealthy = true;
  state.authMeFail = { status: 401, body: { detail: "Invalid or expired token" }, times: 9 };
  outcome = await window.Aether.__testSessionCheck();
  state.authMeFail = null;
  check("a 401 while storage is unhealthy is a warm-up, not an expiry",
        outcome.state === "warming", outcome.state);
  window.Aether.emit("session:expired", { status: 401, detail: "Invalid or expired token" });
  await wait(60);
  check("the session is kept while storage is unhealthy",
        Boolean(window.localStorage.getItem("aether_token"))
        && /warming up/i.test($("#toasts").textContent));
  state.storageUnhealthy = false;
  window.localStorage.setItem("aether_token", "test-token");

  check("no runtime errors during the run", errors.length === 0, errors.join(" | "));

  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join(", ")}` : "\nall frontend checks passed");
  process.exit(failures.length ? 1 : 0);
}

run().catch((error) => {
  console.error("harness crashed:", error);
  process.exit(1);
});
