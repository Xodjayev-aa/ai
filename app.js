(function () {
  const api = window.AetherAPI;
  const md = window.AetherMarkdown;
  const state = {
    user: null,
    config: null,
    conversations: [],
    currentId: null,
    mode: "auto",
    files: [],
    sending: false,
    authMode: "login",
    pane: "chat",
    lastReply: "",
    trainingOn: false,
    consensus: false,
    talk: false,
    tone: "standard",
    voiceURI: "",
  };

  const $ = (id) => document.getElementById(id);
  const show = (el) => el && el.classList.remove("hidden");
  const hide = (el) => el && el.classList.add("hidden");

  function setView(name) {
    hide($("boot"));
    hide($("view-auth"));
    hide($("view-pending"));
    hide($("view-app"));
    show($(name));
  }

  function toastError(el, message) {
    if (!el) return;
    el.hidden = !message;
    el.textContent = message || "";
  }

  let toastTimer = 0;
  function toast(message, kind) {
    const el = $("toast");
    if (!el || !message) return;
    el.textContent = message;
    el.classList.remove("hidden", "err", "ok");
    if (kind) el.classList.add(kind);
    el.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("on");
      setTimeout(() => el.classList.add("hidden"), 220);
    }, 2800);
  }

  function isOwner(user) {
    return !!(user && user.role === "owner");
  }

  function graceful(err, opts) {
    const raw = api.humanize((err && err.message) || "Something went wrong. Try again.");
    if (err && err.status === 401) {
      if (!(opts && opts.keepSession)) api.setToken("");
      if (!(opts && opts.stay)) setView("view-auth");
      return raw || "Sign in required.";
    }
    if (err && err.status === 403) return raw || "You do not have access.";
    return raw;
  }

  function pref(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : v;
    } catch (_) {
      return fallback;
    }
  }

  function setPref(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (_) {}
  }

  function applyChatPrefs() {
    state.consensus = pref("aether.consensus", "0") === "1";
    state.talk = pref("aether.talk", "0") === "1";
    state.tone = pref("aether.tone", "standard") === "unfiltered" ? "unfiltered" : "standard";
    state.voiceURI = pref("aether.voice", "");
    const cons = $("toggle-consensus");
    const talk = $("toggle-talk");
    if (cons) cons.classList.toggle("on", state.consensus);
    if (talk) talk.classList.toggle("on", state.talk);
    const talkSel = $("talk-select");
    if (talkSel) talkSel.value = state.talk ? "on" : "off";
    document.querySelectorAll("[data-tone]").forEach((b) => {
      b.classList.toggle("on", b.getAttribute("data-tone") === state.tone);
    });
    fillVoices();
  }

  function fillVoices() {
    const sel = $("voice-select");
    if (!sel) return;
    const speech = window.AetherSpeech;
    const voices = speech ? speech.listVoices() : (window.speechSynthesis && window.speechSynthesis.getVoices()) || [];
    const prev = sel.value || state.voiceURI;
    sel.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = "Auto — best neural voice";
    sel.appendChild(auto);
    voices.forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.voiceURI;
      const neural = speech && speech.scoreVoice(v) >= 40;
      opt.textContent = (neural ? "● " : "") + v.name + (v.lang ? " (" + v.lang + ")" : "");
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  }

  async function boot() {
    applyTheme(localStorage.getItem(api.THEME_KEY) || "dark");
    applyChatPrefs();
    try {
      state.config = await api.request("/api/config/public");
    } catch (_) {
      state.config = { app: "Aether", provider: false };
    }
    document.querySelectorAll("#auth-app-name, #app-name").forEach((n) => {
      n.textContent = state.config.app || "Aether";
    });
    if (!api.getToken()) {
      setView("view-auth");
      return;
    }
    try {
      const me = await api.request("/api/auth/me");
      await enter(me.user);
    } catch (err) {
      api.setToken("");
      setView("view-auth");
    }
  }

  async function enter(user) {
    state.user = user;
    if (user.account_status !== "approved") {
      $("pending-email").textContent = user.email;
      setView("view-pending");
      return;
    }
    $("user-name").textContent = user.display_name || user.email.split("@")[0];
    $("user-email").textContent = user.email;
    $("user-avatar").textContent = (user.display_name || user.email || "A").slice(0, 1).toUpperCase();
    const owner = isOwner(user);
    $("open-admin").hidden = !owner;
    $("nav-admin").hidden = !owner;
    const eng = $("engine-status");
    if (eng) {
      eng.innerHTML =
        "<i></i>" +
        ((state.config && state.config.engine) || "free") +
        " · " +
        ((state.config && state.config.mode) || "local");
    }
    $("settings-name").value = user.display_name || "";
    setView("view-app");
    showPane("chat");
    await refreshConversations();
    newChat();
  }

  $("auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    toastError($("auth-error"), "");
    const email = $("auth-email").value.trim();
    const password = $("auth-password").value;
    const display_name = $("auth-name").value.trim();
    $("auth-submit").disabled = true;
    try {
      const path = state.authMode === "signup" ? "/api/auth/signup" : "/api/auth/login";
      const body = state.authMode === "signup" ? { email, password, display_name } : { email, password };
      const data = await api.request(path, { method: "POST", body: JSON.stringify(body) });
      if (!data || !data.token || !data.user) {
        throw new Error("Sign in did not return a session. Try again.");
      }
      api.setToken(data.token);
      await enter(data.user);
    } catch (err) {
      let msg = graceful(err, { stay: true, keepSession: true });
      if (err && err.status === 401) {
        msg = "Wrong email or password.";
        if (state.authMode === "login") {
          msg += " If this is the owner email, open Create account to set a new password.";
        }
      }
      toastError($("auth-error"), msg);
    } finally {
      $("auth-submit").disabled = false;
    }
  });

  document.querySelectorAll("[data-auth-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.authMode = btn.getAttribute("data-auth-tab");
      document.querySelectorAll("[data-auth-tab]").forEach((b) => b.classList.toggle("on", b === btn));
      $("name-field").classList.toggle("hidden", state.authMode !== "signup");
      $("auth-submit").textContent = state.authMode === "signup" ? "Create account" : "Sign in";
      $("auth-password").autocomplete = state.authMode === "signup" ? "new-password" : "current-password";
    });
  });

  $("pending-refresh").addEventListener("click", async () => {
    try {
      const me = await api.request("/api/auth/me");
      await enter(me.user);
    } catch (err) {
      toastError($("auth-error"), graceful(err));
    }
  });
  $("pending-signout").addEventListener("click", signOut);
  $("signout").addEventListener("click", signOut);

  async function signOut() {
    try {
      await api.request("/api/auth/logout", { method: "POST" });
    } catch (_) {}
    api.setToken("");
    state.user = null;
    state.currentId = null;
    if (window.AetherSpeech) window.AetherSpeech.stop();
    setView("view-auth");
  }

  $("new-chat").addEventListener("click", () => {
    newChat();
    showPane("chat");
    closeSidebar();
  });

  function newChat() {
    state.currentId = null;
    state.files = [];
    renderChips();
    $("chat-title").textContent = "New conversation";
    $("thread").querySelectorAll("article.msg").forEach((n) => n.remove());
    show($("empty-state"));
    if (!$("thread").contains($("empty-state"))) $("thread").prepend($("empty-state"));
    document.querySelectorAll(".conv-item").forEach((n) => n.classList.remove("on"));
    $("prompt").focus();
  }

  async function refreshConversations() {
    const data = await api.request("/api/conversations");
    state.conversations = data.conversations || [];
    renderConversations($("chat-search").value);
  }

  function renderConversations(filter) {
    const q = (filter || "").toLowerCase();
    const list = $("conv-list");
    list.innerHTML = "";
    state.conversations
      .filter((c) => !q || (c.title || "").toLowerCase().includes(q))
      .forEach((c) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "conv-item" + (c.id === state.currentId ? " on" : "");
        if (c.pinned) {
          const star = document.createElement("span");
          star.className = "pin";
          star.textContent = "★ ";
          btn.appendChild(star);
        }
        const title = document.createElement("span");
        title.textContent = c.title || "Untitled";
        btn.appendChild(title);
        const small = document.createElement("small");
        small.textContent = (c.updated_at || "").replace("T", " ").slice(0, 16);
        btn.appendChild(small);
        btn.addEventListener("click", () => {
          showPane("chat");
          openConversation(c.id);
        });
        list.appendChild(btn);
      });
    if (!list.children.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.style.padding = "8px";
      empty.textContent = "No conversations yet.";
      list.appendChild(empty);
    }
  }

  $("chat-search").addEventListener("input", (e) => renderConversations(e.target.value));

  async function openConversation(id) {
    const data = await api.request("/api/conversations/" + id);
    state.currentId = id;
    $("chat-title").textContent = data.conversation.title || "Conversation";
    $("pin-chat").textContent = data.conversation.pinned ? "★" : "☆";
    const thread = $("thread");
    thread.querySelectorAll("article.msg").forEach((n) => n.remove());
    hide($("empty-state"));
    (data.messages || []).forEach((m) => {
      const cites = (m.metadata && m.metadata.citations) || [];
      appendMessage(m.role, m.content, m.content_type, false, cites);
    });
    renderConversations($("chat-search").value);
    closeSidebar();
    thread.scrollTop = thread.scrollHeight;
  }

  $("pin-chat").addEventListener("click", async () => {
    if (!state.currentId) return;
    const conv = state.conversations.find((c) => c.id === state.currentId);
    const pinned = !(conv && conv.pinned);
    await api.request("/api/conversations/" + state.currentId, {
      method: "PATCH",
      body: JSON.stringify({ pinned }),
    });
    await refreshConversations();
    $("pin-chat").textContent = pinned ? "★" : "☆";
  });

  $("delete-chat").addEventListener("click", async () => {
    if (!state.currentId) return newChat();
    if (!confirm("Delete this conversation?")) return;
    await api.request("/api/conversations/" + state.currentId, { method: "DELETE" });
    await refreshConversations();
    newChat();
  });

  document.querySelectorAll(".mode[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn.getAttribute("data-mode");
      document.querySelectorAll(".mode[data-mode]").forEach((b) => b.classList.toggle("on", b === btn));
    });
  });

  document.querySelectorAll("[data-suggest]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("prompt").value = btn.getAttribute("data-suggest");
      $("prompt").focus();
      autosize();
    });
  });

  const prompt = $("prompt");
  function autosize() {
    prompt.style.height = "auto";
    prompt.style.height = Math.min(prompt.scrollHeight, 180) + "px";
  }
  prompt.addEventListener("input", autosize);
  prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("send").addEventListener("click", send);
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      newChat();
      showPane("chat");
    }
  });

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []).slice(0, 5);
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      try {
        const data = await api.request("/api/files", { method: "POST", body: form });
        state.files.push(data.file);
        renderChips();
        setDrawer(
          "<p><strong>" +
            md.escapeHtml(data.file.filename) +
            "</strong></p><p class='muted'>" +
            (data.file.chars || 0) +
            " characters indexed.</p>"
        );
      } catch (err) {
        const msg = graceful(err);
        toast(msg, "err");
        appendMessage("assistant", msg, "text", false);
      }
    }
  }

  $("file-input").addEventListener("change", async (e) => {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    await uploadFiles(files);
  });

  const dropHost = $("pane-chat");
  ["dragenter", "dragover"].forEach((ev) => {
    dropHost.addEventListener(ev, (e) => {
      e.preventDefault();
      $("drop-mask").classList.remove("hidden");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    dropHost.addEventListener(ev, (e) => {
      e.preventDefault();
      $("drop-mask").classList.add("hidden");
    });
  });
  dropHost.addEventListener("drop", async (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) await uploadFiles(files);
  });

  function renderChips() {
    const wrap = $("file-chips");
    wrap.innerHTML = "";
    state.files.forEach((f, i) => {
      const chip = document.createElement("div");
      chip.className = "chip";
      const name = document.createElement("span");
      name.textContent = f.filename;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.textContent = "×";
      rm.addEventListener("click", () => {
        state.files.splice(i, 1);
        renderChips();
      });
      chip.appendChild(name);
      chip.appendChild(rm);
      wrap.appendChild(chip);
    });
  }

  async function send() {
    const text = prompt.value.trim();
    if ((!text && !state.files.length) || state.sending) return;
    hide($("empty-state"));
    appendMessage("user", text || "(file analysis)", "text", false);
    prompt.value = "";
    autosize();
    const fileIds = state.files.map((f) => f.id);
    state.files = [];
    renderChips();
    const assistant = appendMessage("assistant", "", "text", true);
    if (window.AetherSpeech) window.AetherSpeech.stop();
    state.sending = true;
    $("send").disabled = true;
    $("composer-hint").textContent = "thinking";
    document.querySelector(".composer")?.classList.add("busy");
    const STATUS = {
      context: "preparing",
      search: "checking live sources",
      generate: "answering",
      fallback: "switching to backup",
      throttle: "model busy — using backup",
      image: "creating image",
      consensus: "asking several models",
      synthesize: "merging the best answer",
    };
    let acc = "";
    let cites = [];
    try {
      await api.streamChat(
        {
          conversation_id: state.currentId,
          message: text || "Please analyze the attached file(s).",
          file_ids: fileIds,
          mode: state.mode,
          consensus: state.consensus,
          tone: state.tone,
        },
        (event, payload) => {
          if (event === "meta") {
            if (payload.conversation_id && payload.conversation_id !== state.currentId) {
              state.currentId = payload.conversation_id;
              $("chat-title").textContent = payload.title || "Conversation";
              refreshConversations();
            }
            if (payload.intent) $("composer-hint").textContent = payload.intent;
          } else if (event === "status") {
            const label = STATUS[payload.stage] || payload.stage || "";
            $("composer-hint").textContent = label;
          } else if (event === "citations") {
            cites = payload.items || [];
            renderAssistant(assistant, acc, true, assistant.dataset.type || "text", cites);
          } else if (event === "token") {
            const piece = payload.text || "";
            if (api.humanize(piece) !== piece && !acc) return;
            acc += piece;
            if (api.humanize(acc) !== acc && acc.length < 280) {
              acc = "";
              $("composer-hint").textContent = STATUS.throttle;
              return;
            }
            renderAssistant(assistant, acc, true, "text", cites);
          } else if (event === "image") {
            acc = payload.url || "";
            renderAssistant(assistant, acc, false, "image");
          } else if (event === "error") {
            acc = api.humanize(payload.message || "Something went wrong. Try again.");
            renderAssistant(assistant, acc, false);
          } else if (event === "done") {
            if (!acc) acc = "The main model was busy. Send the message once more.";
            renderAssistant(assistant, acc, false, assistant.dataset.type || "text", cites);
            if (payload.conversation_id) state.currentId = payload.conversation_id;
            refreshConversations();
          }
        }
      );
      if (acc) renderAssistant(assistant, acc, false, assistant.dataset.type || "text", cites);
      if (acc && assistant.dataset.type !== "image") {
        state.lastReply = acc;
        if (state.talk) speakText(acc);
      }
    } catch (err) {
      const msg = graceful(err);
      toast(msg, "err");
      renderAssistant(assistant, msg, false);
    } finally {
      state.sending = false;
      $("send").disabled = false;
      $("composer-hint").textContent = "";
      document.querySelector(".composer")?.classList.remove("busy");
    }
  }

  function appendMessage(role, content, type, streaming, citations) {
    hide($("empty-state"));
    const row = document.createElement("article");
    row.className = "msg " + (role === "user" ? "user" : "assistant");
    const av = document.createElement("div");
    av.className = "avatar";
    av.textContent = role === "user" ? (state.user.display_name || "Y").slice(0, 1).toUpperCase() : "Æ";
    const body = document.createElement("div");
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = role === "user" ? "You" : (state.config && state.config.app) || "Aether";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    body.appendChild(who);
    body.appendChild(bubble);
    row.appendChild(av);
    row.appendChild(body);
    $("thread").appendChild(row);
    const handle = { bubble, dataset: { type: type || "text" }, role };
    if (role === "user") bubble.textContent = content;
    else renderAssistant(handle, content, streaming, type, citations);
    $("thread").scrollTop = $("thread").scrollHeight;
    return handle;
  }

  function renderAssistant(handle, content, streaming, type, citations) {
    const bubble = handle.bubble;
    const kind = type || handle.dataset.type || "text";
    handle.dataset.type = kind;
    if (kind === "image" && content) {
      bubble.textContent = "";
      const url = md.safeUrl(content);
      if (url) {
        const img = document.createElement("img");
        img.className = "gen-image";
        img.alt = "Generated image";
        img.src = url;
        bubble.appendChild(img);
      } else {
        bubble.textContent = "Image URL was blocked.";
      }
    } else {
      bubble.innerHTML = md.renderMarkdown(content || "");
      if (streaming) {
        const c = document.createElement("span");
        c.className = "cursor";
        bubble.appendChild(c);
      }
      bindCodeActions(bubble);
      if (citations && citations.length) {
        const row = document.createElement("div");
        row.className = "cite-row";
        citations.forEach((item) => {
          const url = md.safeUrl(item.url || item.href || "");
          if (!url) return;
          const a = document.createElement("a");
          a.className = "cite";
          a.href = url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = item.title || "source";
          row.appendChild(a);
        });
        if (row.children.length) bubble.appendChild(row);
      }
      if (!streaming && content && kind !== "image") {
        const speak = document.createElement("button");
        speak.type = "button";
        speak.className = "speak-btn";
        speak.textContent = "Speak";
        speak.addEventListener("click", () => {
          if (window.AetherSpeech && window.AetherSpeech.speaking()) {
            window.AetherSpeech.stop();
            return;
          }
          speakText(content);
        });
        bubble.appendChild(speak);
      }
    }
    $("thread").scrollTop = $("thread").scrollHeight;
  }

  function bindCodeActions(bubble) {
    bubble.querySelectorAll(".copy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const code = btn.parentElement.querySelector("code");
        if (!code) return;
        try {
          await navigator.clipboard.writeText(code.textContent || "");
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1200);
        } catch (_) {}
      });
    });
    bubble.querySelectorAll(".run-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const pre = btn.parentElement;
        const code = (pre.querySelector("code") && pre.querySelector("code").textContent) || "";
        const lang = (btn.getAttribute("data-lang") || pre.getAttribute("data-lang") || "").toLowerCase();
        await runSnippet(lang, code);
      });
    });
  }

  async function runSnippet(lang, code) {
    const web = ["html", "css", "js", "javascript"].includes(lang);
    if (web || /<\w+/.test(code)) {
      const html =
        lang === "css"
          ? "<style>" + code + "</style>"
          : lang === "js" || lang === "javascript"
            ? "<script>" + code + "<\/script>"
            : code;
      setDrawer('<iframe sandbox="allow-scripts" class="sandbox-frame" srcdoc="' + md.escapeHtml(html) + '"></iframe>');
      showPane("canvas");
      $("cv-html").value = lang === "html" || /<\w+/.test(code) ? code : $("cv-html").value;
      previewCanvas();
      return;
    }
    try {
      const data = await api.request("/api/code/run", {
        method: "POST",
        body: JSON.stringify({ language: "python", code }),
      });
      $("cv-py").value = code;
      $("cv-py-out").textContent = data.output || "(no output)";
      setDrawer("<pre class='sandbox-out'>" + md.escapeHtml(data.output || "") + "</pre>");
      showPane("canvas");
    } catch (err) {
      $("cv-py-out").textContent = graceful(err);
      showPane("canvas");
    }
  }

  function showPane(name) {
    if (name === "admin" && !isOwner(state.user)) return;
    state.pane = name;
    document.querySelectorAll(".nav-tab").forEach((b) => b.classList.toggle("on", b.getAttribute("data-pane") === name));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("on", p.id === "pane-" + name));
    if (name === "admin") loadAdmin().catch(() => {});
    if (name === "deck" && window.AetherDeck) {
      window.AetherDeck.render();
      window.AetherDeck.fitCanvas();
    }
  }

  document.querySelectorAll(".nav-tab").forEach((btn) => {
    btn.addEventListener("click", () => showPane(btn.getAttribute("data-pane")));
  });

  function setDrawer(html) {
    const drawer = $("split-drawer");
    drawer.hidden = false;
    $("drawer-body").innerHTML = html;
    document.querySelector(".app-view").classList.add("split-on");
  }
  $("toggle-drawer").addEventListener("click", () => {
    const drawer = $("split-drawer");
    drawer.hidden = !drawer.hidden;
  });
  $("close-drawer").addEventListener("click", () => {
    $("split-drawer").hidden = true;
  });

  function previewCanvas() {
    const html = $("cv-html").value;
    const css = $("cv-css").value;
    const js = $("cv-js").value;
    const doc =
      "<!DOCTYPE html><html><head><style>" +
      css +
      "</style></head><body>" +
      html +
      "<script>" +
      js +
      "<\/script></body></html>";
    $("cv-frame").srcdoc = doc;
  }
  $("cv-preview").addEventListener("click", previewCanvas);
  $("cv-clear").addEventListener("click", () => {
    $("cv-frame").srcdoc = "";
  });
  $("cv-run-py").addEventListener("click", async () => {
    $("cv-py-out").textContent = "running…";
    try {
      const data = await api.request("/api/code/run", {
        method: "POST",
        body: JSON.stringify({ language: "python", code: $("cv-py").value }),
      });
      $("cv-py-out").textContent = data.output || "(no output)";
    } catch (err) {
      $("cv-py-out").textContent = graceful(err);
    }
  });

  $("img-go").addEventListener("click", async () => {
    const promptText = $("img-prompt").value.trim();
    if (!promptText) return;
    const [w, h] = ($("img-ratio").value || "1024x1024").split("x").map(Number);
    $("img-status").textContent = "generating…";
    $("img-go").disabled = true;
    try {
      const data = await api.request("/api/generate/image", {
        method: "POST",
        body: JSON.stringify({
          prompt: promptText,
          width: w,
          height: h,
          style: $("img-style").value,
        }),
      });
      const url = md.safeUrl(data.url || "");
      $("img-stage").innerHTML = url ? '<img alt="studio" src="' + url + '">' : "No image.";
      if (url) {
        const dl = $("img-dl");
        dl.hidden = false;
        dl.href = url;
      }
      $("img-status").textContent = data.model || "done";
    } catch (err) {
      $("img-status").textContent = graceful(err);
    } finally {
      $("img-go").disabled = false;
    }
  });

  function markSpeaking(on) {
    document.body.classList.toggle("speaking", !!on);
    $("speak-btn").classList.toggle("on", !!on);
    const hint = $("composer-hint");
    if (on) hint.textContent = "speaking";
    else if (hint.textContent === "speaking") hint.textContent = "";
  }

  function speakText(text) {
    const t = (text || "").trim();
    if (!t) return false;
    const speech = window.AetherSpeech;
    if (!speech) return false;
    if (speech.speaking() && speech.current() === t) {
      speech.stop();
      return true;
    }
    return speech.speak(t, {
      voiceURI: state.voiceURI,
      onStart: () => markSpeaking(true),
      onEnd: () => markSpeaking(false),
    });
  }

  $("speak-btn").addEventListener("click", async () => {
    const speech = window.AetherSpeech;
    if (speech && speech.speaking()) {
      speech.stop();
      return;
    }
    const text = state.lastReply || $("voice-text").value;
    if (speakText(text)) return;
    await serverSpeak(text);
  });
  $("voice-speak").addEventListener("click", () => speakText($("voice-text").value));
  $("voice-stop").addEventListener("click", () => {
    if (window.AetherSpeech) window.AetherSpeech.stop();
    else if (window.speechSynthesis) window.speechSynthesis.cancel();
    const audio = $("voice-audio");
    audio.pause();
    markSpeaking(false);
  });
  $("voice-server").addEventListener("click", () => serverSpeak($("voice-text").value));

  async function serverSpeak(text) {
    const t = (text || "").trim();
    if (!t) return;
    try {
      const headers = new Headers();
      const token = api.getToken();
      if (token) {
        headers.set("Authorization", "Bearer " + token);
        headers.set("X-Aether-Token", token);
      }
      headers.set("Content-Type", "application/json");
      const res = await fetch("/api/generate/audio", {
        method: "POST",
        headers,
        credentials: "same-origin",
        body: JSON.stringify({ text: t }),
      });
      const type = res.headers.get("content-type") || "";
      if (type.includes("audio")) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = $("voice-audio");
        audio.src = url;
        audio.hidden = false;
        audio.play();
        return;
      }
      speakText(t);
    } catch (_) {
      speakText(t);
    }
  }

  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  if (SpeechRec) {
    rec = new SpeechRec();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.onresult = (ev) => {
      const said = ev.results[0][0].transcript;
      $("prompt").value = ($("prompt").value + " " + said).trim();
      autosize();
    };
    rec.onstart = () => {
      $("mic-btn").classList.add("listening");
      $("composer-hint").textContent = "listening";
    };
    rec.onend = () => {
      $("mic-btn").classList.remove("listening");
      $("composer-hint").textContent = "";
    };
    rec.onerror = () => {
      $("mic-btn").classList.remove("listening");
      $("composer-hint").textContent = "";
      toast("Mic could not start", "err");
    };
  }
  $("mic-btn").addEventListener("click", () => {
    if (!rec) {
      $("composer-hint").textContent = "voice input not supported";
      toast("Voice input not supported here", "err");
      return;
    }
    try {
      if ($("mic-btn").classList.contains("listening")) {
        rec.stop();
        return;
      }
      rec.start();
    } catch (_) {}
  });

  $("open-settings").addEventListener("click", async () => {
    show($("modal-settings"));
    closeSidebar();
    await loadMemories();
  });
  $("open-admin").addEventListener("click", () => {
    hide($("modal-admin"));
    showPane("admin");
  });
  const gotoAdmin = $("goto-admin");
  if (gotoAdmin) {
    gotoAdmin.addEventListener("click", () => {
      hide($("modal-admin"));
      showPane("admin");
    });
  }
  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => hide($(btn.getAttribute("data-close"))));
  });
  document.querySelectorAll(".modal").forEach((modal) => {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) hide(modal);
    });
  });

  $("save-settings").addEventListener("click", async () => {
    try {
      const payload = { display_name: $("settings-name").value.trim() };
      if ($("settings-password").value) payload.password = $("settings-password").value;
      const data = await api.request("/api/auth/me", { method: "PATCH", body: JSON.stringify(payload) });
      state.user = data.user;
      $("user-name").textContent = data.user.display_name || data.user.email;
      toastError($("settings-msg"), "Saved.");
      $("settings-msg").style.color = "var(--accent)";
    } catch (err) {
      $("settings-msg").style.color = "var(--danger)";
      toastError($("settings-msg"), graceful(err));
    }
  });

  $("theme-select").value = localStorage.getItem(api.THEME_KEY) || "dark";
  $("theme-select").addEventListener("change", (e) => applyTheme(e.target.value));

  const consBtn = $("toggle-consensus");
  if (consBtn) {
    consBtn.addEventListener("click", () => {
      state.consensus = !state.consensus;
      setPref("aether.consensus", state.consensus ? "1" : "0");
      consBtn.classList.toggle("on", state.consensus);
    });
  }
  const talkBtn = $("toggle-talk");
  if (talkBtn) {
    talkBtn.addEventListener("click", () => {
      state.talk = !state.talk;
      setPref("aether.talk", state.talk ? "1" : "0");
      talkBtn.classList.toggle("on", state.talk);
      const talkSel = $("talk-select");
      if (talkSel) talkSel.value = state.talk ? "on" : "off";
    });
  }
  document.querySelectorAll("[data-tone]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.tone = btn.getAttribute("data-tone") === "unfiltered" ? "unfiltered" : "standard";
      setPref("aether.tone", state.tone);
      document.querySelectorAll("[data-tone]").forEach((b) => {
        b.classList.toggle("on", b.getAttribute("data-tone") === state.tone);
      });
    });
  });
  const talkSel = $("talk-select");
  if (talkSel) {
    talkSel.addEventListener("change", (e) => {
      state.talk = e.target.value === "on";
      setPref("aether.talk", state.talk ? "1" : "0");
      if (talkBtn) talkBtn.classList.toggle("on", state.talk);
    });
  }
  const voiceSel = $("voice-select");
  if (voiceSel) {
    voiceSel.addEventListener("change", (e) => {
      state.voiceURI = e.target.value;
      setPref("aether.voice", state.voiceURI);
    });
  }
  if (window.speechSynthesis) {
    window.speechSynthesis.addEventListener("voiceschanged", fillVoices);
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(api.THEME_KEY, theme);
    $("theme-select").value = theme;
  }

  $("memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = $("memory-input").value.trim();
    if (!content) return;
    await api.request("/api/memories", { method: "POST", body: JSON.stringify({ content, category: "fact" }) });
    $("memory-input").value = "";
    await loadMemories();
  });

  async function loadMemories() {
    const data = await api.request("/api/memories");
    const list = $("memory-list");
    list.innerHTML = "";
    (data.memories || []).forEach((m) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = `(${m.category}) ${m.content}`;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn sm ghost";
      rm.textContent = "Remove";
      rm.addEventListener("click", async () => {
        await api.request("/api/memories/" + m.id, { method: "DELETE" });
        await loadMemories();
      });
      li.appendChild(span);
      li.appendChild(rm);
      list.appendChild(li);
    });
    if (!list.children.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No memories yet.";
      list.appendChild(li);
    }
  }

  function setTrainingUi(on) {
    state.trainingOn = !!on;
    const tog = $("train-toggle");
    tog.classList.toggle("on", !!on);
    tog.setAttribute("aria-pressed", on ? "true" : "false");
    tog.textContent = on ? "On" : "Off";
    $("fact-input").disabled = !on;
    $("fact-submit").disabled = !on;
    $("teach-queue").querySelectorAll("textarea, button").forEach((el) => {
      el.disabled = !on;
    });
  }

  $("train-toggle").addEventListener("click", async () => {
    try {
      const data = await api.request("/api/admin/teaching/mode", {
        method: "PATCH",
        body: JSON.stringify({ enabled: !state.trainingOn }),
      });
      setTrainingUi(data.training_mode_enabled);
      await loadAdmin();
    } catch (err) {
      $("teach-queue").textContent = graceful(err);
    }
  });

  $("fact-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.trainingOn) return;
    const content = $("fact-input").value.trim();
    if (!content) return;
    try {
      await api.request("/api/admin/teaching/facts", {
        method: "POST",
        body: JSON.stringify({ content, category: "fact" }),
      });
      $("fact-input").value = "";
      await loadAdmin();
    } catch (err) {
      $("teach-queue").textContent = graceful(err);
    }
  });

  async function loadAdmin() {
    if (!isOwner(state.user)) return;
    let stats = {};
    let users = { users: [] };
    let teaching = { training_mode_enabled: false, prompts: [], knowledge: [] };
    try {
      stats = await api.request("/api/admin/stats");
      users = await api.request("/api/admin/users");
    } catch (err) {
      $("admin-stats").textContent = graceful(err);
      return;
    }
    try {
      teaching = await api.request("/api/admin/teaching");
    } catch (_) {}
    setTrainingUi(!!teaching.training_mode_enabled);
    $("admin-stats").innerHTML = [
      ["Users", stats.users],
      ["Pending", stats.pending],
      ["Chats", stats.conversations],
      ["Events", stats.totals && stats.totals.events],
      ["Failures", stats.totals && stats.totals.failures],
    ]
      .map(([k, v]) => `<div class="stat"><b>${md.escapeHtml(String(v ?? 0))}</b><span>${k}</span></div>`)
      .join("");
    const rows = (users.users || [])
      .map((u) => {
        const st = u.account_status;
        const badge = `<span class="badge ${st}">${md.escapeHtml(st)}</span>`;
        const actions =
          u.role === "owner"
            ? ""
            : `<button class="btn sm" data-admin="${u.id}" data-status="approved">Approve</button>
               <button class="btn sm ghost" data-admin="${u.id}" data-status="revoked">Revoke</button>`;
        return `<tr>
          <td>${md.escapeHtml(u.email)}</td>
          <td>${md.escapeHtml(u.role)}</td>
          <td>${badge}</td>
          <td>${actions}</td>
        </tr>`;
      })
      .join("");
    $("admin-users").innerHTML = `<table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
    $("admin-users").querySelectorAll("[data-admin]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api.request("/api/admin/users/" + btn.getAttribute("data-admin"), {
          method: "PATCH",
          body: JSON.stringify({ account_status: btn.getAttribute("data-status") }),
        });
        await loadAdmin();
      });
    });
    const usage = (stats.recent || [])
      .map(
        (e) =>
          `<tr><td>${md.escapeHtml(e.kind || "")}</td><td>${md.escapeHtml((e.model || "").split("/").pop())}</td><td>${e.success ? "ok" : "fail"}</td><td>${e.latency_ms || 0}ms</td></tr>`
      )
      .join("");
    $("admin-usage").innerHTML = `<table><thead><tr><th>Kind</th><th>Model</th><th>Result</th><th>Latency</th></tr></thead><tbody>${usage}</tbody></table>`;

    const queue = $("teach-queue");
    queue.innerHTML = "";
    const pending = (teaching.prompts || []).filter((p) => p.status === "pending");
    if (!pending.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = state.trainingOn ? "No open teaching prompts." : "Training Mode is off — prompts are paused.";
      queue.appendChild(p);
    }
    pending.forEach((p) => {
      const card = document.createElement("div");
      card.className = "teach-card";
      const q = document.createElement("p");
      q.textContent = p.question;
      const ctx = document.createElement("p");
      ctx.className = "muted";
      ctx.textContent = p.context || "";
      const ta = document.createElement("textarea");
      ta.placeholder = "Correct fact or rule…";
      ta.disabled = !state.trainingOn;
      const go = document.createElement("button");
      go.type = "button";
      go.className = "btn sm primary";
      go.textContent = "Teach";
      go.disabled = !state.trainingOn;
      go.addEventListener("click", async () => {
        if (!ta.value.trim()) return;
        await api.request("/api/admin/teaching/answer", {
          method: "POST",
          body: JSON.stringify({ prompt_id: p.id, answer: ta.value.trim() }),
        });
        await loadAdmin();
      });
      card.appendChild(q);
      card.appendChild(ctx);
      card.appendChild(ta);
      card.appendChild(go);
      queue.appendChild(card);
    });

    const klist = $("knowledge-list");
    klist.innerHTML = "";
    (teaching.knowledge || []).forEach((k) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = `(${k.category}) ${k.content}`;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn sm ghost";
      rm.textContent = "Remove";
      rm.addEventListener("click", async () => {
        await api.request("/api/admin/teaching/facts/" + k.id, { method: "DELETE" });
        await loadAdmin();
      });
      li.appendChild(span);
      li.appendChild(rm);
      klist.appendChild(li);
    });
    if (!klist.children.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No custom knowledge yet.";
      klist.appendChild(li);
    }
  }

  $("open-sidebar").addEventListener("click", () => {
    $("sidebar").classList.add("open");
    document.body.classList.add("nav-open");
  });
  $("close-sidebar").addEventListener("click", closeSidebar);
  function closeSidebar() {
    $("sidebar").classList.remove("open");
    document.body.classList.remove("nav-open");
  }
  document.querySelector(".workspace").addEventListener("click", (e) => {
    if (document.body.classList.contains("nav-open") && e.target === document.querySelector(".workspace")) {
      closeSidebar();
    }
  });

  if (window.AetherDeck) window.AetherDeck.bind();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  boot();
})();
