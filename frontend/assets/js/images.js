/* ══════════════════════════════════════════════════════════════════════
   Aether — images
   Aspect-ratio picker, download, regenerate-with-same-prompt, prompt history.
   (No provider chrome in this pane: one discreet credit lives in Settings →
   About.)
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function images() {
  const A = window.Aether;
  const { $ } = A;

  const RATIOS = [
    { id: "1:1", label: "Square", size: [1024, 1024] },
    { id: "16:9", label: "Wide", size: [1280, 720] },
    { id: "9:16", label: "Tall", size: [720, 1280] },
    { id: "4:5", label: "Portrait", size: [1024, 1280] },
    { id: "3:2", label: "Photo", size: [1216, 832] },
  ];
  const IDEAS = [
    "a minimalist geometric logo on dark charcoal, single accent colour",
    "a nebula city at dusk, cinematic lighting, film grain",
    "a friendly robot studying at a desk, warm illustration",
    "an isometric cutaway of a bee hive, clean vector style",
    "a poster about the water cycle, bold shapes, limited palette",
  ];

  const S = { ratio: A.store.get("imageRatio", "1:1"), busy: false };
  let history = A.store.get("imageHistory", []);

  function size() {
    return (RATIOS.find((r) => r.id === S.ratio) || RATIOS[0]).size;
  }

  A.initImages = function initImages() {
    const picker = $("#image-ratio");
    picker.innerHTML = "";
    for (const ratio of RATIOS) {
      picker.append(A.el("button", {
        class: `ratio-chip${ratio.id === S.ratio ? " active" : ""}`,
        dataset: { ratio: ratio.id },
        title: `${ratio.label} ${ratio.id}`,
        onclick: () => {
          S.ratio = ratio.id;
          A.store.set("imageRatio", ratio.id);
          A.$$(".ratio-chip", picker).forEach((chip) => {
            chip.classList.toggle("active", chip.dataset.ratio === ratio.id);
          });
        },
      }, [
        A.el("span", { class: `ratio-shape r-${ratio.id.replace(":", "-")}` }),
        A.el("span", { class: "ratio-label", text: ratio.id }),
      ]));
    }
    $("#image-go").onclick = () => generate();
    $("#image-idea").onclick = () => {
      $("#image-prompt").value = IDEAS[Math.floor(Math.random() * IDEAS.length)];
      $("#image-prompt").focus();
    };
    $("#image-prompt").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) generate();
    });
    renderHistory();
  };

  function renderHistory() {
    const box = $("#image-history");
    box.innerHTML = "";
    if (!history.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    const label = A.el("span", { class: "muted small-text", text: "Recent:" });
    box.append(label);
    for (const prompt of history.slice(0, 6)) {
      box.append(A.el("button", {
        class: "chip small", text: prompt.length > 44 ? `${prompt.slice(0, 44)}…` : prompt,
        title: prompt,
        onclick: () => { $("#image-prompt").value = prompt; },
      }));
    }
  }

  function remember(prompt) {
    history = [prompt, ...history.filter((item) => item !== prompt)].slice(0, 12);
    A.store.set("imageHistory", history);
    renderHistory();
  }

  async function generate(promptOverride = null) {
    const prompt = (promptOverride ?? $("#image-prompt").value).trim();
    if (!prompt) { A.toast("Describe the image first", { type: "err" }); return; }
    if (S.busy) return;
    S.busy = true;
    $("#image-go").disabled = true;
    const [width, height] = size();
    const card = A.el("figure", { class: "image-card loading" }, [
      A.el("div", { class: "image-stage" }, [A.el("div", { class: "image-skeleton" })]),
      A.el("figcaption", { class: "image-cap" }, [
        A.el("span", { class: "muted small-text", text: "Generating — free tier can take 10–30s…" }),
      ]),
    ]);
    $("#image-results").prepend(card);
    const started = Date.now();
    try {
      const data = await A.api("/api/images/generate", {
        method: "POST",
        body: { prompt, width, height, model: $("#image-model")?.value || "flux" },
      });
      A.emit("usage:refresh");
      const img = new Image();
      const stage = card.querySelector(".image-stage");
      stage.innerHTML = "";
      stage.append(img);
      // The provider hands back a URL; the pixels arrive separately. Without a
      // deadline a stalled image would leave a spinner up forever.
      await new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error("The image took too long to load (free tier).")), 60000);
        img.onload = () => { clearTimeout(timer); resolve(); };
        img.onerror = () => {
          clearTimeout(timer);
          reject(new Error("The image could not be loaded — the free image server may be busy."));
        };
        img.src = data.url;
        img.alt = prompt;
        img.loading = "lazy";
      });
      card.classList.remove("loading");
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      card.querySelector(".image-cap").innerHTML = "";
      card.querySelector(".image-cap").append(
        A.el("span", {
          class: "image-prompt", text: prompt, title: prompt,
        }),
        A.el("span", { class: "muted small-text", text: `${data.width}×${data.height} · ${seconds}s` }),
        actionRow(prompt, data),
      );
      remember(prompt);
    } catch (err) {
      const message = err.detail || err.message;
      card.classList.remove("loading");
      card.classList.add("image-failed");
      const stage = card.querySelector(".image-stage");
      if (stage && !stage.querySelector("img")) {
        stage.innerHTML = "";
        stage.append(A.el("div", { class: "image-error", role: "alert" }, [
          A.icon("alert", 22),
          A.el("p", { class: "muted small-text", text: message }),
        ]));
      }
      card.querySelector(".image-cap").innerHTML = "";
      card.querySelector(".image-cap").append(
        A.el("span", { class: "image-prompt", text: prompt, title: prompt }),
        A.el("span", { class: "muted small-text", text: "Nothing was saved" }),
        A.el("div", { class: "image-actions" }, [
          A.el("button", {
            class: "btn small-btn primary", text: "Try again",
            onclick: () => { card.remove(); generate(prompt); },
          }),
          A.el("button", {
            class: "btn small-btn ghost", text: "Copy prompt",
            onclick: () => A.copy(prompt, "Prompt copied"),
          }),
        ]),
      );
      A.toast(message, { type: "err" });
      // Keep the failed prompt in history so it is easy to retry later.
      remember(prompt);
    } finally {
      S.busy = false;
      $("#image-go").disabled = false;
    }
  }

  function actionRow(prompt, data) {
    const row = A.el("div", { class: "image-actions" });
    const link = `/api/images/download?url=${encodeURIComponent(data.url)}&name=${encodeURIComponent(
      prompt.slice(0, 40).replace(/[^\w\-. ]+/g, "").trim() || "aether-image")}`;
    row.append(
      A.el("a", { class: "btn small-btn", href: link, html: `${A.icon("download", 13)}<span>Download</span>` }),
      A.el("button", {
        class: "btn small-btn", title: "Same prompt, new seed",
        html: `${A.icon("refresh", 13)}<span>Again</span>`,
        onclick: () => generate(prompt),
      }),
      A.el("button", {
        class: "btn small-btn ghost", title: "Copy prompt",
        html: `${A.icon("copy", 13)}<span>Copy</span>`,
        onclick: () => A.copy(prompt, "Prompt copied"),
      }),
    );
    return row;
  }
})();
