/* ══════════════════════════════════════════════════════════════════════
   Aether — shell
   Auth, boot, pane navigation, status pills, usage polling, service worker.
   Loaded last: it wires everything else up.
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function shell() {
  const A = window.Aether;
  const { $, $$ } = A;

  let me = null;
  let usageTimer = null;

  /* ───────────────────────────── auth ─────────────────────────────── */

  function switchAuthTab(mode) {
    $("#tab-login").classList.toggle("active", mode === "login");
    $("#tab-register").classList.toggle("active", mode === "register");
    $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
    $("#auth-password").autocomplete = mode === "login" ? "current-password" : "new-password";
    $("#auth-mode").value = mode;
  }

  /* ─────────────────────── auth view transitions ──────────────────── */

  // The server names this case explicitly (401 "User not found"): the JWT is
  // fine, the account behind it is gone — i.e. storage was reset.
  const MISSING_ACCOUNT = /user not found|account no longer exists/i;

  function isMissingAccount(err) {
    return Boolean(err) && err.status === 401
      && MISSING_ACCOUNT.test(String(err.detail || err.message || ""));
  }

  /* Showing the login screen must not lose the open conversation, so stash
     the hash first; a successful sign-in restores it (see the form handler). */
  function showAuthView() {
    A.session.stashHash();
    $("#app-view").classList.add("hidden");
    $("#auth-view").classList.remove("hidden");
  }

  let storageResetShown = false;

  function showStorageReset() {
    if (storageResetShown) return;
    // Never while the auth screen is already open — the user is already doing
    // the fix, and login/register failures have their own inline cards.
    const authView = document.getElementById("auth-view");
    if (authView && !authView.classList.contains("hidden")) {
      const preEmail = A.emailFromToken(A.session.token) || A.session.email || "";
      if (preEmail && !$("#auth-email").value) $("#auth-email").value = preEmail;
      return;
    }
    storageResetShown = true;
    const email = A.emailFromToken(A.session.token) || A.session.email || "";
    // The account no longer exists, so the token is meaningless — but keep the
    // email around: it is the one thing worth prefilling. Do NOT flip the tab:
    // prefill may fill the input but must not mutate the visible tab or submit target.
    A.session.token = "";
    showAuthView();
    if (email) $("#auth-email").value = email;
    $("#auth-password").focus();
    A.toast("Storage was reset — please create your account again",
            { type: "warn", timeout: 6000 });
  }

  function showSessionExpired() {
    A.session.clear();
    showAuthView();
    switchAuthTab("login");
    A.toast("Session expired — please sign in again", { type: "warn" });
  }

  /* "Session expired" is only honest when the database is healthy. A stale
     status check can mean storage is still warming up, which is not the same
     thing as a dead session. */
  function storageKnownUnhealthy() {
    const schema = A.lastStorage?.schema;
    return Boolean(schema) && schema.ready === false;
  }

  async function dbHealthy() {
    if (storageKnownUnhealthy()) return false;
    try {
      const status = await A.api("/api/ai/status");
      A.lastStorage = status?.database || A.lastStorage;
      const schema = status?.database?.schema;
      return !(schema && schema.ready === false);
    } catch { return true; /* the 401 already came through — trust it */ }
  }

  function saveAuth(data) {
    const token = data.access_token || data.token || data.accessToken || "";
    if (!token) throw new Error("Invalid server response: missing token");
    A.session.token = token;
    const emailVal = $("#auth-email").value.trim().toLowerCase();
    if (emailVal) A.session.email = emailVal;
  }

  function initAuth() {
    $("#tab-login").onclick = () => switchAuthTab("login");
    $("#tab-register").onclick = () => switchAuthTab("register");
    $("#auth-form").onsubmit = async (event) => {
      event.preventDefault();
      const mode = $("#auth-mode").value || "login";
      const button = $("#auth-submit");
      const error = $("#auth-error");
      error.classList.add("hidden");
      button.disabled = true;
      button.textContent = mode === "login" ? "Signing in…" : "Creating account…";
      try {
        const data = await A.apiRetry(`/api/auth/${mode}`, {
          method: "POST",
          body: { email: $("#auth-email").value.trim(), password: $("#auth-password").value },
        });
        saveAuth(data);
        $("#auth-email").value = "";
        $("#auth-password").value = "";
        A.session.restoreHash();          // open conversation comes back
        await enterApp();
      } catch (err) {
        error.textContent = err.detail || err.message;
        error.classList.remove("hidden");
      } finally {
        button.disabled = false;
        switchAuthTab(mode);
      }
    };
    $("#retry-btn").onclick = () => location.reload();
    A.on("session:expired", (err) => {
      // storage-reset toast: at most once, never while auth screen is open, never for login/register
      if (isMissingAccount(err) && !storageKnownUnhealthy() && (A.emailFromToken(A.session.token) || A.session.email)) {
        const authOpen = !document.getElementById("auth-view")?.classList.contains("hidden");
        if (!authOpen && !storageResetShown) { showStorageReset(); return; }
        if (authOpen) return; // already handling the fix
      }
      if (storageKnownUnhealthy()) {
        // Storage is coming back: keep the session and let the user retry
        // instead of pretending the session died.
        A.toast("Storage is warming up — try again in a few seconds", { type: "warn" });
        return;
      }
      showSessionExpired();
    });
  }

  /* ─────────────────────────── app boot ──────────────────────────── */

  async function enterApp() {
    $("#auth-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    $("#user-email").textContent = A.session.email;
    try {
      me = await A.api("/api/auth/me");
      $("#nav-admin").classList.toggle("hidden", !me.is_admin);
    } catch { /* still usable */ }

    A.initChat();
    A.initVoice();
    A.initSlides();
    A.initImages();
    A.initSettings();
    A.initAdmin?.();
    A.initNews?.();
    A.initTasks?.();

    A.loadPersonas();
    A.loadConversations();
    A.refreshUsage();
    A.loadStatus();
    if (usageTimer) clearInterval(usageTimer);
    usageTimer = setInterval(() => {
      if (!document.hidden) A.refreshUsage();
    }, 25000);

    $(".pane:not(.hidden)")?.classList.add("pane-enter");
    A.showPane("chat", { silent: true });
    $("#chat-input").focus();
  }

  A.loadPersonas = async function loadPersonas() {
    const select = $("#persona-select");
    if (!select) return;
    try {
      const personas = await A.api("/api/settings/personas");
      const current = select.value;
      select.innerHTML = '<option value="">Persona</option>';
      for (const persona of personas) {
        select.append(A.el("option", { value: persona.id, text: persona.name }));
      }
      if (personas.some((p) => p.id === current)) select.value = current;
    } catch { /* offline */ }
  };

  /* ─────────────────────────── status pill ───────────────────────── */

  A.loadStatus = async function loadStatus() {
    try {
      const data = await A.api("/api/ai/status");
      const keyless = (data.providers || []).find((p) => p.kind === "pollinations");
      const queue = keyless?.queue || data.keyless?.queue || {};
      const pill = $("#mode-pill");
      const cooling = queue.cooling_down || false;
      pill.className = `pill ${cooling ? "warn" : "ok"}`;
      pill.innerHTML = `${A.icon("bolt", 12)}<span>${cooling ? "Cooling down" : "Free AI ready"}</span>`;
      pill.title = [
        "Keyless free tier — no API keys, nothing to pay.",
        `Database: ${data.database?.label || data.database_mode}`,
        `Queue: ${queue.queued_requests || 0} waiting · interval ${queue.interval_seconds || 5}s`,
        data.database?.warning || "",
      ].filter(Boolean).join("\n");
      const storage = data.database || {};
      A.lastStorage = storage;             // used to judge "session expired"
      const security = data.security || {};
      const notice = storage.warning || security.warning || "";
      const healthy = Boolean(storage.persistent) && !notice;
      $("#storage-banner")?.classList.toggle("hidden", healthy);
      if (!healthy) {
        const banner = $("#storage-banner");
        if (banner) banner.textContent = notice
          || "Temporary storage in this preview — chats may not persist.";
      }
      if (cooling && queue.retry_after_seconds > 0) showCooldownBanner(queue.retry_after_seconds);
    } catch {
      const pill = $("#mode-pill");
      if (pill) {
        pill.className = "pill warn";
        pill.innerHTML = `${A.icon("bolt", 12)}<span>Offline</span>`;
      }
    }
  };

  // Exposed for tests (tests/frontend-smoke.mjs) — harmless in production.
  A.__testCooldown = (seconds) => showCooldownBanner(seconds);

  /* Mobile keyboards: on iOS the visual viewport shrinks while the layout
     viewport (and 100vh) do not, so the composer ends up hidden behind the
     keyboard. We expose the overlap as --kb and let CSS lift the composer.
     Chrome/Android gets `interactive-widget=resizes-content` instead. */
  function initKeyboardHandling() {
    const vv = window.visualViewport;
    if (!vv) return;
    const apply = () => {
      const overlap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      // Ignore browser-chrome shifts; only a real keyboard is this tall.
      document.documentElement.style.setProperty("--kb", `${overlap > 90 ? Math.round(overlap) : 0}px`);
      if (overlap > 90 && document.activeElement === $("#chat-input")) A.scrollToBottom();
    };
    vv.addEventListener("resize", apply);
    vv.addEventListener("scroll", apply);
    $("#chat-input")?.addEventListener("focus", () => setTimeout(apply, 250));
    $("#chat-input")?.addEventListener("blur", () => setTimeout(apply, 150));
    apply();
  }

  function showCooldownBanner(seconds) {
    const banner = $("#cooldown-banner");
    if (!banner) return;
    let left = seconds;
    banner.classList.remove("hidden");
    const render = () => {
      banner.innerHTML = `${A.icon("clock", 14)}<span>Free AI cooling down — retry in `
        + `<b>${A.formatCountdown(left)}</b></span>`;
    };
    render();
    clearInterval(banner._timer);
    banner._timer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(banner._timer);
        banner.classList.add("hidden");
        A.loadStatus();
        return;
      }
      render();
    }, 1000);
  }

  /* ───────────────────────────── panes ───────────────────────────── */

  const PANES = {
    chat: { button: "#nav-chat", pane: "#chat-pane" },
    images: { button: "#nav-images", pane: "#images-pane" },
    slides: { button: "#nav-slides", pane: "#slides-pane" },
    news: { button: "#nav-news", pane: "#news-pane" },
    tasks: { button: "#nav-tasks", pane: "#tasks-pane" },
    settings: { button: "#nav-settings", pane: "#settings-pane" },
    admin: { button: "#nav-admin", pane: "#admin-pane" },
  };

  A.showPane = function showPane(name, { silent = false } = {}) {
    for (const [key, config] of Object.entries(PANES)) {
      const pane = $(config.pane);
      if (!pane) continue;
      pane.classList.toggle("hidden", key !== name);
      $(config.button)?.classList.toggle("active", key === name);
    }
    A.closeSidebar();
    if (!silent) {
      if (name === "chat") setTimeout(() => $("#chat-input")?.focus(), 60);
      if (name === "slides") A.loadDecks?.();
      if (name === "settings") A.loadSettings?.();
      if (name === "admin") A.loadAdmin?.();
      if (name === "tasks") A.loadTasks?.();
    }
    if (name === "chat") A.scrollToBottom?.(true);
  };

  A.closeSidebar = function closeSidebar() {
    $("#sidebar").classList.remove("open");
    $("#scrim").classList.remove("show");
  };

  A.scrollToBottom = function scrollToBottom(force = false) {
    const box = $("#messages");
    if (!box) return;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 220;
    if (near || force) box.scrollTop = box.scrollHeight;
  };

  A.autoGrow = function autoGrow(node) {
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  };

  function initShell() {
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
      if (isMobile()) A.closeSidebar();
      else $("#app-view").classList.add("sb-collapsed");
    };
    $("#scrim").onclick = () => A.closeSidebar();
    $("#user-btn").onclick = () => {
      if (isMobile()) { $("#sidebar").classList.add("open"); $("#scrim").classList.add("show"); }
      else { A.showPane("settings"); }
    };
    for (const [key, config] of Object.entries(PANES)) {
      const button = $(config.button);
      if (button) button.onclick = () => { A.showPane(key); };
    }
    $("#nav-chat-top").onclick = () => A.showPane("chat");
    $("#storage-banner").onclick = () => A.showPane("settings");
    $("#mode-pill").onclick = () => {
      A.showPane("settings");
      setTimeout(() => $("#usage-detail")?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
    };
    $("#password-btn").onclick = () => {
      $("#pw-error").classList.add("hidden");
      $("#password-form").reset();
      $("#password-modal").classList.remove("hidden");
    };
    $("#password-close").onclick = () => $("#password-modal").classList.add("hidden");
    $("#password-form").onsubmit = async (event) => {
      event.preventDefault();
      const error = $("#pw-error");
      error.classList.add("hidden");
      const next = $("#pw-new").value;
      if (next !== $("#pw-confirm").value) {
        error.textContent = "New passwords do not match.";
        error.classList.remove("hidden");
        return;
      }
      try {
        await A.api("/api/auth/password", {
          method: "POST",
          body: { current_password: $("#pw-current").value, new_password: next },
        });
        $("#password-modal").classList.add("hidden");
        A.toast("Password updated");
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove("hidden");
      }
    };
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        A.showPane("chat");
        $("#chat-search")?.focus();
      }
      if (event.key === "Escape") {
        $("#tools-menu")?.classList.add("hidden");
      }
    });
    initKeyboardHandling();
    window.addEventListener("online", () => { $("#offline-note")?.classList.add("hidden"); A.refreshUsage(); });
    window.addEventListener("offline", () => $("#offline-note")?.classList.remove("hidden"));
  }

  /* ──────────────────────── service worker ───────────────────────── */

  function initServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      reg.addEventListener("updatefound", () => {
        const worker = reg.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) {
            A.toast("A new version of Aether is ready", {
              action: { label: "Reload", onClick: () => location.reload() },
              timeout: 12000,
            });
          }
        });
      });
    }).catch(() => { /* offline-first is a bonus, not a requirement */ });
  }

  /* ───────────────────────────── start ───────────────────────────── */

  /* Boot check for a stored session. Retries once (the first call after a
     deploy can hit a cold database) and then says which of the four things
     actually happened — a 401 is not always "your session expired". */
  async function checkSession() {
    try {
      await A.apiRetry("/api/auth/me", {}, { attempts: 2, delay: 250 });
      return { state: "ok" };
    } catch (err) {
      if (err.status === 401) {
        const missing = isMissingAccount(err);
        const hasEmail = Boolean(A.emailFromToken(A.session.token) || A.session.email);
        if (missing && hasEmail) {
          if (await dbHealthy()) return { state: "reset", err };
          return { state: "warming", err };
        }
        if (await dbHealthy()) return { state: "expired", err };
        return { state: "warming", err };
      }
      return { state: "unreachable", err };
    }
  }

  // Exposed for tests (tests/frontend-smoke.mjs) — harmless in production.
  A.__testSessionCheck = checkSession;

  async function boot() {
    A.theme.init();
    initShell();
    initAuth();
    A.on("usage:refresh", () => A.refreshUsage());
    A.on("chat:finished", () => { A.refreshUsage(); A.loadStatus(); });
    A.on("memory:changed", () => A.loadSettings?.());

    if (A.session.token) {
      const result = await checkSession();
      if (result.state === "ok") {
        await enterApp();
        initServiceWorker();
        return;
      }
      if (result.state === "reset") {
        showStorageReset();
        initServiceWorker();
        return;
      }
      if (result.state === "expired") {
        showSessionExpired();
        initServiceWorker();
        return;
      }
      // "warming" (401 while storage is unhealthy) and "unreachable"
      // (503/network/5xx): the session is kept and the user gets a retry.
      showAuthView();
      $("#retry-btn").classList.remove("hidden");
      const box = $("#auth-error");
      box.textContent = result.state === "warming"
        ? "Storage is warming up — try again in a few seconds."
        : (result.err?.status === 503 && result.err?.detail)
          ? result.err.detail
          : "Can't reach the server right now — your session is saved.";
      box.classList.remove("hidden");
      return;
    }
    showAuthView();
    initServiceWorker();
  }

  // Boot exactly once: the DOMContentLoaded listener and the readyState check
  // below can both be satisfied in exotic load orders (cached/deferred script).
  let booted = false;
  function bootOnce() {
    if (booted) return;
    booted = true;
    boot();
  }

  document.addEventListener("DOMContentLoaded", bootOnce);
  if (document.readyState !== "loading") bootOnce();
})();
