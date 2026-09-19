/* ══════════════════════════════════════════════════════════════════════
   Aether — small tools: news headlines and scheduled prompts
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function tools() {
  const A = window.Aether;
  const { $ } = A;

  /* ───────────────────────────── news ─────────────────────────────── */

  A.initNews = function initNews() {
    $("#news-go").onclick = async () => {
      const box = $("#news-results");
      const query = $("#news-topic").value.trim();
      box.innerHTML = "";
      for (let i = 0; i < 4; i += 1) box.append(A.el("div", { class: "skeleton skeleton-line tall" }));
      try {
        const data = await A.api(`/api/news${query ? `?q=${encodeURIComponent(query)}` : ""}`);
        box.innerHTML = "";
        if (!data.items?.length) {
          box.append(A.el("p", { class: "muted pad", text: "No headlines found." }));
          return;
        }
        for (const item of data.items) {
          box.append(A.el("article", { class: "news-item" }, [
            A.el("a", {
              href: item.link, target: "_blank", rel: "noopener noreferrer",
              text: item.title,
            }),
            A.el("span", { class: "muted small-text", text: `${item.source || ""} · ${item.published || ""}` }),
          ]));
        }
      } catch (err) {
        box.innerHTML = "";
        box.append(A.el("p", { class: "muted pad", text: err.message }));
      }
    };
  };

  /* ───────────────────────────── tasks ────────────────────────────── */

  A.initTasks = function initTasks() {
    const hour = $("#task-hour");
    if (hour && !hour.options.length) {
      for (let h = 0; h < 24; h += 1) {
        hour.append(A.el("option", { value: h, text: `${String(h).padStart(2, "0")}:00 UTC` }));
      }
      hour.value = "6";
    }
    $("#task-add").onclick = async () => {
      const prompt = $("#task-prompt").value.trim();
      if (!prompt) return;
      try {
        await A.api("/api/tasks", {
          method: "POST", body: { prompt, hour_utc: Number(hour.value) },
        });
        $("#task-prompt").value = "";
        A.toast("Scheduled — the answer will land in a chat");
        A.loadTasks();
      } catch (err) { A.toast(err.detail || "Could not schedule that", { type: "err" }); }
    };
  };

  A.loadTasks = async function loadTasks() {
    const box = $("#task-list");
    if (!box) return;
    try {
      const tasks = await A.api("/api/tasks");
      box.innerHTML = "";
      if (!tasks.length) {
        box.append(A.el("p", { class: "muted small-text", text: "No scheduled prompts yet." }));
        return;
      }
      for (const task of tasks) {
        box.append(A.el("div", { class: "memory-row" }, [
          A.el("span", { class: "memory-text" }, [
            A.el("strong", { text: task.prompt.slice(0, 70) }),
            A.el("br"),
            A.el("span", {
              class: "muted small-text",
              text: `daily at ${String(task.hour_utc).padStart(2, "0")}:00 UTC · `
                  + (task.last_run ? `last run ${task.last_run}` : "not run yet"),
            }),
          ]),
          A.el("button", {
            class: "icon-btn", title: "Delete", html: A.icon("trash", 14),
            onclick: async () => {
              await A.api(`/api/tasks/${task.id}`, { method: "DELETE" });
              A.loadTasks();
            },
          }),
        ]));
      }
    } catch { /* offline */ }
  };
})();
