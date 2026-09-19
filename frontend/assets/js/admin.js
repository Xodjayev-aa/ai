/* ══════════════════════════════════════════════════════════════════════
   Aether — admin dashboard (owners/admins only)
   ══════════════════════════════════════════════════════════════════════ */
"use strict";

(function admin() {
  const A = window.Aether;
  const { $ } = A;

  A.initAdmin = function initAdmin() {
    /* the pane is rendered on demand by A.loadAdmin */
  };

  A.loadAdmin = async function loadAdmin() {
    const body = $("#admin-body");
    if (!body) return;
    try {
      const data = await A.api("/api/admin/overview");
      render(data);
    } catch (err) {
      body.innerHTML = "";
      body.append(A.el("p", { class: "muted pad", text: err.message }));
    }
  };

  function statCard(value, label) {
    return A.el("div", { class: "stat-card" }, [
      A.el("div", { class: "stat-v", text: String(value ?? 0) }),
      A.el("div", { class: "stat-k", text: label }),
    ]);
  }

  function render(data) {
    const stats = data.stats || {};
    const cards = $("#admin-cards");
    cards.innerHTML = "";
    for (const [value, label] of [
      [(data.users || []).length, "Users"],
      [stats.conversations, "Conversations"],
      [stats.messages, "Messages"],
      [stats.requests_today, "AI requests today"],
      [stats.requests_total, "AI requests total"],
    ]) cards.append(statCard(value, label));

    const ownerEmail = data.ai?.owner || "";
    const table = $("#admin-users");
    table.innerHTML = "<tr><th>Email</th><th>Chats</th><th>Msgs</th><th>Role</th><th></th></tr>";
    for (const user of data.users || []) {
      const isOwner = user.email === ownerEmail;
      const role = user.is_admin ? (isOwner ? "owner" : "admin") : "user";
      const actions = A.el("td", { class: "rowacts" });
      if (!isOwner && !user.is_me) {
        actions.append(
          A.el("button", {
            class: "link-btn",
            text: user.is_admin ? "demote" : "promote",
            onclick: async () => {
              await A.api(`/api/admin/users/${user.id}/admin`, {
                method: "POST", body: { is_admin: !user.is_admin },
              });
              A.loadAdmin();
            },
          }),
          A.el("button", {
            class: "link-btn danger", text: "delete",
            onclick: async () => {
              const ok = await A.confirm({
                title: `Delete ${user.email}?`,
                message: "This removes the account and all of its data.",
                okLabel: "Delete", danger: true,
              });
              if (!ok) return;
              await A.api(`/api/admin/users/${user.id}`, { method: "DELETE" });
              A.loadAdmin();
            },
          }),
        );
      }
      const row = A.el("tr", {}, [
        A.el("td", { text: user.email }),
        A.el("td", { text: String(user.conversations) }),
        A.el("td", { text: String(user.messages) }),
        A.el("td", {}, [A.el("span", { class: `badge${user.is_admin ? " ok" : ""}`, text: role })]),
        actions,
      ]);
      table.append(row);
    }

    const convs = $("#admin-convs");
    convs.innerHTML = "<tr><th>Title</th><th>User</th><th>Msgs</th><th></th></tr>";
    for (const conv of data.conversations || []) {
      convs.append(A.el("tr", {}, [
        A.el("td", { text: (conv.title || "").slice(0, 40) }),
        A.el("td", { text: conv.user_email || "?" }),
        A.el("td", { text: String(conv.message_count) }),
        A.el("td", { class: "rowacts" }, [
          A.el("button", {
            class: "link-btn danger", text: "delete",
            onclick: async () => {
              const ok = await A.confirm({ title: "Delete this conversation?", okLabel: "Delete", danger: true });
              if (!ok) return;
              await A.api(`/api/admin/conversations/${conv.id}`, { method: "DELETE" });
              A.loadAdmin();
            },
          }),
        ]),
      ]));
    }

    const ai = data.ai || {};
    const box = $("#admin-ai");
    box.innerHTML = "";
    box.append(
      A.el("p", { class: "small-text" }, [
        A.el("span", { class: "muted", text: "Storage: " }),
        A.el("b", { text: ai.database?.label || ai.database_mode || "?" }),
      ]),
      A.el("p", { class: "muted small-text", text: "Providers (rotate in order, auto-failover):" }),
    );
    for (const provider of ai.providers || []) {
      const state = provider.disabled ? "disabled" : provider.cooling_down ? "cooling" : "ready";
      box.append(A.el("div", { class: `prov-row ${state}` }, [
        A.el("span", { text: provider.name }),
        A.el("span", {
          class: "muted small-text",
          text: `${provider.label || provider.key || ""} · ${state} · ok ${provider.ok} / fail ${provider.failed}`,
        }),
      ]));
    }
    const queue = ai.keyless?.queue;
    if (queue) {
      box.append(A.el("p", {
        class: "muted small-text",
        text: `Keyless queue: ${queue.queued_requests} waiting · interval ${queue.interval_seconds}s · `
            + `${queue.requests_last_minute} req/min · rate-limited ${queue.rate_limited}×`,
      }));
    }
    box.append(A.el("button", {
      class: "btn ghost small-btn", text: "Refresh",
      onclick: () => A.loadAdmin(),
    }));
  }
})();
