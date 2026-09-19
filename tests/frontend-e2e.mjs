/**
 * Happy-path E2E: the real app (jsdom) against the real API whose free-tier
 * providers are stubbed (tests/mock_server.py). Everything else is genuine —
 * HTTP, auth, SQLite, SSE, streaming, deck assembly, export blobs.
 *
 *   AETHER_BASE=http://127.0.0.1:8001 node tests/frontend-e2e.mjs
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { JSDOM, VirtualConsole } = require("/home/user/node_modules/jsdom");

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.AETHER_BASE || "http://127.0.0.1:8001";
const html = readFileSync(join(root, "frontend/index.html"), "utf8");
const SCRIPTS = ["core.js", "chat.js", "voice.js", "slides.js", "images.js",
                 "settings.js", "tools.js", "admin.js", "app.js"];

const failures = [];
const errors = [];
function check(name, ok, detail = "") {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(name);
}

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => errors.push(e.message));
virtualConsole.on("error", (m) => errors.push(String(m)));

process.on("unhandledRejection", (reason) => {
  errors.push(`unhandled: ${reason?.message || reason}`);
  if (process.env.AETHER_E2E_DEBUG) console.log("UNHANDLED:", reason);
});
const dom = new JSDOM(html, {
  url: `${BASE}/`, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole,
});
const { window } = dom;
const realFetch = globalThis.fetch;
window.fetch = (url, options) => {
  const target = String(url).startsWith("/") ? BASE + url : String(url);
  if (process.env.AETHER_E2E_DEBUG) console.log("  →", (options?.method || "GET"), target);
  return realFetch(target, options);
};
window.HTMLElement.prototype.scrollIntoView = () => {};
window.Audio = class {
  constructor() { window.__audio = this; }
  play() { this.started = true; setTimeout(() => this.onended?.(), 120); return Promise.resolve(); }
  pause() { this.paused = true; }
};
window.URL.createObjectURL = () => "blob:aether";
window.URL.revokeObjectURL = () => {};
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
  try { window.eval(readFileSync(join(root, "frontend/assets/js", name), "utf8")); }
  catch (error) { errors.push(`${name}: ${error.message}`); }
}

const $ = (sel) => window.document.querySelector(sel);
const $$ = (sel) => Array.from(window.document.querySelectorAll(sel));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 20000) {
  const deadline = Date.now() + ms;
  for (;;) {
    if (fn()) return true;
    if (Date.now() > deadline) return false;
    await wait(50);
  }
}
const lastAssistant = () => {
  const all = $$(".msg.assistant .content");
  return all[all.length - 1]?.textContent || "";
};

async function run() {
  check("scripts evaluated without exceptions", errors.length === 0, errors.join(" | "));
  // jsdom fires DOMContentLoaded asynchronously; the app boots on it.
  await until(() => window.document.readyState !== "loading", 5000);
  await wait(150);
  $("#tab-register").click();
  $("#auth-email").value = `e2e-${Date.now()}@example.com`;
  $("#auth-password").value = "supersecret1";
  $("#auth-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  const signedIn = await until(() => !$("#app-view").classList.contains("hidden"), 30000);
  check("signed in", signedIn,
        [$("#auth-error")?.textContent, errors.join(" | ")].filter(Boolean).join(" / "));
  if (!signedIn) { console.log("aborting: authentication failed"); process.exit(1); }

  /* ── chat streaming ───────────────────────────────────────────── */
  $("#chat-input").value = "What is the point of a hexagon?";
  $("#chat-input").dispatchEvent(new window.Event("input"));
  $("#send-btn").click();
  check("stop button shows while streaming", await until(() => !$("#stop-btn").classList.contains("hidden"), 5000));
  check("answer streams in", await until(() => lastAssistant().length > 30, 30000),
        lastAssistant().slice(0, 60));
  check("stream finishes and hides the stop button",
        await until(() => $("#stop-btn").classList.contains("hidden"), 30000));
  const answer = lastAssistant();
  check("answer mentions the question", answer.includes("hexagon"));
  check("assistant actions (copy/regenerate/teach) present",
        $$(".msg.assistant:last-child .msg-act").length >= 4,
        `${$$(".msg.assistant:last-child .msg-act").length} actions`);
  check("follow-up chips offered", $$("#followup-chips .chip").length >= 3);
  check("usage meter reflects the request",
        await until(() => !($("#sb-meter-text")?.textContent || "").includes("0 /"), 8000),
        $("#sb-meter-text")?.textContent);

  /* ── stop mid-stream keeps the partial answer ─────────────────── */
  $("#chat-input").value = "Now tell me a longer story about bees and gardens.";
  $("#send-btn").click();
  await until(() => lastAssistant().length > 5 && !$("#stop-btn").classList.contains("hidden"), 10000);
  const beforeStop = lastAssistant().length;
  $("#stop-btn").click();
  await wait(600);
  check("stopping keeps a partial answer", lastAssistant().length >= beforeStop,
        `${beforeStop} → ${lastAssistant().length} chars`);
  check("stopped answer is marked as stopped",
        Boolean($(".msg.assistant:last-child .bubble.was-stopped")));
  const partialSaved = await window.Aether.api(
    `/api/conversations/${window.Aether.chatState.conversationId}/messages`);
  check("partial answer persisted server-side",
        partialSaved.some((m) => m.role === "assistant" && (m.meta || "").includes("partial") ||
                                (m.content || "").length > 0));

  /* ── regenerate ───────────────────────────────────────────────── */
  const before = $$(".msg.assistant").length;
  $(".msg.assistant:last-child .msg-act[title='Regenerate']")?.click();
  await until(() => $$(".msg.assistant").length > 0 && $("#stop-btn").classList.contains("hidden"), 40000);
  check("regenerate leaves one answer per turn", $$(".msg.assistant").length === before,
        `${before} → ${$$(".msg.assistant").length}`);

  /* ── edit own last message (rewind) ───────────────────────────── */
  const userCount = $$(".msg.user").length;
  const editButtons = $$(".msg.user .msg-act[title='Edit and resend']");
  editButtons[editButtons.length - 1]?.click();
  await wait(300);
  const editor = $$("#messages textarea.edit-area").pop();
  check("edit opens an editor on the last user message", Boolean(editor));
  if (editor) {
    editor.value = "Rewritten question about hexagons.";
    const save = editor.closest(".bubble").querySelector(".edit-actions button.primary");
    save?.click();
    const rewound = await until(() => $$(".msg.user").length <= userCount, 40000);
    check("editing rewinds the conversation", rewound, `${$$(".msg.user").length} user turns`);
  }

  /* ── presentations: incremental build + exports ───────────────── */
  $("#nav-slides").click();
  $("#slide-topic").value = "Why bees matter";
  $("#slide-count").value = "3";
  $("#slide-go").click();
  check("deck rows appear", await until(() => $$(".slide-row").length === 3, 30000),
        `${$$(".slide-row").length} rows`);
  check("slide 1 finishes while later slides are still pending",
        await until(() => $$('.slide-row[data-state="done"]').length >= 1, 30000));
  const allDone = await until(() => $$('.slide-row[data-state="done"]').length === 3, 60000);
  check("all slides generate incrementally", allDone,
        $$(".slide-row").map((r) => r.dataset.state).join(","));
  check("deck viewer opens with content",
        await until(() => !$("#slides-modal").classList.contains("hidden") &&
                          ($("#slide-stage").textContent || "").toLowerCase().includes("bees"), 10000),
        ($("#slide-stage").textContent || "").slice(0, 50));
  // navigate every slide
  let navOk = true;
  for (let i = 0; i < 3; i += 1) {
    $("#slide-next").click();
    await wait(120);
    if (!($("#slide-counter")?.textContent || "").includes("/")) navOk = false;
  }
  check("deck navigation works", navOk, $("#slide-counter")?.textContent);

  const deckId = window.Aether.slidesState?.deckId || window.Aether.deckState?.deckId;
  check("deck is persisted with an id", Boolean(deckId), String(deckId));
  for (const kind of ["pptx", "html"]) {
    const resp = await window.Aether.api(`/api/presentations/${kind}`, {
      method: "POST", raw: true, body: { deck_id: deckId, theme: "ocean" },
    });
    const blob = await resp.blob();
    check(`${kind} export returns a real file`, blob.size > 1500, `${blob.size} bytes`);
    if (kind === "pptx") {
      const buf = Buffer.from(await blob.arrayBuffer());
      check("pptx is a valid OOXML zip", buf[0] === 0x50 && buf[1] === 0x4b,
            `magic ${buf.slice(0, 2).toString("hex")}`);
      writeFileSync("/tmp/aether-e2e-deck.pptx", buf);
    } else {
      const text = await blob.text();
      check("html deck contains the slide titles",
            text.includes("bees") || text.includes("Bees"), `${text.length} chars`);
      writeFileSync("/tmp/aether-e2e-deck.html", text);
    }
  }
  $("#slides-close").click();

  /* ── resume after reload: decks list ──────────────────────────── */
  await window.Aether.loadDecks();
  check("deck appears in the deck list", $$("#deck-list .deck-row").length >= 1,
        `${$$("#deck-list .deck-row").length} decks / ${$("#deck-list")?.textContent?.slice(0, 40)}`);

  /* ── voice: sentence-by-sentence TTS + captions + call flow ───── */
  const voices = await window.Aether.api("/api/voice/voices");
  check("voice catalogue reaches the UI", voices.voices.length >= 4, `${voices.voices.length}`);
  window.Aether.setVoice(voices.voices[0].id);
  const spoken = [];
  window.speechSynthesis.speak = (u) => spoken.push(u.text);
  await window.Aether.speak("First line about bees. Second line about gardens. Third one.",
                            { onCaption: () => {} });
  await wait(400);
  const usedServerTts = Boolean(window.__audio?.started);
  check("voice answers are spoken (server TTS or browser neural)",
        usedServerTts || spoken.length > 0,
        usedServerTts ? "server audio" : `browser: ${spoken.length} utterances`);

  window.Aether.startCall();
  await until(() => !$("#call-view").classList.contains("hidden"), 5000);
  check("call view opens with a mic indicator", Boolean($("#call-view .call-orb")));
  const rec = window.__rec;
  rec.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "How are bees doing" }], { isFinal: true })] });
  check("voice question is sent after the silence pause",
        await until(() => ($$(".msg.user .content").slice(-1)[0]?.textContent || "").includes("How are bees"),
                     15000),
        $$(".msg.user .content").slice(-1)[0]?.textContent);
  $("#call-end").click();
  await wait(200);
  check("ending the call closes the view", $("#call-view").classList.contains("hidden"));

  /* ── voice v2: HD catalogue, prefs, prompt clauses, exam mode ──── */
  const hd = voices.hd || {};
  check("voices payload advertises the HD catalogue",
        hd.enabled === true && (hd.voices || []).length >= 5,
        `${(hd.voices || []).length} HD voices`);
  check("HD languages are popular-first (uz, en, ru, tr)",
        ["uz", "en", "ru", "tr"].every((code, index) => hd.languages?.[index]?.code === code),
        (hd.languages || []).slice(0, 4).map((l) => l.code).join(","));
  check("HD default voice is the multilingual one",
        hd.default_voice === "en-US-AndrewMultilingualNeural", hd.default_voice);

  const ttsHeaders = { "content-type": "application/json",
                       authorization: `Bearer ${window.Aether.session.token}` };
  const hdSpeak = await window.fetch("/api/voice/tts", {
    method: "POST", headers: ttsHeaders,
    body: JSON.stringify({ text: "Salom, bu HD ovoz.", voice: "uz-UZ-SardorNeural",
                           speed: 1.1, provider: "auto" }),
  });
  const hdBytes = (await hdSpeak.arrayBuffer()).byteLength;
  check("HD voice answers with audio and says so",
        hdSpeak.status === 200 && hdSpeak.headers.get("x-aether-tts") === "hd" && hdBytes > 512,
        `${hdSpeak.status} · ${hdSpeak.headers.get("x-aether-tts")} · ${hdBytes}B`);
  const legacySpeak = await window.fetch("/api/voice/tts", {
    method: "POST", headers: ttsHeaders,
    body: JSON.stringify({ text: "Backup voice.", voice: "nova", provider: "auto" }),
  });
  check("legacy voice still answers as the backup link",
        legacySpeak.status === 200 && legacySpeak.headers.get("x-aether-tts") === "legacy",
        legacySpeak.headers.get("x-aether-tts"));
  const browserSpeak = await window.fetch("/api/voice/tts", {
    method: "POST", headers: ttsHeaders,
    body: JSON.stringify({ text: "Browser only.", voice: "nova", provider: "browser" }),
  });
  const browserBody = await browserSpeak.json();
  check("browser-only requests come back as {browser_tts}",
        browserSpeak.status === 503 && browserBody?.detail?.browser_tts === true,
        `${browserSpeak.status} ${JSON.stringify(browserBody?.detail?.message)}`);

  /* ── new settings flags round-trip + prompt clauses ────────────── */
  const prefsDefaults = await window.Aether.api("/api/settings/prefs");
  check("new voice prefs exist with sane defaults",
        prefsDefaults.voice_hd === true && prefsDefaults.voice_language === "auto"
        && prefsDefaults.voice_name === "" && prefsDefaults.allow_strong_language === false
        && prefsDefaults.exam?.enabled === false,
        JSON.stringify(prefsDefaults));
  await window.Aether.api("/api/settings/prefs", { method: "PUT", body: {
    voice_hd: true, voice_language: "uz", voice_name: "uz-UZ-SardorNeural",
    allow_strong_language: true, exam: { enabled: true, preset: "cefr_b2", topic: "space" },
  } });
  const prefsReread = await window.Aether.api("/api/settings/prefs");
  check("voice prefs round-trip and persist",
        prefsReread.voice_name === "uz-UZ-SardorNeural" && prefsReread.voice_language === "uz"
        && prefsReread.allow_strong_language === true && prefsReread.exam.preset === "cefr_b2"
        && prefsReread.exam.topic === "space",
        JSON.stringify(prefsReread.exam));

  const capturedPrompt = async () =>
    (await (await realFetch(`${BASE}/__mock/last_prompt`)).json()).system || "";

  const sendMessage = async (text) => {
    $("#chat-input").value = text;
    $("#chat-input").dispatchEvent(new window.Event("input"));
    $("#send-btn").click();
    await until(() => !$("#stop-btn").classList.contains("hidden"), 6000);
    await until(() => $("#stop-btn").classList.contains("hidden"), 30000);
    await wait(350);
  };

  await sendMessage("clause check with mature language on");
  const promptOn = await capturedPrompt();
  check("mature-language clause reaches the model when the toggle is on",
        promptOn.includes("realistic profanity") && !promptOn.includes("no profanity"),
        promptOn.includes("realistic profanity") ? "clause present" : "missing");
  check("language clause follows the preference (Uzbek)",
        promptOn.includes("Reply in Uzbek."));

  await window.Aether.api("/api/settings/prefs",
                          { method: "PUT", body: { allow_strong_language: false,
                                                   voice_language: "auto" } });
  await sendMessage("clause check with family-friendly language");
  const promptOff = await capturedPrompt();
  check("family-friendly clause comes back when the toggle is off",
        promptOff.includes("no profanity") && !promptOff.includes("realistic profanity"),
        promptOff.includes("no profanity") ? "clause present" : "missing");
  check("auto language clause is used again",
        promptOff.includes("Reply in the user's language."));

  /* ── exam mode in the call: HUD, longer pause, Done/Finish ────── */
  window.Aether.startCall();
  await until(() => !$("#call-view").classList.contains("hidden"), 5000);
  $("#call-mode-exam").click();
  await wait(80);
  check("exam HUD is visible in the call",
        !$("#call-hud").classList.contains("hidden")
        && /Speaking exam/.test($("#call-hud").textContent), $("#call-hud").textContent);
  const examRec = window.__rec;
  const usersBeforeExam = $$(".msg.user").length;
  const examStart = Date.now();
  examRec.onresult({ resultIndex: 0,
    results: [Object.assign([{ transcript: "Cities should invest in parks" }], { isFinal: true })] });
  await until(() => $$(".msg.user").length > usersBeforeExam, 9000);
  const examDelay = Date.now() - examStart;
  check("exam answers wait for the longer 2.4 s pause", examDelay >= 2000, `${examDelay} ms`);
  await until(() => /question \d/.test($("#call-hud").textContent), 20000);
  check("exam HUD counts the question",
        /question \d/.test($("#call-hud").textContent), $("#call-hud").textContent);
  check("Done and Finish controls live in the call",
        Boolean($("#call-done")) && Boolean($("#call-finish"))
        && !$("#call-done").classList.contains("hidden"));
  const usersBeforeDone = $$(".msg.user").length;
  examRec.onresult({ resultIndex: 0,
    results: [Object.assign([{ transcript: "Safety and lighting matter most" }], { isFinal: true })] });
  const doneStart = Date.now();
  $("#call-done").click();
  await until(() => $$(".msg.user").length > usersBeforeDone, 9000);
  check("Done sends immediately instead of waiting", Date.now() - doneStart < 1500,
        `${Date.now() - doneStart} ms`);
  const usersBeforeFinish = $$(".msg.user").length;
  $("#call-finish").click();
  const finishSent = await until(() => $$(".msg.user").length > usersBeforeFinish, 9000);
  const finishText = $$(".msg.user .content").slice(-1)[0]?.textContent || "";
  check("Finish asks for the feedback card",
        finishSent && /finished|feedback/i.test(finishText), finishText.slice(0, 60));
  $("#call-end").click();
  await wait(200);

  /* ── images ───────────────────────────────────────────────────── */
  $("#nav-images").click();
  $("#image-prompt").value = "a geometric hexagon done in soft indigo";
  // The sandbox cannot fetch image.pollinations.ai, so stub just the pixel fetch
  // to prove the surrounding UI (bytes → download → history) works.
  const originalFetch = window.fetch;
  window.Image = class {
    set src(value) { this._src = value; this.naturalWidth = 1024; setTimeout(() => this.onload?.(), 30); }
    get src() { return this._src; }
  };
  $("#image-go").click();
  const rendered = await until(() => $("#image-results .image-card:not(.loading)"), 30000);
  check("image card completes with the prompt attached", rendered,
        $("#image-results .image-prompt")?.textContent);
  check("image history remembers the prompt",
        (window.localStorage.getItem("aether:imageHistory") || "").includes("hexagon"),
        (window.localStorage.getItem("aether:imageHistory") || "").slice(0, 60));
  window.fetch = originalFetch;
  check("image download endpoint is offered",
        Boolean($("#image-results .image-actions, #image-results .image-cap button")));

  /* ── teach → memory round trip through the UI ─────────────────── */
  $("#nav-settings").click();
  await until(() => ($("#usage-detail")?.textContent || "").includes("/"), 10000);
  $("#teach-start").click();
  check("teach interview renders questions",
        await until(() => Boolean($(".teach-card") && $(".teach-card").textContent.length > 10), 10000),
        $(".teach-card")?.textContent?.slice(0, 60));
  const teachInput = $(".teach-card input[type=text], .teach-card textarea, #teach-answer");
  if (teachInput) {
    teachInput.value = "Call me Sam and keep answers short.";
    $(".teach-card button.primary")?.click();
    check("teach records the answer",
          await until(() => ($("#memory-list")?.textContent || "").includes("Sam"), 15000),
          ($("#memory-list")?.textContent || "").slice(0, 80));
  }

  check("export-all link is present", Boolean($("#export-all")));
  check("no runtime errors", errors.length === 0, errors.join(" | "));
  console.log(failures.length ? `\n${failures.length} FAILED: ${failures.join(", ")}` : "\nall E2E checks passed");
  process.exit(failures.length ? 1 : 0);
}

run().catch((error) => { console.error("harness crashed:", error); process.exit(1); });
