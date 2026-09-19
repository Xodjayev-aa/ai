/* ══════════════════════════════════════════════════════════════════════
   Aether — settings, Teach mode, memory, personas, usage & about
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function settings() {
  const A = window.Aether;
  const { $ } = A;

  const S = {
    memories: [], personas: [], usage: null, voices: [], teach: null,
    hd: null, hdVoices: [], hdLanguages: [], prefs: null,
  };

  /* ─────────────────────────── load / render ──────────────────────── */

  A.loadSettings = async function loadSettings() {
    try {
      const [instructions, memory, personas, usage, voices, prefs] = await Promise.all([
        A.api("/api/settings/instructions").catch(() => ({ text: "" })),
        A.api("/api/settings/memory").catch(() => ({ auto: true, memories: [] })),
        A.api("/api/settings/personas").catch(() => []),
        A.api("/api/usage").catch(() => null),
        A.api("/api/voice/voices").catch(() => ({ voices: [] })),
        A.api("/api/settings/prefs").catch(() => null),
      ]);
      $("#instructions").value = instructions.text || "";
      $("#auto-memory").checked = Boolean(memory.auto);
      S.memories = memory.memories || [];
      S.personas = personas || [];
      S.usage = usage;
      S.voices = voices.voices || [];
      S.hd = voices.hd || null;
      S.hdVoices = (voices.hd && voices.hd.voices) || [];
      S.hdLanguages = (voices.hd && voices.hd.languages) || [];
      S.prefs = prefs || S.prefs;
      renderMemories();
      renderPersonas();
      // Voice prefs first: a partial usage payload must never hide them.
      renderVoiceOptions();
      renderUsage();
      renderStorage(usage);
      $("#about-version").textContent = A.version;
    } catch (err) {
      if (err.status !== 401) A.toast(err.message, { type: "err" });
    }
  };

  function renderMemories() {
    const box = $("#memory-list");
    box.innerHTML = "";
    if (!S.memories.length) {
      box.append(A.el("p", {
        class: "muted small-text",
        text: "Nothing remembered yet. Use “Teach Aether” above and it will remember how you like your answers.",
      }));
      return;
    }
    for (const memory of S.memories) {
      box.append(A.el("div", { class: "memory-row" }, [
        A.el("span", { class: "memory-text", text: memory.content }),
        A.el("button", {
          class: "icon-btn", title: "Edit", html: A.icon("edit", 14),
          onclick: async () => {
            const value = await A.prompt({
              title: "Edit memory", value: memory.content, multiline: true, okLabel: "Save",
            });
            if (value === null || !value.trim()) return;
            try {
              await A.api(`/api/settings/memory/${memory.id}`, {
                method: "PATCH", body: { content: value.trim() },
              });
              A.toast("Memory updated");
              A.loadSettings();
            } catch (err) { A.toast(err.detail || "Could not update", { type: "err" }); }
          },
        }),
        A.el("button", {
          class: "icon-btn", title: "Forget", html: A.icon("trash", 14),
          onclick: async () => {
            await A.api(`/api/settings/memory/${memory.id}`, { method: "DELETE" });
            A.toast("Forgotten");
            A.loadSettings();
          },
        }),
      ]));
    }
  }

  function renderPersonas() {
    const box = $("#persona-list");
    box.innerHTML = "";
    if (!S.personas.length) {
      box.append(A.el("p", {
        class: "muted small-text",
        text: "No personas yet. A persona is a character or style Aether can switch into.",
      }));
      return;
    }
    for (const persona of S.personas) {
      box.append(A.el("div", { class: "persona-card" }, [
        A.el("div", { class: "persona-head" }, [
          A.el("span", { class: "persona-badge", text: persona.name.slice(0, 2).toUpperCase() }),
          A.el("strong", { text: persona.name }),
          A.el("button", {
            class: "icon-btn", title: "Delete persona", html: A.icon("trash", 14),
            onclick: async () => {
              const ok = await A.confirm({
                title: `Delete "${persona.name}"?`, okLabel: "Delete", danger: true,
              });
              if (!ok) return;
              await A.api(`/api/settings/personas/${persona.id}`, { method: "DELETE" });
              A.loadSettings();
            },
          }),
        ]),
        A.el("p", { class: "persona-prompt muted small-text", text: persona.prompt }),
        A.el("button", {
          class: "btn small-btn", text: "Use in this chat",
          onclick: () => {
            const select = $("#persona-select");
            select.value = persona.id;
            select.dispatchEvent(new Event("change"));
            A.closeSidebar();
            A.showPane("chat");
            A.toast(`Persona "${persona.name}" selected`);
          },
        }),
      ]));
    }
  }

  function renderUsage() {
    const usage = S.usage;
    if (!usage) return;
    const pct = Math.min(100, Math.round((usage.used_today / usage.limit) * 100));
    const reset = new Date(usage.resets_at);
    const hours = Math.max(0, Math.round((reset - Date.now()) / 3600000));
    $("#usage-detail").innerHTML = "";
    $("#usage-detail").append(
      A.el("div", { class: "usage-line" }, [
        A.el("strong", { text: `${usage.used_today} of ${usage.limit} AI requests today` }),
        A.el("span", { class: "muted small-text", text: `resets in ~${hours}h (midnight UTC)` }),
      ]),
      A.el("div", { class: "meter" }, [
        A.el("div", { class: "meter-fill", style: `width:${pct}%` }),
      ]),
      A.el("p", { class: "muted small-text", text:
        `Shared free tier: ${usage.queue.queued_requests} request(s) queued, `
        + `~${usage.queue.estimated_wait_seconds}s estimated wait. `
        + "Free shared AI — short waits possible." }),
      usage.cooldown?.active
        ? A.el("p", { class: "cooldown-note", text: `Cooling down: retry in ${A.formatCountdown(usage.cooldown.seconds)}` })
        : null,
    );
  }

  function renderStorage(usage) {
    const box = $("#storage-status");
    if (!box) return;
    const storage = usage?.storage;
    box.innerHTML = "";
    const warn = storage && !storage.persistent;
    box.append(A.el("div", { class: `storage-pill${warn ? " warn" : " ok"}` }, [
      A.el("span", { class: "dot" }),
      A.el("span", { text: storage ? storage.label : "Unknown storage" }),
    ]));
    if (storage?.warning) {
      const banner = $("#storage-banner");
      banner.textContent = storage.warning;
      banner.classList.remove("hidden");
    } else {
      $("#storage-banner").classList.add("hidden");
    }
  }

  function renderVoiceOptions() {
    renderVoicePrefs();
    const speed = $("#voice-speed");
    speed.value = String(A.voiceEngine.speed);
    $("#voice-speed-value").textContent = `${A.voiceEngine.speed.toFixed(2)}×`;
    speed.oninput = () => {
      A.setVoiceSpeed(Number(speed.value));
      $("#voice-speed-value").textContent = `${A.voiceEngine.speed.toFixed(2)}×`;
      savePrefs({ voice_speed: Number(speed.value) }, true);
    };
    $("#voice-test").onclick = () => A.speak(
      "Hi, this is how I sound in a call. Free shared voice servers can have short waits, "
      + "and if they are busy I switch to your device's best voice.", $("#voice-test"));
  }

  /* ─────────────────────── voice preferences (v2) ─────────────────── */

  function savePrefs(patch, quiet = false) {
    S.prefs = { ...(S.prefs || {}), ...patch };
    if (patch.exam) S.prefs.exam = { ...(S.prefs.exam || {}), ...patch.exam };
    return A.api("/api/settings/prefs", { method: "PUT", body: patch }).then(
      (saved) => {
        S.prefs = saved;
        A.voiceEngine?.syncPrefs?.(saved);
        if (!quiet) A.toast("Voice settings saved", { timeout: 1600 });
        return saved;
      },
      (err) => {
        if (!quiet) A.toast(err.detail || "Could not save the voice settings", { type: "err" });
        return S.prefs;
      },
    );
  }
  A.saveVoicePrefs = savePrefs;

  function renderVoicePrefs() {
    const prefs = S.prefs || {};
    const hd = S.hd || { enabled: false, languages: [], voices: [] };
    const hdBox = $("#voice-hd");
    const langSelect = $("#voice-language");
    const voiceSelect = $("#voice-select");
    if (!hdBox || !langSelect || !voiceSelect) return;

    // ── HD toggle
    hdBox.checked = prefs.voice_hd !== false;
    hdBox.disabled = !hd.enabled;
    $("#voice-hd-note").classList.toggle("hidden", hd.enabled);
    hdBox.onchange = () => {
      savePrefs({ voice_hd: hdBox.checked });
      A.voiceEngine.preferBrowser = !hdBox.checked && false;
      renderVoicePrefs();
    };

    // ── language list: Auto, then the popular codes, then the rest
    const languages = S.hdLanguages.length
      ? S.hdLanguages
      : [{ code: "en", name: "English", count: 1 }];
    const current = prefs.voice_language || "auto";
    langSelect.innerHTML = "";
    langSelect.append(A.el("option", { value: "auto", text: "Auto (the language used)" }));
    for (const language of languages) {
      langSelect.append(A.el("option", {
        value: language.code,
        text: `${language.name}${language.count ? ` (${language.count})` : ""}`,
      }));
    }
    langSelect.value = current === "auto" || languages.some((l) => l.code === current)
      ? current : "auto";
    langSelect.onchange = () => {
      savePrefs({ voice_language: langSelect.value, voice_name: "" });
      renderVoicePrefs();
    };

    // ── voice list, filtered by the chosen language
    const selectedLang = langSelect.value;
    voiceSelect.innerHTML = "";
    if (!hdBox.checked || !hd.enabled) {
      voiceSelect.append(A.el("option", { value: "", text: "Device voices only" }));
      voiceSelect.disabled = true;
      return;
    }
    voiceSelect.disabled = false;
    voiceSelect.append(A.el("option", { value: "", text: "Auto (best match)" }));
    let pool = S.hdVoices;
    if (selectedLang !== "auto") {
      const matches = pool.filter((v) => v.lang === selectedLang);
      if (matches.length) pool = matches;
    } else {
      // Keep the list short and useful: the curated popular voices first.
      const popular = pool.filter((v) => /Sardor|Madina|AndrewMultilingual|EmmaMultilingual|RyanNeural|SoniaNeural|Dmitry|Svetlana|Ahmet|Emel/.test(v.short));
      pool = popular.length ? popular : pool.slice(0, 24);
    }
    for (const voice of pool) {
      voiceSelect.append(A.el("option", {
        value: voice.short,
        text: `${voice.name} · ${voice.locale}`,
      }));
    }
    const wanted = prefs.voice_name || "";
    voiceSelect.value = pool.some((v) => v.short === wanted) ? wanted : "";
    voiceSelect.onchange = () => {
      savePrefs({ voice_name: voiceSelect.value });
      if (voiceSelect.value) A.previewVoice(voiceSelect.value);
    };

    // ── strong language
    const strong = $("#allow-strong-language");
    strong.checked = Boolean(prefs.allow_strong_language);
    strong.onchange = () => savePrefs({ allow_strong_language: strong.checked });

    // ── exam mode
    const preset = $("#exam-preset");
    const exam = prefs.exam || {};
    preset.value = exam.enabled ? (exam.preset || "ielts") : "off";
    const topic = $("#exam-topic");
    topic.value = exam.topic || "";
    topic.classList.toggle("hidden", preset.value !== "custom");
    preset.onchange = () => {
      const value = preset.value;
      topic.classList.toggle("hidden", value !== "custom");
      savePrefs({ exam: { enabled: value !== "off", preset: value === "off" ? "ielts" : value } });
    };
    topic.onchange = () => savePrefs({ exam: { enabled: true, preset: "custom", topic: topic.value.trim() } });
  }
  A.renderVoicePrefs = renderVoicePrefs;

  /* ─────────────────────────── Teach mode ─────────────────────────── */

  const teach = { sessionId: null, questions: [], index: 0, saving: false };

  A.startTeach = async function startTeach() {
    try {
      const data = await A.api("/api/teach/start", { method: "POST" });
      teach.sessionId = data.session_id;
      teach.questions = data.questions;
      teach.index = 0;
      openTeachStep();
    } catch (err) {
      A.toast(err.detail || "Could not start the interview", { type: "err" });
    }
  };

  function openTeachStep() {
    const question = teach.questions[teach.index];
    const total = teach.questions.length;
    const progress = A.el("div", { class: "teach-progress" });
    for (let i = 0; i < total; i += 1) {
      progress.append(A.el("span", { class: `teach-dot${i < teach.index ? " done" : i === teach.index ? " now" : ""}` }));
    }
    const input = A.el("input", {
      type: "text", placeholder: question.hint || "Your answer", maxlength: "400",
      onkeydown: (event) => { if (event.key === "Enter") submit(); },
    });
    const wrap = A.el("div", { class: "modal soft", id: "teach-modal" }, [
      A.el("div", { class: "modal-inner teach-card" }, [
        A.el("div", { class: "teach-head" }, [
          A.el("span", { class: "teach-ic", html: A.icon("wand", 16) }),
          A.el("strong", { text: "Teach Aether" }),
          A.el("span", { class: "muted small-text", text: `${teach.index + 1} of ${total}` }),
        ]),
        progress,
        A.el("h3", { text: question.prompt }),
        input,
        A.el("p", { class: "muted tiny", text: "Saved as a memory you can edit or delete below." }),
        A.el("div", { class: "dialog-actions" }, [
          A.el("button", {
            class: "btn ghost", text: teach.index ? "Back" : "Cancel",
            onclick: () => {
              if (teach.index) { teach.index -= 1; replace(wrap, openTeachStep); return; }
              wrap.remove();
              finishTeach(true);
            },
          }),
          A.el("button", {
            class: "btn ghost", text: "Skip", onclick: () => advance(wrap),
          }),
          A.el("button", { class: "btn primary", text: "Save & continue", onclick: () => submit() }),
        ]),
      ]),
    ]);
    function replace(node, fn) { node.remove(); fn(); }
    async function submit() {
      const answer = input.value.trim();
      if (!answer) { advance(wrap); return; }
      await saveAnswer(question.id, answer);
      advance(wrap);
    }
    async function advance(node) {
      node.remove();
      teach.index += 1;
      if (teach.index >= teach.questions.length) { await finishTeach(false); return; }
      openTeachStep();
    }
    document.body.append(wrap);
    input.focus();
  }

  async function saveAnswer(questionId, answer) {
    try {
      await A.api("/api/teach/answer", {
        method: "POST",
        body: { session_id: teach.sessionId, question_id: questionId, answer },
      });
      // Show it landing immediately — the modal promises "saved as a memory".
      await refreshMemories();
      A.emit("memory:changed");
    } catch (err) {
      A.toast(err.detail || "Could not save that answer", { type: "err" });
    }
  }

  async function refreshMemories() {
    try {
      const memory = await A.api("/api/settings/memory");
      S.memories = memory.memories || [];
      $("#auto-memory").checked = Boolean(memory.auto);
      renderMemories();
    } catch { /* the list refreshes when Settings is next loaded */ }
  }
  A.refreshMemories = refreshMemories;

  async function finishTeach(cancelled) {
    const toast = A.toast(cancelled ? "Interview closed" : "Tidying up what I learned…",
                          { timeout: 0 });
    try {
      const result = await A.api("/api/teach/finish", {
        method: "POST", body: { session_id: teach.sessionId },
      });
      toast.dismiss();
      const saved = result.answered || 0;
      A.toast(cancelled ? `${saved} thing(s) saved` : `Thanks! I'll remember that (${saved} memories)`);
    } catch {
      toast.dismiss();
      A.toast("Saved — I'll use this in future answers.");
    }
    A.loadSettings();
  }

  /* ──────────────────────────── wiring ───────────────────────────── */

  A.initSettings = function initSettings() {
    $("#instructions-save").onclick = async () => {
      try {
        await A.api("/api/settings/instructions", {
          method: "PUT", body: { text: $("#instructions").value },
        });
        A.toast("Instructions saved");
      } catch (err) { A.toast(err.detail || "Could not save", { type: "err" }); }
    };
    $("#auto-memory").onchange = async (event) => {
      await A.api("/api/settings/memory/auto", {
        method: "PUT", body: { enabled: event.target.checked },
      }).catch(() => {});
    };
    $("#memory-add").onclick = async () => {
      const input = $("#memory-new");
      const value = input.value.trim();
      if (!value) return;
      try {
        await A.api("/api/settings/memory", { method: "POST", body: { content: value } });
        input.value = "";
        A.toast("Memory added");
        A.loadSettings();
      } catch (err) { A.toast(err.detail || "Could not add that", { type: "err" }); }
    };
    $("#persona-add").onclick = async () => {
      const name = $("#persona-name").value.trim();
      const prompt = $("#persona-prompt").value.trim();
      if (!name || !prompt) { A.toast("Name and description are both needed", { type: "err" }); return; }
      try {
        await A.api("/api/settings/personas", { method: "POST", body: { name, prompt } });
        $("#persona-name").value = "";
        $("#persona-prompt").value = "";
        A.toast("Persona added");
        A.loadSettings();
      } catch (err) { A.toast(err.detail || "Could not save the persona", { type: "err" }); }
    };
    $("#teach-start").onclick = () => A.startTeach();
    for (const id of ["#theme-toggle", "#theme-toggle-settings"]) {
      const btn = $(id);
      if (btn) btn.onclick = () => {
        const theme = A.theme.toggle();
        renderThemeButton();
        A.toast(theme === "dark" ? "Dark theme" : "Light theme", { timeout: 1800 });
      };
    }
    renderThemeButton();
    $("#export-all").onclick = () => {
      window.open("/api/settings/export", "_blank");
      A.toast("Export started");
    };
    $("#logout-btn").onclick = async () => {
      await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
      A.session.clear();
      location.reload();
    };
    $("#usage-refresh").onclick = () => A.refreshUsage(true);
  };

  function renderThemeButton() {
    const dark = A.theme.get() === "dark";
    const html = `${A.icon(dark ? "moon" : "sun", 14)}<span>${dark ? "Dark" : "Light"}</span>`;
    for (const id of ["#theme-toggle", "#theme-toggle-settings"]) {
      const btn = $(id);
      if (btn) btn.innerHTML = html;
    }
  }
  A.renderThemeButton = renderThemeButton;

  /* ─────────────────────── usage meter (global) ───────────────────── */

  A.refreshUsage = async function refreshUsage(force = false) {
    try {
      S.usage = await A.api("/api/usage");
      const meter = $("#sb-meter-fill");
      const text = $("#sb-meter-text");
      const toggle = $("#sb-meter");
      if (meter && text) {
        const { used_today: used, limit } = S.usage;
        meter.style.width = `${Math.min(100, Math.round((used / limit) * 100))}%`;
        text.textContent = `${used} / ${limit} today`;
        toggle?.classList.toggle("low", used / limit > 0.8);
      }
      renderUsage();
      const storage = S.usage.storage;
      const banner = $("#storage-banner");
      if (banner && storage) {
        if (!storage.persistent || storage.warning) {
          banner.textContent = storage.warning
            || "Storage is temporary in this environment — chats may not persist.";
          banner.classList.remove("hidden");
        } else {
          banner.classList.add("hidden");
        }
      }
      A.emit("usage:updated", S.usage);
    } catch (err) {
      if (force) A.toast(err.message, { type: "err" });
    }
  };
})();
