/* ══════════════════════════════════════════════════════════════════════
   Aether — chat
   Streaming answers with a real Stop button, Regenerate, Edit-and-resend,
   copy everywhere, follow-up chips, teach-after-answer and honest error
   states (retry + live cooldown countdown).
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function chat() {
  const A = window.Aether;
  const { $, esc } = A;

  const S = {
    conversationId: null,
    conversations: [],
    streaming: false,
    controller: null,
    assistantId: null,
    buffer: "",
    stopped: false,
    lastUserMessageId: null,
    lastUserText: "",
    mode: "ai",
    attachedDoc: null,
    attachedImage: null,
    autoRetries: 0,
    searchTimer: null,
    flags: { deep: false, reason: false },
  };
  A.chatState = S;

  const chipsRow = () => $("#followup-chips");

  /* ───────────────────────── conversations ───────────────────────── */

  A.loadConversations = async function loadConversations() {
    try {
      S.conversations = await A.api("/api/conversations");
      renderConversations();
    } catch (err) {
      if (err.status !== 401) console.warn("[aether] conversations", err);
    }
  };

  function renderConversations() {
    const list = $("#conv-list");
    if (!list) return;
    list.innerHTML = "";
    if (!S.conversations.length) {
      list.append(A.el("p", { class: "sb-empty", text: "No chats yet — start one above." }));
      return;
    }
    for (const conv of S.conversations) {
      const item = A.el("div", {
        class: "conv-item" + (conv.id === S.conversationId ? " active" : ""),
        role: "button", tabindex: "0",
        onclick: () => A.openConversation(conv.id),
        onkeydown: (event) => { if (event.key === "Enter") A.openConversation(conv.id); },
      }, [
        A.el("span", { class: "conv-title", text: (conv.pinned ? "· " : "") + conv.title }),
        A.el("span", { class: "conv-acts" }, [
          iconAction("pin", "Pin", () => patchConv(conv.id, { pinned: !conv.pinned })),
          iconAction("folder", "Folder", async () => {
            const folder = await A.prompt({
              title: "Move to folder", label: "Leave empty for General",
              value: conv.folder || "", okLabel: "Move",
            });
            if (folder !== null) patchConv(conv.id, { folder });
          }),
          iconAction("link", "Share link", async () => {
            try {
              const data = await A.api(`/api/conversations/${conv.id}/share`, { method: "POST" });
              A.copy(`${location.origin}${data.path}`, "Read-only link copied");
            } catch (err) { A.toast(err.detail || "Could not create a link", { type: "err" }); }
          }),
          iconAction("download", "Export .md", () => {
            window.open(`/api/conversations/${conv.id}/export?fmt=md`, "_blank");
          }),
          iconAction("trash", "Delete", async () => {
            const ok = await A.confirm({
              title: "Delete this chat?",
              message: `"${conv.title}" and its messages will be removed.`,
              okLabel: "Delete", danger: true,
            });
            if (!ok) return;
            await A.api(`/api/conversations/${conv.id}`, { method: "DELETE" });
            if (S.conversationId === conv.id) A.newChat();
            A.loadConversations();
          }, true),
        ]),
      ]);
      list.append(item);
    }
  }

  function iconAction(name, title, handler, danger = false) {
    return A.el("button", {
      class: `conv-act${danger ? " danger" : ""}`, title, "aria-label": title,
      html: A.icon(name, 14),
      onclick: (event) => { event.stopPropagation(); handler(); },
    });
  }

  async function patchConv(id, payload) {
    try {
      await A.api(`/api/conversations/${id}`, { method: "PATCH", body: payload });
      A.loadConversations();
    } catch (err) { A.toast(err.detail || "Could not update the chat", { type: "err" }); }
  }

  A.newChat = function newChat() {
    if (S.streaming) A.stopStreaming();
    S.conversationId = null;
    S.lastUserMessageId = null;
    document.title = "Aether";
    const box = $("#messages");
    box.innerHTML = "";
    box.append(hero());
    renderConversations();
    $("#composer-hint")?.classList.remove("hidden");
  };

  A.openConversation = async function openConversation(id) {
    if (S.streaming) A.stopStreaming();
    S.conversationId = id;
    const box = $("#messages");
    box.innerHTML = A.skeleton("message") + A.skeleton("message");
    A.showPane("chat");
    try {
      const conv = await A.api(`/api/conversations/${id}`);
      const meta = S.conversations.find((c) => c.id === id);
      document.title = `${meta?.title || conv.title || "Chat"} · Aether`;
      box.innerHTML = "";
      const messages = conv.messages || [];
      if (!messages.length) box.append(hero());
      let lastUserId = null;
      for (const message of messages) {
        if (message.role === "user") {
          lastUserId = message.id;
          appendUserMessage(message.content, message, message.id ===
            (messages.filter((m) => m.role === "user").slice(-1)[0] || {}).id);
        } else {
          appendAssistantMessage(message.content, { id: message.id, meta: message.meta });
        }
      }
      S.lastUserMessageId = lastUserId;
      A.scrollToBottom(true);
      renderConversations();
    } catch (err) {
      box.innerHTML = "";
      box.append(errorCard(err, () => A.openConversation(id)));
    }
  };

  /* ─────────────────────────── rendering ─────────────────────────── */

  function hero() {
    const wrap = A.el("div", { class: "hero" }, [
      A.el("div", { class: "hero-mark", html: A.icon("logo", 34) }),
      A.el("h2", { text: "What should we dig into?" }),
      A.el("p", {
        class: "muted",
        text: "Free, keyless AI — chat, voice calls, images and presentations.",
      }),
    ]);
    const chips = [
      ["Explain something simply", "Explain quantum computing like I'm 12"],
      ["Quiz me for a test", "Quiz me on World War 2 — 5 questions, one at a time."],
      ["Draft an email", "Write a polite email asking my teacher for feedback on my project"],
      ["Plan my week", "Give me a study plan for a busy student, 20 minutes a day"],
    ];
    const row = A.el("div", { class: "hero-chips" });
    for (const [label, fill] of chips) {
      row.append(A.el("button", {
        class: "chip", text: label,
        onclick: () => { $("#chat-input").value = fill; A.autoGrow($("#chat-input")); $("#chat-input").focus(); },
      }));
    }
    wrap.append(row);
    return wrap;
  }
  A.chatHero = hero;

  function avatar() {
    return A.el("div", { class: "avatar", html: A.icon("logo", 16) });
  }

  function appendUserMessage(text, meta = {}, editable = false) {
    const box = $("#messages");
    box.querySelector(".hero")?.remove();
    const bubble = A.el("div", { class: "bubble" });
    bubble.append(A.el("div", { class: "content", text }));
    if (meta?.image) bubble.append(A.el("span", { class: "msg-tag", text: "image attached" }));
    if (meta?.doc) bubble.append(A.el("span", { class: "msg-tag", text: "document attached" }));
    const acts = A.el("div", { class: "bubble-acts" });
    acts.append(A.el("button", {
      class: "msg-act", title: "Copy", html: A.icon("copy", 13),
      onclick: () => A.copy(text),
    }));
    if (editable) {
      acts.append(A.el("button", {
        class: "msg-act", title: "Edit and resend", html: A.icon("edit", 13),
        onclick: () => startEdit(bubble, text),
      }));
    }
    const wrap = A.el("div", { class: "msg user" }, [A.el("div", { class: "bubble-wrap" }, [bubble, acts])]);
    box.append(wrap);
    return wrap;
  }

  function appendAssistantMessage(text, { id = null, live = false, meta = {} } = {}) {
    const box = $("#messages");
    box.querySelector(".hero")?.remove();
    const content = A.el("div", { class: "content" });
    if (live) content.innerHTML = text;
    else content.innerHTML = A.markdown(text);
    const metaRow = A.el("div", { class: "meta" }, [
      A.el("span", { class: "who", text: "Aether" }),
      meta?.polished ? A.el("span", { class: "badge", text: "polished" }) : null,
      meta?.stopped ? A.el("span", { class: "badge warn", text: "stopped" }) : null,
    ]);
    const acts = A.el("div", { class: "msg-acts" });
    const bubble = A.el("div", { class: "bubble" }, [metaRow, content, acts]);
    const wrap = A.el("div", { class: "msg assistant" }, [avatar(), bubble]);
    wrap.dataset.messageId = id || "";
    box.append(wrap);
    wrap.chat = { content, acts, bubble, text };
    if (!live) decorateAssistant(wrap, text, id, meta);
    return wrap;
  }
  A.appendAssistantMessage = appendAssistantMessage;

  function decorateAssistant(wrap, text, id, meta = {}) {
    const { acts, content } = wrap.chat || { acts: wrap.querySelector(".msg-acts"), content: wrap.querySelector(".content") };
    acts.innerHTML = "";
    acts.append(
      actionButton("copy", "Copy", () => A.copy(text)),
      actionButton("speaker", "Read aloud", () => A.speak(text, acts)),
      actionButton("teach", "Teach Aether", () => teachPopover(acts, wrap)),
      actionButton("refresh", "Regenerate", () => regenerate(wrap)),
    );
    if (meta?.stopped) {
      acts.append(actionButton("chevron", "Continue", () => sendMessage({
        text: "Please continue exactly where you stopped.",
      })));
    }
  }

  function actionButton(name, title, handler) {
    return A.el("button", {
      class: "msg-act", title, "aria-label": title, html: A.icon(name, 13),
      onclick: handler,
    });
  }

  A.appendUserMessage = (text, meta) => appendUserMessage(text, meta || {});

  A.decorateAssistant = (wrap, text, id, meta) => decorateAssistant(wrap, text, id, meta);

  /* ───────────────────────── follow-up chips ───────────────────────── */

  function renderFollowUps() {
    const row = chipsRow();
    if (!row) return;
    row.innerHTML = "";
    const suggestions = [
      "Explain that more simply",
      "Give me an example",
      "Go deeper on the important part",
      "Quiz me on this",
      "Turn this into a study checklist",
    ];
    for (const text of suggestions.slice(0, 4)) {
      row.append(A.el("button", {
        class: "chip small", text,
        onclick: () => sendMessage({ text }),
      }));
    }
  }

  /* ─────────────────────────── streaming ─────────────────────────── */

  function setStreaming(active) {
    S.streaming = active;
    $("#send-btn")?.classList.toggle("hidden", active);
    $("#stop-btn")?.classList.toggle("hidden", !active);
    $("#composer")?.classList.toggle("busy", active);
  }
  A.setStreaming = setStreaming;

  function phase(text, phaseName = "") {
    const bar = $("#phase-bar");
    if (!bar) return;
    bar.innerHTML = "";
    bar.dataset.phase = phaseName;
    bar.append(A.el("span", { class: "spin" }), A.el("span", { class: "phase-text", text }));
    bar.classList.remove("hidden");
  }
  function phaseHide() { $("#phase-bar")?.classList.add("hidden"); }

  async function streamAnswer({ text, regenerate: regen = false, teachInstruction = null,
                               existingWrap = null }) {
    if (S.streaming) return;
    if (!S.conversationId) {
      const conv = await A.api("/api/conversations", {
        method: "POST", body: { title: (text || S.lastUserText).slice(0, 60) || "Voice chat" },
      });
      S.conversationId = conv.id;
      A.loadConversations();
    }
    const assistantId = A.uuid();
    S.assistantId = assistantId;
    S.buffer = "";
    S.stopped = false;
    const wrap = existingWrap || appendAssistantMessage("", { live: true, id: assistantId });
    wrap.chat = { ...(wrap.chat || {}), content: wrap.querySelector(".content"),
                  acts: wrap.querySelector(".msg-acts") };
    wrap.chat.content.innerHTML = A.typingDots();
    S.currentWrap = wrap;
    const controller = new AbortController();
    S.controller = controller;
    setStreaming(true);
    phase("Working out an answer…", "draft");

    let error = null;
    try {
      await A.stream("/api/chat/stream", {
        conversation_id: S.conversationId,
        message: text || "",
        regenerate: regen,
        assistant_message_id: assistantId,
        client_saves_partial: true,
        mode: S.mode,
        persona_id: $("#persona-select")?.value || null,
        reasoning: Boolean(S.flags.reason),
        force_polish: Boolean(S.flags.deep),
        doc_id: S.attachedDoc?.doc_id || null,
        image_b64: S.attachedImage || null,
        voice: Boolean(A.voiceCall?.active),
        teach_instruction: teachInstruction,
      }, {
        signal: controller.signal,
        onEvent: (event) => {
          switch (event.type) {
            case "meta":
              A.emit("usage:refresh");
              break;
            case "phase":
              if (event.phase === "queued") {
                phase(event.detail || `Free AI — about ${Math.round(event.wait_seconds)}s in the queue`, "queued");
              } else if (event.phase === "polish") {
                phase("Second pass: polishing the answer…", "polish");
              } else if (event.phase === "research") {
                phase("Reading Wikipedia & DuckDuckGo (no AI)…", "research");
              } else {
                phase("Writing…", "draft");
              }
              break;
            case "ping":
              if (!S.buffer) phase(`Still working — ${Math.round(event.elapsed)}s.`, "wait");
              break;
            case "delta":
              S.buffer += event.text;
              if (event.text) phaseHide();
              wrap.chat.content.innerHTML = A.markdown(S.buffer);
              wrap.chat.content.classList.add("streaming");
              A.scrollToBottom();
              break;
            case "final":
              if (event.text) S.buffer = event.text;
              wrap.chat.content.innerHTML = A.markdown(S.buffer);
              wrap.dataset.messageId = event.message_id || assistantId;
              break;
            case "error":
              error = new A.ApiError(event.detail || "The free AI failed.", {
                status: event.retryable ? 503 : 400,
                retryAfter: event.retry_after || 0,
              });
              break;
            case "done":
              wrap.dataset.messageId = event.message_id || assistantId;
              wrap.dataset.stopped = event.stopped ? "1" : "";
              A.emit("usage:refresh");
              break;
            default: break;
          }
        },
      });
    } catch (err) {
      if (err?.name === "AbortError") {
        // User pressed Stop — the partial answer stays, and is saved below.
      } else {
        error = err;
      }
    } finally {
      setStreaming(false);
      phaseHide();
      S.controller = null;
      wrap.chat.content.classList.remove("streaming");
      A.emit("chat:finished", { conversationId: S.conversationId });
    }

    const finishedText = (S.buffer || "").trim();
    if (S.stopped && finishedText) {
      try {
        await A.api("/api/chat/partial", {
          conversation_id: S.conversationId,
          message_id: wrap.dataset.messageId || assistantId,
          content: finishedText,
        });
      } catch { /* the answer is still on screen */ }
    }
    if (finishedText) {
      wrap.chat.content.innerHTML = A.markdown(finishedText);
      wrap.chat.text = finishedText;
      decorateAssistant(wrap, finishedText, wrap.dataset.messageId, { stopped: S.stopped });
      wrap.chat.acts.querySelectorAll(".msg-act")[0]?.setAttribute("title", "Copy");
      renderFollowUps();
      const bubble = wrap.querySelector(".bubble");
      if (S.stopped) bubble.classList.add("was-stopped");
      A.emit("message:saved", { conversationId: S.conversationId, text: finishedText });
    } else if (!error && !S.stopped) {
      wrap.chat.content.innerHTML = '<p class="muted">The AI returned an empty answer. Try again.</p>';
    }

    if (error) showStreamError(wrap, error, { text, regenerate: regen, teachInstruction });
    else A.loadConversations();
  }
  A.streamAnswer = streamAnswer;

  function showStreamError(wrap, error, retryArgs) {
    const content = wrap.chat?.content || wrap.querySelector(".content");
    const hadText = Boolean((S.buffer || "").trim());
    const card = errorCard(error, () => {
      card.remove();
      streamAnswer({ ...retryArgs, existingWrap: hadText ? null : wrap });
    }, { hadText });
    content.append(card);
    A.toast(error.message, {
      type: "err",
      action: { label: "Retry", onClick: () => { card.remove(); streamAnswer(retryArgs); } },
    });
    // Free tier cooling down: count down in the UI and auto-retry once.
    if (error.retryAfter > 0 && S.autoRetries < 2) {
      S.autoRetries += 1;
      countdownInto(card.querySelector(".retry-hint"), error.retryAfter, () => {
        card.remove();
        streamAnswer(retryArgs);
      });
    }
  }

  function errorCard(error, onRetry, { hadText = false } = {}) {
    const card = A.el("div", { class: "error-card" }, [
      A.el("div", { class: "error-head", html: A.icon("bolt", 15) }),
      A.el("div", { class: "error-body" }, [
        A.el("strong", { text: hadText ? "The answer stopped early" : "That didn’t work" }),
        A.el("p", { class: "muted small-text", text: error.message || String(error) }),
        A.el("span", { class: "retry-hint muted small-text" }),
      ]),
      A.el("button", { class: "btn small-btn primary", text: "Retry", onclick: onRetry }),
    ]);
    return card;
  }
  A.errorCard = errorCard;

  function countdownInto(node, seconds, done) {
    if (!node) return;
    let left = seconds;
    const render = () => {
      node.textContent = left > 0
        ? `Free shared AI — retrying in ${A.formatCountdown(left)}`
        : "Retrying…";
    };
    render();
    const timer = setInterval(() => {
      left -= 1;
      render();
      if (left <= 0) { clearInterval(timer); done?.(); }
    }, 1000);
  }

  /* ──────────────────────────── actions ───────────────────────────── */

  A.stopStreaming = function stopStreaming() {
    if (!S.streaming) return;
    S.stopped = true;
    S.controller?.abort();
    setStreaming(false);
    phaseHide();
    A.toast("Stopped — the partial answer is kept.", { type: "info", timeout: 2600 });
  };

  function regenerate(wrap) {
    if (S.streaming) return;
    const previous = wrap.text || "";
    wrap.remove();
    streamAnswer({ text: "", regenerate: true, keepContext: true });
    if (previous) A.emit("regenerate:started");
  }

  function startEdit(bubble, originalText) {
    if (S.streaming) return;
    const wrap = bubble.closest(".msg.user");
    const area = A.el("textarea", { class: "edit-area", rows: 3, value: originalText });
    const save = A.el("button", { class: "btn primary small-btn", text: "Save & resend" });
    const cancel = A.el("button", { class: "btn ghost small-btn", text: "Cancel" });
    bubble.innerHTML = "";
    bubble.append(area, A.el("div", { class: "edit-actions" }, [save, cancel]));
    area.focus();
    A.autoGrow(area);
    area.addEventListener("input", () => A.autoGrow(area));
    cancel.onclick = () => { wrap.replaceWith(...[]); A.openConversation(S.conversationId); };
    save.onclick = async () => {
      const edited = area.value.trim();
      if (!edited) return;
      const messageId = wrap.dataset.messageId || S.lastUserMessageId;
      try {
        if (messageId) {
          await A.api(`/api/conversations/${S.conversationId}/truncate`, {
            method: "POST", body: { message_id: messageId },
          });
        }
      } catch (err) {
        A.toast(err.detail || "Could not rewind the chat", { type: "err" });
        return;
      }
      // Drop this message and everything after it, then send the edit.
      let node = wrap;
      while (node) {
        const next = node.nextElementSibling;
        node.remove();
        node = next;
      }
      await sendMessage({ text: edited });
    };
  }

  function teachPopover(anchor, wrap) {
    document.querySelector(".teach-pop")?.remove();
    const input = A.el("input", {
      type: "text", placeholder: "e.g. keep it shorter, show the steps first",
      maxlength: "500",
    });
    const reanswer = A.el("input", { type: "checkbox", id: "teach-reanswer" });
    const pop = A.el("div", { class: "teach-pop" }, [
      A.el("strong", { text: "Teach Aether" }),
      A.el("p", { class: "muted small-text", text: "Saved to memory and applied to future answers." }),
      input,
      A.el("label", { class: "teach-check", for: "teach-reanswer" }, [
        reanswer, A.el("span", { text: "Re-answer this question with the correction" }),
      ]),
      A.el("div", { class: "teach-actions" }, [
        A.el("button", { class: "btn ghost small-btn", text: "Cancel", onclick: () => pop.remove() }),
        A.el("button", {
          class: "btn primary small-btn", text: "Save",
          onclick: async () => {
            const instruction = input.value.trim();
            if (!instruction) return;
            try {
              await A.api("/api/teach/improve", {
                method: "POST", body: { instruction, question: S.lastUserText.slice(0, 200) },
              });
              A.toast("Learned — thanks for the correction");
              pop.remove();
              A.emit("memory:changed");
              if (reanswer.checked) {
                wrap.remove();
                streamAnswer({ text: "", regenerate: true, teachInstruction: instruction });
              }
            } catch (err) {
              A.toast(err.detail || "Could not save that", { type: "err" });
            }
          },
        }),
      ]),
    ]);
    anchor.closest(".bubble").append(pop);
    input.focus();
    const close = (event) => {
      if (!pop.contains(event.target)) { pop.remove(); document.removeEventListener("click", close); }
    };
    setTimeout(() => document.addEventListener("click", close), 0);
  }

  /* ──────────────────────────── composer ─────────────────────────── */

  A.sendMessage = sendMessage;
  async function sendMessage({ text = null, regenerate: regen = false } = {}) {
    const input = $("#chat-input");
    const value = (text ?? input.value).trim();
    if (S.streaming) return;
    if (!value && !regen) return;
    if (!regen) {
      input.value = "";
      A.autoGrow(input);
      const wrap = appendUserMessage(value, {
        image: Boolean(S.attachedImage), doc: Boolean(S.attachedDoc),
      }, true);
      wrap.dataset.messageId = "";
      S.lastUserText = value;
      S.attachedDoc = null;
      S.attachedImage = null;
      renderAttachments();
      A.scrollToBottom(true);
      const created = await ensureConversation(value);
      if (!created) return;
      // The server returns the user message id in the conversation record; keep
      // the DOM id in sync so Edit can truncate precisely.
      try {
        const messages = await A.api(`/api/conversations/${S.conversationId}/messages`);
        const lastUser = [...messages].reverse().find((m) => m.role === "user");
        if (lastUser) {
          wrap.dataset.messageId = lastUser.id;
          S.lastUserMessageId = lastUser.id;
        }
      } catch { /* edit will fall back to the last known id */ }
    }
    S.autoRetries = 0;
    await streamAnswer({ text: value, regenerate: regen });
  }

  async function ensureConversation(title) {
    if (S.conversationId) return true;
    try {
      const conv = await A.api("/api/conversations", {
        method: "POST", body: { title: title.slice(0, 60) },
      });
      S.conversationId = conv.id;
      document.title = `${title.slice(0, 40)} · Aether`;
      A.loadConversations();
      return true;
    } catch (err) {
      A.toast(err.detail || "Could not start a chat", { type: "err" });
      return false;
    }
  }

  /* ───────────────────────── attachments ─────────────────────────── */

  function renderAttachments() {
    const box = $("#attachments");
    if (!box) return;
    box.innerHTML = "";
    const items = [];
    if (S.attachedDoc) items.push(["doc", `PDF · ${S.attachedDoc.name}`, A.icon("pdf", 14)]);
    if (S.attachedImage) items.push(["img", "Image attached", A.icon("camera", 14)]);
    for (const [key, label, icon] of items) {
      box.append(A.el("span", { class: "chip attachment" }, [
        A.el("span", { class: "chip-ic", html: icon }),
        A.el("span", { text: label }),
        A.el("button", {
          class: "chip-x", "aria-label": "Remove", html: A.icon("x", 12),
          onclick: () => {
            if (key === "doc") S.attachedDoc = null; else S.attachedImage = null;
            renderAttachments();
          },
        }),
      ]));
    }
    box.classList.toggle("hidden", !items.length);
  }

  /* ─────────────────────────── wiring ────────────────────────────── */

  A.initChat = function initChat() {
    // A brand-new user lands on an empty pane: give them the hero, prompts
    // and the composer hint instead of a blank column.
    if (!$("#messages").children.length) A.newChat();
    const input = $("#chat-input");
    A.autoGrow(input);
    input.addEventListener("input", () => A.autoGrow(input));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });
    $("#send-btn").onclick = () => sendMessage();
    $("#stop-btn").onclick = () => A.stopStreaming();
    $("#new-chat").onclick = () => { A.newChat(); A.closeSidebar(); };

    // answer mode (AI / research)
    const setMode = (mode) => {
      S.mode = mode;
      $("#mode-ai").classList.toggle("active", mode === "ai");
      $("#mode-research").classList.toggle("active", mode === "research");
      $("#tools-dot").classList.toggle("hidden", mode === "ai" &&
        !S.flags.deep && !S.flags.reason && !$("#persona-select").value);
    };
    $("#mode-ai").onclick = () => setMode("ai");
    $("#mode-research").onclick = () => setMode("research");
    A.setAnswerMode = setMode;

    const toggleSwitch = (buttonId, flag, swId) => {
      $(buttonId).onclick = () => {
        S.flags[flag] = !S.flags[flag];
        $(swId).classList.toggle("on", S.flags[flag]);
        setMode(S.mode);
      };
    };
    toggleSwitch("#tool-deep", "deep", "#sw-deep");
    toggleSwitch("#tool-reason", "reason", "#sw-reason");
    $("#persona-select").onchange = () => setMode(S.mode);

    $("#tools-btn").onclick = (event) => {
      event.stopPropagation();
      $("#tools-menu").classList.toggle("hidden");
    };
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".tools-anchor")) $("#tools-menu").classList.add("hidden");
    });
    $("#tool-pdf").onclick = () => { $("#tools-menu").classList.add("hidden"); $("#pdf-input").click(); };
    $("#tool-image").onclick = () => { $("#tools-menu").classList.add("hidden"); $("#image-input").click(); };

    $("#pdf-input").onchange = async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const toast = A.toast(`Reading ${file.name}…`, { timeout: 0 });
      try {
        const data = await A.api("/api/files/extract-pdf", { method: "POST", form });
        S.attachedDoc = { doc_id: data.doc_id, name: data.name };
        renderAttachments();
        toast.dismiss();
        A.toast("PDF attached");
      } catch (err) {
        toast.dismiss();
        A.toast(err.detail || "Could not read that PDF", { type: "err" });
      }
      event.target.value = "";
    };
    $("#image-input").onchange = (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 4 * 1024 * 1024) {
        A.toast("Image too large (max 4 MB)", { type: "err" });
        return;
      }
      const reader = new FileReader();
      reader.onload = () => { S.attachedImage = reader.result; renderAttachments(); };
      reader.readAsDataURL(file);
      event.target.value = "";
    };

    // search
    $("#chat-search").addEventListener("input", (event) => {
      clearTimeout(S.searchTimer);
      const query = event.target.value.trim();
      const box = $("#search-results");
      if (query.length < 2) { box.classList.add("hidden"); return; }
      S.searchTimer = setTimeout(async () => {
        try {
          const hits = await A.api(`/api/chats/search?q=${encodeURIComponent(query)}`);
          box.innerHTML = "";
          if (!hits.length) {
            box.append(A.el("p", { class: "muted small-text pad", text: "No matches." }));
          }
          for (const hit of hits) {
            box.append(A.el("button", {
              class: "search-hit",
              onclick: () => {
                box.classList.add("hidden");
                $("#chat-search").value = "";
                A.openConversation(hit.conversation_id);
              },
            }, [
              A.el("b", { text: hit.title || "Chat" }),
              A.el("span", { class: "muted small-text", text: (hit.content || "").slice(0, 90) }),
            ]));
          }
          box.classList.remove("hidden");
        } catch { box.classList.add("hidden"); }
      }, 280);
    });

    // persona select in the tools menu
    A.loadPersonas?.();

    document.addEventListener("aether:keydown", () => {});
    $("#messages").addEventListener("click", (event) => {
      const copy = event.target.closest(".code-copy");
      if (!copy) return;
      const code = A.markdownCode(Number(copy.dataset.codeIdx));
      A.copy(code, "Code copied");
    });
  };
})();
