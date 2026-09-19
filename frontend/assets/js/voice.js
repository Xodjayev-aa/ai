/* ══════════════════════════════════════════════════════════════════════
   Aether — voice

   Two things live here:

   1. The speech engine (shared by "read aloud" and call mode).
      Voice chain, strictly in order:
        a) Pollinations openai-audio — keyless neural voices (alloy/nova/...)
        b) the browser's *neural* voices — filtered for Natural/Neural/Google/
           Samantha-class names, never a robotic default.
      Answers are split into sentences and played as they arrive, so the first
      sound starts within a second or two instead of after a long silence.

   2. Call mode: full-screen, continuous listening with silence detection,
      barge-in, live captions, mute, end-call, voice picker and speed control.
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function voice() {
  const A = window.Aether;
  const { $ } = A;

  /* ───────────────────────── sentence splitting ───────────────────── */

  const ABBREV = /\b(?:mr|mrs|ms|dr|prof|st|vs|etc|e\.g|i\.e|fig|no|approx|dept|est)\.$/i;

  A.splitSentences = function splitSentences(text, maxLen = 220) {
    const clean = A.stripMarkdown(text).replace(/\s+/g, " ").trim();
    if (!clean) return [];
    const parts = [];
    for (const token of clean.split(/(?<=[.!?…])\s+/)) {
      const piece = token.trim();
      if (!piece) continue;
      const previous = parts[parts.length - 1];
      // Merge only real fragments (abbreviations like "Mr." split off above) —
      // every complete sentence becomes its own request, which is what makes
      // the first word audible within a second or two.
      if (previous && ABBREV.test(previous)) {
        parts[parts.length - 1] = `${previous} ${piece}`;
      } else {
        parts.push(piece);
      }
    }
    // Long sentences are split again at commas so playback still starts early.
    const out = [];
    for (const part of parts) {
      if (part.length <= maxLen) { out.push(part); continue; }
      let rest = part;
      while (rest.length > maxLen) {
        const comma = rest.lastIndexOf(",", maxLen);
        const cut = comma > maxLen * 0.5 ? comma + 1 : rest.lastIndexOf(" ", maxLen);
        if (cut <= 0) break;
        out.push(rest.slice(0, cut).trim());
        rest = rest.slice(cut).trim();
      }
      if (rest) out.push(rest);
    }
    return out.filter(Boolean);
  };

  /* ─────────────────────── browser neural voices ──────────────────── */

  const NEURAL_HINT = /(natural|neural|online|google|samantha|aria|jenny|guy|libby|sonia|ryan|siri|eloquence|premium|enhanced)/i;
  const ROBOTIC_HINT = /(espeak|albert|bad news|bells|boing|bubbles|cellos|zarvox|whisper|trinoids|pipe organ|novelty|desktop|compact|robotic)/i;
  // Voices that are plain but genuinely pleasant — a tier above the harsh
  // legacy SAPI/GPS set when no neural voice is installed at all.
  const PLEASANT_HINT = /(zira|serena|moira|tessa|fiona|karen|catherine|susan|michelle|emma|amelie|joanna|kendra|kimberly|salli)/i;
  const HARSH_HINT = /(david|mark|fred|albert|victoria|agnes|junior|ralph|harsh|flat)/i;

  A.pickBrowserVoice = function pickBrowserVoice() {
    if (!window.speechSynthesis) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return null;
    const english = voices.filter((v) => /^en/i.test(v.lang || ""));
    const score = (voice) => {
      let value = 0;
      if (NEURAL_HINT.test(`${voice.name} ${voice.voiceURI}`)) value += 10;
      if (ROBOTIC_HINT.test(voice.name)) value -= 20;
      if (PLEASANT_HINT.test(voice.name)) value += 5;
      if (HARSH_HINT.test(voice.name)) value -= 3;
      if (voice.localService === false) value += 4;      // cloud voices sound best
      if (/^en-US/i.test(voice.lang)) value += 2;
      if (/samantha|aria|jenny|libby|sonia|natural/i.test(voice.name)) value += 3;
      return value;
    };
    const pool = (english.length ? english : voices)
      .slice()
      .sort((a, b) => score(b) - score(a));
    return pool[0] || null;
  };

  /* ──────────────────────────── engine ───────────────────────────── */

  const engine = {
    active: null,
    generation: 0,
    preferBrowser: false,
    onCaption: null,
    onState: null,
    voice: A.store.get("voice", "nova"),
    speed: Number(A.store.get("voiceSpeed", 1)) || 1,
    muted: false,
  };
  A.voiceEngine = engine;

  A.setVoice = function setVoice(id) {
    engine.voice = id || "nova";
    A.store.set("voice", engine.voice);
  };
  A.setVoiceSpeed = function setVoiceSpeed(speed) {
    engine.speed = A.clamp(Number(speed) || 1, 0.5, 2);
    A.store.set("voiceSpeed", engine.speed);
  };

  function stopAudio() {
    if (engine.active) {
      engine.active.pause();
      engine.active.src = "";
      engine.active = null;
    }
    try { window.speechSynthesis?.cancel(); } catch { /* ignore */ }
  }

  A.stopSpeaking = function stopSpeaking() {
    engine.generation += 1;
    engine.queue = [];
    stopAudio();
    engine.onState?.("idle");
  };

  async function browserSpeak(text, generation) {
    if (!window.speechSynthesis) return false;
    const voice = A.pickBrowserVoice();
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      if (voice) utterance.voice = voice;
      utterance.rate = A.clamp(engine.speed * (voice && /en/i.test(voice.lang) ? 1 : 1), 0.5, 2);
      utterance.pitch = 1;
      utterance.onend = () => resolve(true);
      utterance.onerror = () => resolve(false);
      if (generation !== engine.generation) { resolve(false); return; }
      window.speechSynthesis.speak(utterance);
    });
  }

  async function fetchSpeech(text, generation) {
    if (engine.preferBrowser) return null;
    try {
      const resp = await fetch("/api/voice/tts", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(A.session.token ? { Authorization: `Bearer ${A.session.token}` } : {}),
        },
        body: JSON.stringify({ text, voice: engine.voice, speed: engine.speed }),
      });
      if (!resp.ok) {
        if (resp.status === 503) {
          const payload = await resp.json().catch(() => ({}));
          if (payload?.detail?.browser_tts) engine.preferBrowser = true;
        }
        return null;
      }
      const blob = await resp.blob();
      if (generation !== engine.generation) return null;
      return URL.createObjectURL(blob);
    } catch { return null; }
  }

  function playUrl(url) {
    return new Promise((resolve) => {
      const audio = new Audio(url);
      audio.playbackRate = A.clamp(engine.speed, 0.5, 2);
      engine.active = audio;
      const done = () => { URL.revokeObjectURL(url); if (engine.active === audio) engine.active = null; resolve(); };
      audio.onended = done;
      audio.onerror = done;
      audio.play().then(() => {}).catch(() => done());
    });
  }

  /**
   * A queue that speaks text as it arrives: pass sentences in, it plays them
   * one after another, fetching the next while the current one is speaking.
   */
  class SpeechQueue {
    constructor({ onCaption, onState, generation } = {}) {
      this.chunks = [];
      this.playing = false;
      this.done = false;
      this.onCaption = onCaption;
      this.onState = onState;
      this.generation = generation ?? engine.generation;
    }

    push(text) {
      const sentences = A.splitSentences(text);
      this.chunks.push(...sentences);
      this.#pump();
    }

    async #pump() {
      if (this.playing) return;
      this.playing = true;
      while (this.chunks.length && this.generation === engine.generation) {
        const sentence = this.chunks.shift();
        this.onState?.("speaking");
        this.onCaption?.(sentence, "speaking");
        const url = await fetchSpeech(sentence, this.generation);
        if (this.generation !== engine.generation) break;
        if (url) await playUrl(url);
        else await browserSpeak(sentence, this.generation);
        if (this.generation !== engine.generation) break;
      }
      this.playing = false;
      if (this.generation === engine.generation) {
        this.onState?.(this.done ? "idle" : "idle");
        this.onCaption?.("", "idle");
      }
    }

    finish() { this.done = true; this.#pump(); }

    get busy() { return this.playing || this.chunks.length > 0; }
  }

  /** Read a whole message aloud (chat "Read aloud" button). */
  A.speak = async function speak(text, btn) {
    const plain = A.stripMarkdown(text).slice(0, 2000);
    if (!plain) return;
    if (engine.speakingText === plain) { A.stopSpeaking(); engine.speakingText = null; return; }
    A.stopSpeaking();
    engine.speakingText = plain;
    const original = btn?.innerHTML;
    if (btn) btn.innerHTML = A.icon("stop", 13);
    const generation = engine.generation;
    const queue = new SpeechQueue({});
    queue.push(plain);
    queue.finish();
    const wait = () => {
      if (queue.busy && generation === engine.generation) setTimeout(wait, 400);
      else {
        engine.speakingText = null;
        if (btn && original) btn.innerHTML = original;
      }
    };
    wait();
  };

  /* ────────────────────────── call mode ──────────────────────────── */

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  const call = {
    active: false,
    listening: false,
    muted: false,
    recognition: null,
    queue: null,
    generation: 0,
    finalText: "",
    silenceTimer: null,
    lastSpeechAt: 0,
    speaking: false,
  };
  A.voiceCall = call;

  const els = {};

  function mount() {
    if (els.root) return els;
    const root = document.createElement("div");
    root.id = "call-view";
    root.className = "call-view hidden";
    root.innerHTML = `
      <div class="call-top">
        <span class="call-status"><i class="dot"></i><span id="call-status-text">Connecting…</span></span>
        <button class="icon-btn" id="call-close" title="End call" aria-label="End call">
          <svg class="ic" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </div>
      <div class="call-stage">
        <div class="call-orb" id="call-orb" data-state="idle">
          <span class="orb-ring"></span><span class="orb-ring delay"></span>
          <span class="orb-core">${A.icon("logo", 38)}</span>
        </div>
        <p class="call-caption" id="call-caption">Tap the mic and start talking.</p>
        <p class="call-heard muted small-text" id="call-heard"></p>
      </div>
      <div class="call-controls">
        <button class="call-btn" id="call-mute" title="Mute microphone">${A.icon("mic", 20)}</button>
        <button class="call-btn primary" id="call-mic" title="Start listening">${A.icon("mic", 22)}</button>
        <button class="call-btn end" id="call-end" title="End call">${A.icon("phone", 20)}</button>
      </div>
      <div class="call-settings">
        <label class="call-field">
          <span class="muted small-text">Voice</span>
          <select id="call-voice"></select>
        </label>
        <label class="call-field">
          <span class="muted small-text">Speed <b id="call-speed-value">1.0×</b></span>
          <input id="call-speed" type="range" min="0.7" max="1.4" step="0.05">
        </label>
        <p class="muted tiny" id="call-hint">Free shared voice servers can have short waits. If they are
          busy, Aether uses your device's best neural voice.</p>
      </div>`;
    document.body.append(root);
    els.root = root;
    els.orb = $("#call-orb", root);
    els.caption = $("#call-caption", root);
    els.heard = $("#call-heard", root);
    els.status = $("#call-status-text", root);
    els.mic = $("#call-mic", root);
    els.mute = $("#call-mute", root);
    els.end = $("#call-end", root);
    els.voice = $("#call-voice", root);
    els.speed = $("#call-speed", root);
    els.speedValue = $("#call-speed-value", root);
    els.close = $("#call-close", root);

    els.end.onclick = () => A.endCall();
    els.close.onclick = () => A.endCall();
    els.mute.onclick = () => setMuted(!call.muted);
    els.mic.onclick = () => { call.listening ? pauseListening() : startListening(); };
    els.speed.value = String(engine.speed);
    els.speedValue.textContent = `${engine.speed.toFixed(2)}×`;
    els.speed.oninput = () => {
      A.setVoiceSpeed(Number(els.speed.value));
      els.speedValue.textContent = `${engine.speed.toFixed(2)}×`;
    };
    els.voice.onchange = () => A.setVoice(els.voice.value);
    return els;
  }

  function setOrb(state) {
    if (!els.orb) return;
    els.orb.dataset.state = state;
    els.root.classList.toggle("speaking", state === "speaking");
    els.root.classList.toggle("listening", state === "listening");
    els.root.classList.toggle("thinking", state === "thinking");
  }

  function setStatus(text) { if (els.status) els.status.textContent = text; }

  function caption(text, kind = "speaking") {
    if (!els.caption) return;
    if (text) els.caption.textContent = text;
    els.caption.dataset.kind = kind;
    if (kind === "listening" && !text) els.caption.textContent = "Listening…";
  }

  async function loadVoices() {
    try {
      const data = await A.api("/api/voice/voices");
      els.voice.innerHTML = "";
      for (const item of data.voices) {
        els.voice.append(A.el("option", {
          value: item.id,
          text: `${item.name}${item.tags ? ` — ${item.tags}` : ""}`,
          selected: item.id === engine.voice,
        }));
      }
      A.voiceNote = data.note;
      if (data.queue?.cooling_down) {
        els.status.textContent = "Voice service busy — using device voices";
      }
    } catch { /* keep the default option */ }
  }

  function startListening() {
    if (!Recognition || call.muted) return;
    try {
      call.recognition = new Recognition();
      const rec = call.recognition;
      rec.lang = navigator.language || "en-US";
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      rec.onstart = () => {
        call.listening = true;
        els.mic.classList.add("live");
        setStatus("Listening — pause to send");
        setOrb("listening");
        caption("", "listening");
      };
      rec.onspeechstart = () => {
        // Barging in stops Aether mid-sentence, like a real call.
        if (call.speaking) A.stopSpeaking();
        call.speaking = false;
        call.lastSpeechAt = Date.now();
        setOrb("listening");
      };
      rec.onresult = (event) => {
        call.lastSpeechAt = Date.now();
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          if (result.isFinal) call.finalText += result[0].transcript;
          else interim += result[0].transcript;
        }
        caption(call.finalText || interim, "listening");
        els.heard.textContent = call.finalText ? "" : "Start speaking…";
        clearTimeout(call.silenceTimer);
        call.silenceTimer = setTimeout(onSilence, 1100);
      };
      rec.onerror = (event) => {
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          setStatus("Microphone blocked — allow access or use text chat");
          els.heard.textContent = "Enable the microphone in your browser settings to keep talking.";
        } else if (event.error === "no-speech") {
          els.heard.textContent = "Didn't catch that — try again.";
        }
      };
      rec.onend = () => {
        call.listening = false;
        els.mic.classList.remove("live");
        if (call.active && !call.muted && !call.speaking) {
          setTimeout(() => { if (call.active && !call.speaking && !call.listening) restartListening(); }, 250);
        }
      };
      rec.start();
    } catch {
      setStatus("Could not start the microphone");
    }
  }

  function restartListening() {
    if (!call.active || call.muted || call.speaking) return;
    try { call.recognition?.start(); } catch { /* already running */ }
  }

  function pauseListening() {
    call.listening = false;
    try { call.recognition?.stop(); } catch { /* ignore */ }
    els.mic.classList.remove("live");
    setStatus("Paused — tap the mic to talk");
    setOrb("idle");
  }

  function onSilence() {
    const said = call.finalText.trim();
    call.finalText = "";
    if (!said || !call.active) return;
    // If Aether is still talking, the user talking wins: stop the audio and
    // answer the question. Dropping it silently would look like a dead mic.
    if (call.speaking) {
      A.stopSpeaking();
      call.speaking = false;
    }
    els.heard.textContent = "";
    setOrb("thinking");
    setStatus("Thinking…");
    A.askByVoice(said);
  }

  function setMuted(muted) {
    call.muted = muted;
    els.mute.classList.toggle("on", muted);
    els.mute.innerHTML = muted ? A.icon("micOff", 20) : A.icon("mic", 20);
    if (muted) {
      A.stopSpeaking();
      pauseListening();
      setStatus("Muted");
      els.heard.textContent = "Mic muted.";
    } else {
      els.heard.textContent = "";
      startListening();
    }
  }

  /** Ask a question by voice and speak the answer as it streams in. */
  A.askByVoice = async function askByVoice(text) {
    if (!call.active) return;
    const generation = ++engine.generation;
    call.speaking = true;
    const queue = new SpeechQueue({
      generation,
      onState: (state) => {
        call.speaking = state === "speaking" || queue.chunks.length > 0;
        if (state === "speaking") setOrb("speaking");
      },
      onCaption: (sentence, kind) => caption(sentence, kind),
    });
    call.queue = queue;

    let spoken = "";
    let sentenceBuffer = "";
    let stopped = false;
    const controller = new AbortController();
    call.controller = controller;

    try {
      if (!A.chatState.conversationId) {
        const conv = await A.api("/api/conversations", { method: "POST", body: { title: text.slice(0, 60) } });
        A.chatState.conversationId = conv.id;
        A.loadConversations();
      }
      const assistantId = A.uuid();
      // Show the spoken question in the transcript too — a call must leave a
      // readable conversation behind, not just spoken audio.
      A.appendUserMessage?.(text, { voice: true });
      A.appendAssistantMessage("", { live: true, id: assistantId });
      const wrap = $("#messages").lastElementChild;
      const content = wrap?.querySelector(".content") || null;
      if (content) content.innerHTML = A.typingDots();

      await A.stream("/api/chat/stream", {
        conversation_id: A.chatState.conversationId,
        message: text,
        assistant_message_id: assistantId,
        client_saves_partial: true,
        voice: true,
        mode: "ai",
        persona_id: null,
      }, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "delta" || event.type === "final") {
            if (event.type === "delta") {
              sentenceBuffer += event.text;
              const sentences = A.splitSentences(sentenceBuffer);
              if (sentences.length > 1) {
                const complete = sentences.slice(0, -1);
                sentenceBuffer = sentences[sentences.length - 1];
                for (const sentence of complete) { spoken += ` ${sentence}`; queue.push(sentence); }
              }
            } else if (event.text) {
              spoken = event.text;
            }
            if (content) content.innerHTML = A.markdown(spoken || sentenceBuffer);
            A.emit("usage:refresh");
          } else if (event.type === "phase" && event.phase === "queued") {
            setStatus(`Free AI is shared — about ${Math.round(event.wait_seconds)}s wait`);
          } else if (event.type === "error") {
            stopped = true;
            setStatus("The free AI is unavailable — try again");
            els.heard.textContent = event.detail || "";
            caption("Sorry, the free AI is busy right now. Tap the mic and try again.", "error");
            queue.push("Sorry, the free AI is busy right now.");
          } else if (event.type === "done") {
            wrap.dataset.messageId = event.message_id || assistantId;
          }
        },
      });
    } catch (err) {
      if (err?.name !== "AbortError") {
        setStatus("Connection hiccup — try again");
        els.heard.textContent = err.message || "";
      }
    } finally {
      const tail = A.splitSentences(sentenceBuffer);
      if (tail.length) { spoken += ` ${tail.join(" ")}`; queue.push(sentenceBuffer); }
      queue.finish();
      const wrap = $("#messages").lastElementChild;
      if (wrap && (spoken || sentenceBuffer)) {
        wrap.querySelector(".content").innerHTML = A.markdown(spoken || sentenceBuffer);
        wrap.chat = { content: wrap.querySelector(".content"), acts: wrap.querySelector(".msg-acts") };
        A.decorateAssistant?.(wrap, spoken || sentenceBuffer, wrap.dataset.messageId, { });
      }
      // Stay "speaking" until the spoken audio actually drains, so barge-in
      // (or the mute button) can interrupt mid-sentence.
      call.queue = queue;
      (async () => {
        for (let i = 0; i < 240; i += 1) {
          if (generation !== engine.generation || !queue.busy) break;
          await A.sleep(200);
        }
        call.speaking = false;
        call.queue = null;
        if (call.active && !stopped && generation === engine.generation) {
          setStatus("Listening — pause to send");
          caption("", "listening");
          setOrb("listening");
          restartListening();
        }
      })();
    }
  };

  A.startCall = async function startCall() {
    if (!Recognition) {
      const supported = await A.confirm({
        title: "Voice calls aren’t supported in this browser",
        message: "Speech recognition needs Chrome, Edge or Safari. You can still "
               + "chat by text, and Aether can read answers out loud.",
        okLabel: "Open text chat", cancelLabel: "Close",
      });
      if (supported) { A.showPane("chat"); $("#chat-input")?.focus(); }
      return;
    }
    mount();
    await loadVoices();
    // Start from a clean audio state so the mic is never competing with a
    // half-finished answer from before the call.
    A.stopSpeaking();
    call.speaking = false;
    call.finalText = "";
    call.active = true;
    call.muted = false;
    engine.generation += 1;
    engine.preferBrowser = false;
    els.root.classList.remove("hidden");
    document.body.classList.add("in-call");
    setOrb("listening");
    setStatus("Listening — pause to send");
    caption("", "listening");
    els.heard.textContent = "Say something like “explain my homework”.";
    setMuted(false);
    A.emit("call:started");
  };

  A.endCall = function endCall() {
    call.active = false;
    call.listening = false;
    call.finalText = "";
    clearTimeout(call.silenceTimer);
    A.stopSpeaking();
    call.controller?.abort();
    try { call.recognition?.stop(); } catch { /* ignore */ }
    els.root?.classList.add("hidden");
    document.body.classList.remove("in-call");
    setStatus("Call ended");
    A.emit("call:ended");
  };

  A.initVoice = function initVoice() {
    mount();
    // Voices load asynchronously in some browsers.
    if (window.speechSynthesis) {
      window.speechSynthesis.onvoiceschanged = () => { A.pickBrowserVoice(); };
    }
    $("#call-btn")?.addEventListener("click", () => A.startCall());
  };
})();
