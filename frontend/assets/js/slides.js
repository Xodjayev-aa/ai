/* ══════════════════════════════════════════════════════════════════════
   Aether — presentations

   Decks are built a few slides at a time (plan → slide → slide…), so progress
   is real, a failure only costs one slide, and everything survives a reload
   because each step is stored server-side under a deck id.
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function slides() {
  const A = window.Aether;
  const { $ } = A;

  const S = {
    deckId: null,
    title: "",
    subtitle: "",
    plan: [],
    slides: [],
    index: 0,
    busy: false,
    cancelled: false,
  };
  A.deckState = S;

  const el = (tag, props, children) => A.el(tag, props, children);

  A.initSlides = function initSlides() {
    const count = $("#slide-count");
    if (count && !count.options.length) {
      for (let i = 3; i <= 20; i += 1) {
        count.append(el("option", { value: i, text: `${i} slides`, selected: i === 8 }));
      }
    }
    $("#slide-go").onclick = () => startDeck();
    $("#deck-pptx").onclick = () => exportDeck("pptx");
    $("#deck-html").onclick = () => exportDeck("html");
    $("#slides-close").onclick = () => $("#slides-modal").classList.add("hidden");
    $("#slide-prev").onclick = () => step(-1);
    $("#slide-next").onclick = () => step(1);
    $("#deck-regen").onclick = () => regenerateSlide();
    $("#deck-copy").onclick = () => A.copy(slideText(), "Slide copied");
    document.addEventListener("keydown", (event) => {
      if ($("#slides-modal").classList.contains("hidden")) return;
      if (event.key === "ArrowRight") step(1);
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === "Escape") $("#slides-modal").classList.add("hidden");
    });
    A.loadDecks();
  };

  /* ───────────────────────────── building ─────────────────────────── */

  async function startDeck() {
    const topic = $("#slide-topic").value.trim();
    if (!topic) { A.toast("Give me a topic first", { type: "err" }); return; }
    if (S.busy) return;
    setProgress("Designing the structure…", 0, 0);
    S.cancelled = false;
    S.busy = true;
    $("#slide-go").disabled = true;
    try {
      const plan = await A.api("/api/presentations/plan", {
        method: "POST",
        body: {
          topic,
          num_slides: Number($("#slide-count").value) || 8,
          audience: $("#slide-audience").value.trim() || "general audience",
          tone: $("#slide-tone").value || "professional but engaging",
        },
      });
      S.deckId = plan.deck_id;
      S.title = plan.title;
      S.subtitle = plan.subtitle || "";
      S.plan = plan.slides.map((slide) => slide.title);
      S.slides = new Array(S.plan.length).fill(null);
      S.index = 0;
      renderProgress();
      setProgress(`“${S.title}” — generating ${S.plan.length} slides…`, 0, S.plan.length);
      await generateRemaining();
    } catch (err) {
      const message = err.detail || err.message;
      setProgress("", 0, 0, true);
      showPlanError(message);
      A.toast(message, {
        type: "err", action: { label: "Retry", onClick: () => startDeck() },
      });
    } finally {
      S.busy = false;
      $("#slide-go").disabled = false;
    }
  }

  /* A failed plan must be recoverable in place — the outline is the longest
     wait in the flow, so a toast alone would lose the user's topic. */
  function showPlanError(message) {
    const host = $("#slide-list");
    if (!host) return;
    host.innerHTML = "";
    const retryable = message;
    host.append(A.el("div", { class: "error-card", role: "alert" }, [
      A.el("div", { class: "error-head" }, [
        A.icon("alert", 16), A.el("strong", { text: "That didn’t work" }),
      ]),
      A.el("p", { class: "muted small-text", text: retryable }),
      A.el("div", { class: "error-actions" }, [
        A.el("button", {
          class: "btn small-btn primary retry-btn", text: "Try again",
          onclick: () => startDeck(),
        }),
        A.el("button", {
          class: "btn small-btn ghost", text: "Fewer slides",
          onclick: () => {
            const select = $("#slide-count");
            if (select) select.value = "4";
            startDeck();
          },
        }),
      ]),
    ]));
  }

  async function generateRemaining() {
    for (let i = 0; i < S.plan.length; i += 1) {
      if (S.cancelled) return;
      if (S.slides[i]) continue;
      await generateSlide(i);
      if (S.cancelled) return;
    }
    setProgress(`“${S.title}” — ${S.plan.length} slides ready`, S.plan.length, S.plan.length);
    A.toast("Deck ready — export it or present it here");
    A.loadDecks();
    openDeck();
  }

  async function generateSlide(index) {
    markSlide(index, "working");
    setProgress(`Writing slide ${index + 1} of ${S.plan.length}…`, index, S.plan.length);
    try {
      const slide = await A.api("/api/presentations/slide", {
        method: "POST",
        body: { deck_id: S.deckId, index },
      });
      S.slides[index] = slide;
      markSlide(index, "done");
      A.emit("usage:refresh");
      return true;
    } catch (err) {
      markSlide(index, "error", err.detail || err.message);
      setProgress(`Slide ${index + 1} failed — ${err.detail || err.message}`, index, S.plan.length, true);
      throw Object.assign(err, { slideIndex: index });
    }
  }

  async function continueDeck() {
    S.cancelled = false;
    S.busy = true;
    $("#slide-go").disabled = true;
    try {
      await generateRemaining();
    } catch (err) {
      A.toast(err.detail || err.message, {
        type: "err", action: { label: "Retry", onClick: () => { S.busy = false; continueDeck(); } },
      });
    } finally {
      S.busy = false;
      $("#slide-go").disabled = false;
    }
  }

  /* ─────────────────────────── progress UI ────────────────────────── */

  function setProgress(text, done, total, isError = false) {
    const box = $("#slide-progress");
    const bar = $("#slide-bar-fill");
    box.classList.toggle("hidden", !text && !total);
    box.classList.toggle("error", isError);
    $("#slide-progress-text").textContent = text;
    $("#slide-progress-count").textContent = total ? `${done}/${total}` : "";
    if (bar) bar.style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
  }

  function renderProgress() {
    const list = $("#slide-list");
    list.innerHTML = "";
    S.plan.forEach((title, index) => {
      list.append(el("div", {
        class: "slide-row", dataset: { index: String(index) },
      }, [
        el("span", { class: "slide-row-status", text: `${index + 1}` }),
        el("span", { class: "slide-row-title", text: title }),
        el("button", {
          class: "slide-row-retry hidden", title: "Retry this slide", html: A.icon("refresh", 13),
          onclick: () => retrySlide(index),
        }),
      ]));
    });
  }

  function markSlide(index, state, detail = "") {
    const row = $(`.slide-row[data-index="${index}"]`);
    if (!row) return;
    row.dataset.state = state;
    row.classList.toggle("error", state === "error");
    row.querySelector(".slide-row-retry").classList.toggle("hidden", state !== "error");
    if (detail) row.title = detail;
    const badge = row.querySelector(".slide-row-title");
    if (state === "done") badge.dataset.done = "1";
  }

  async function retrySlide(index) {
    if (S.busy) return;
    S.busy = true;
    try {
      await generateSlide(index);
      if (S.slides.every(Boolean)) {
        setProgress(`“${S.title}” — ${S.plan.length} slides ready`, S.plan.length, S.plan.length);
        A.loadDecks();
      }
    } catch { /* the row stays in the error state */ }
    finally { S.busy = false; }
  }

  /* ──────────────────────────── deck list ─────────────────────────── */

  A.loadDecks = async function loadDecks() {
    const box = $("#deck-list");
    if (!box) return;
    try {
      const decks = await A.api("/api/presentations/decks");
      box.innerHTML = "";
      if (!decks.length) {
        box.append(el("p", { class: "muted small-text", text: "No decks yet." }));
        return;
      }
      for (const deck of decks) {
        box.append(el("div", { class: "deck-row" }, [
          el("div", { class: "deck-row-main" }, [
            el("strong", { text: deck.title }),
            el("span", {
              class: "muted small-text",
              text: `${deck.status === "ready" ? "ready" : deck.status} · ${A.timeAgo(deck.updated_at || deck.created_at)}`,
            }),
          ]),
          el("button", {
            class: "btn ghost small-btn", text: "Open",
            onclick: () => openDeckById(deck.id),
          }),
          el("button", {
            class: "icon-btn", title: "Delete deck", html: A.icon("trash", 14),
            onclick: async () => {
              const ok = await A.confirm({
                title: "Delete this deck?", message: deck.title, okLabel: "Delete", danger: true,
              });
              if (!ok) return;
              await A.api(`/api/presentations/deck/${deck.id}`, { method: "DELETE" });
              A.loadDecks();
            },
          }),
        ]));
      }
    } catch { /* offline: the pane still works for new decks */ }
  };

  async function openDeckById(deckId) {
    setProgress("Loading deck…", 0, 0);
    try {
      const detail = await A.api(`/api/presentations/deck/${deckId}`);
      S.deckId = detail.deck_id;
      S.title = detail.title;
      S.subtitle = detail.subtitle || "";
      S.plan = detail.plan || [];
      S.slides = (detail.slides || []).slice(0, S.plan.length);
      while (S.slides.length < S.plan.length) S.slides.push(null);
      S.index = 0;
      renderProgress();
      const done = S.slides.filter(Boolean).length;
      setProgress(`${S.title} — ${done}/${S.plan.length} slides`, done, S.plan.length);
      if (done < S.plan.length) {
        const resume = await A.confirm({
          title: "Finish this deck?",
          message: `${done} of ${S.plan.length} slides are ready. Generate the rest now?`,
          okLabel: "Generate the rest",
        });
        if (resume) { await continueDeck(); return; }
      }
      openDeck();
    } catch (err) {
      A.toast(err.detail || "Could not open that deck", { type: "err" });
    }
  }
  A.openDeckById = openDeckById;

  /* ─────────────────────── viewer (present mode) ──────────────────── */

  function openDeck() {
    $("#deck-title").textContent = S.title || "Deck";
    $("#slides-modal").classList.remove("hidden");
    renderSlide();
  }

  function step(delta) {
    const next = S.index + delta;
    if (next < 0 || next >= S.plan.length) return;
    S.index = next;
    renderSlide();
  }

  function renderSlide() {
    const stage = $("#slide-stage");
    const index = S.index;
    const slide = S.slides[index];
    const position = `${index + 1} / ${S.plan.length}`;
    $("#slide-counter").textContent = position;
    $("#deck-subtitle").textContent = S.subtitle || "";
    if (!slide) {
      stage.innerHTML = `<h3>${A.esc(S.plan[index] || "Slide")}</h3>
        <p class="muted small-text">This slide hasn't been generated yet.</p>
        <button class="btn primary small-btn" id="slide-generate-now">Generate this slide</button>`;
      $("#slide-generate-now").onclick = async () => {
        S.busy = true;
        try { await generateSlide(index); renderSlide(); } catch { /* row shows the error */ }
        finally { S.busy = false; }
      };
      return;
    }
    stage.dataset.index = String(index);
    stage.innerHTML = `<h3>${A.esc(slide.title)}</h3>
      <ul>${(slide.bullets || []).map((b) => `<li>${A.esc(b)}</li>`).join("")}</ul>
      ${slide.notes ? `<div class="slide-notes"><b>Speaker notes</b>${A.esc(slide.notes)}</div>` : ""}`;
  }

  function slideText() {
    const slide = S.slides[S.index];
    if (!slide) return "";
    return [slide.title, ...(slide.bullets || []).map((b) => `• ${b}`),
            slide.notes ? `\nNotes: ${slide.notes}` : ""].join("\n");
  }

  async function regenerateSlide() {
    if (S.busy || !S.deckId) return;
    const feedback = await A.prompt({
      title: `Regenerate slide ${S.index + 1}`,
      label: "Optional: what should change?",
      placeholder: "shorter, more examples, simpler wording…",
      okLabel: "Regenerate",
    });
    if (feedback === null) return;
    S.busy = true;
    try {
      const slide = await A.api("/api/presentations/slide", {
        method: "POST",
        body: { deck_id: S.deckId, index: S.index, feedback: feedback || null },
      });
      S.slides[S.index] = slide;
      renderSlide();
      markSlide(S.index, "done");
      A.toast("Slide regenerated");
    } catch (err) {
      A.toast(err.detail || "Could not regenerate that slide", { type: "err" });
    } finally { S.busy = false; }
  }

  /* ──────────────────────────── exports ───────────────────────────── */

  async function exportDeck(kind) {
    if (!S.deckId) { A.toast("Build a deck first", { type: "err" }); return; }
    const button = kind === "pptx" ? $("#deck-pptx") : $("#deck-html");
    const original = button.textContent;
    button.textContent = "Building…";
    button.disabled = true;
    try {
      const resp = await A.api(`/api/presentations/${kind}`, {
        method: "POST", raw: true,
        body: { deck_id: S.deckId, theme: $("#deck-theme").value || "ocean" },
      });
      const blob = await resp.blob();
      const safe = (S.title || "presentation").replace(/[^\w\-. ]+/g, "").trim() || "presentation";
      A.download(blob, kind === "pptx" ? `${safe}.pptx` : `${safe}-deck.html`);
      A.toast(kind === "pptx" ? ".pptx downloaded" : "HTML deck downloaded");
    } catch (err) {
      A.toast(err.detail || err.message || "Export failed", { type: "err" });
    } finally {
      button.textContent = original;
      button.disabled = false;
    }
  }
  A.exportDeck = exportDeck;
})();
