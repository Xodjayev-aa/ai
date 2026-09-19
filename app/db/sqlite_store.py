"""Row-level data access for users, conversations, messages, usage."""

import datetime
import json
import uuid

from app.db.engine import execute, query

DATE_FMT = "%Y-%m-%d"


# ---------------------------------------------------------------- users

def create_user(email: str, password_hash: str, is_admin: bool = False) -> int:
    res = execute(
        "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, ?)",
        (email.lower(), password_hash, 1 if is_admin else 0),
    )
    return int(res["last_insert_rowid"])


def get_user_by_email(email: str) -> dict | None:
    rows = query("SELECT * FROM users WHERE email = ?", (email.lower(),))
    return rows[0] if rows else None


def get_user_by_id(user_id: int) -> dict | None:
    rows = query("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


def count_users() -> int:
    rows = query("SELECT COUNT(*) AS n FROM users")
    return int(rows[0]["n"]) if rows else 0


# ------------------------------------------------------------ admin ops

def list_users(limit: int = 500) -> list[dict]:
    return query(
        "SELECT u.id, u.email, u.is_admin, u.created_at, "
        "COUNT(DISTINCT c.id) AS conversations, "
        "COUNT(DISTINCT m.id) AS messages "
        "FROM users u "
        "LEFT JOIN conversations c ON c.user_id = u.id "
        "LEFT JOIN messages m ON m.user_id = u.id "
        "GROUP BY u.id ORDER BY u.id LIMIT ?",
        (limit,),
    )


def set_admin(user_id: int, is_admin: bool) -> None:
    execute("UPDATE users SET is_admin = ? WHERE id = ?",
            (1 if is_admin else 0, user_id))


def update_password(user_id: int, password_hash: str) -> None:
    execute("UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id))


def delete_user(user_id: int) -> int:
    execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    execute("DELETE FROM usage_counters WHERE user_id = ?", (user_id,))
    res = execute("DELETE FROM users WHERE id = ?", (user_id,))
    return res["affected"]


def all_conversations(limit: int = 300) -> list[dict]:
    return query(
        "SELECT c.id, c.title, c.created_at, c.updated_at, "
        "c.user_id, u.email AS user_email, "
        "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
        "FROM conversations c LEFT JOIN users u ON u.id = c.user_id "
        "ORDER BY c.updated_at DESC LIMIT ?",
        (limit,),
    )


def admin_delete_conversation(conv_id: str) -> int:
    execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    res = execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    return res["affected"]


# --------------------------------------------------------- conversations

def create_conversation(user_id: int, title: str) -> dict:
    conv_id = str(uuid.uuid4())
    execute(
        "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
        (conv_id, user_id, title[:120] or "New chat"),
    )
    return {"id": conv_id, "title": title[:120] or "New chat"}


def list_conversations(user_id: int, limit: int = 100) -> list[dict]:
    return query(
        "SELECT id, title, folder, pinned, created_at, updated_at FROM conversations "
        "WHERE user_id = ? ORDER BY updated_at DESC, created_at DESC LIMIT ?",
        (user_id, limit),
    )


def get_conversation(conv_id: str, user_id: int) -> dict | None:
    rows = query(
        "SELECT id, title, created_at, updated_at FROM conversations "
        "WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    )
    return rows[0] if rows else None


def rename_conversation(conv_id: str, user_id: int, title: str) -> None:
    execute(
        "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND user_id = ?",
        (title[:120], conv_id, user_id),
    )


def touch_conversation(conv_id: str) -> None:
    execute(
        "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (conv_id,),
    )


def delete_conversation(conv_id: str, user_id: int) -> int:
    execute("DELETE FROM messages WHERE conversation_id = ? AND user_id = ?",
            (conv_id, user_id))
    res = execute("DELETE FROM conversations WHERE id = ? AND user_id = ?",
                  (conv_id, user_id))
    return res["affected"]


# --------------------------------------------------------------- messages

def add_message(conversation_id: str, user_id: int, role: str,
                content: str, meta: dict | None = None) -> dict:
    msg_id = str(uuid.uuid4())
    execute(
        "INSERT INTO messages (id, conversation_id, user_id, role, content, meta) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, conversation_id, user_id, role, content,
         json.dumps(meta) if meta else None),
    )
    touch_conversation(conversation_id)
    return {"id": msg_id, "conversation_id": conversation_id,
            "role": role, "content": content, "meta": meta}


def upsert_message(message_id: str, conversation_id: str, user_id: int,
                   role: str, content: str, meta: dict | None = None) -> dict:
    """Insert an assistant answer under a *client-supplied* id, or extend it.

    Used so the streamed answer and a partially-saved (stopped) answer are the
    same row: whoever writes last wins, but a shorter partial never overwrites
    a longer finished answer.
    """
    execute(
        "INSERT INTO messages (id, conversation_id, user_id, role, content, meta) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "content = CASE WHEN length(excluded.content) >= length(messages.content) "
        "THEN excluded.content ELSE messages.content END, "
        "meta = CASE WHEN length(excluded.content) >= length(messages.content) "
        "THEN excluded.meta ELSE messages.meta END",
        (message_id, conversation_id, user_id, role, content,
         json.dumps(meta) if meta else None),
    )
    touch_conversation(conversation_id)
    return {"id": message_id, "conversation_id": conversation_id,
            "role": role, "content": content, "meta": meta}


def list_messages(conversation_id: str, user_id: int,
                  limit: int = 200) -> list[dict]:
    rows = query(
        "SELECT id, role, content, meta, created_at FROM messages "
        "WHERE conversation_id = ? AND user_id = ? ORDER BY created_at ASC LIMIT ?",
        (conversation_id, user_id, limit),
    )
    for r in rows:
        if r.get("meta"):
            try:
                r["meta"] = json.loads(r["meta"])
            except (TypeError, ValueError):
                r["meta"] = None
    return rows


# ------------------------------------------------------------------ usage

def bump_usage(user_id: int, amount: int = 1) -> int:
    day = datetime.datetime.now(datetime.timezone.utc).strftime(DATE_FMT)
    execute(
        "INSERT INTO usage_counters (day, user_id, count) VALUES (?, ?, ?) "
        "ON CONFLICT (day, user_id) DO UPDATE SET count = count + 1",
        (day, user_id, amount),
    )
    return get_usage(user_id)


def get_usage(user_id: int) -> int:
    day = datetime.datetime.now(datetime.timezone.utc).strftime(DATE_FMT)
    rows = query(
        "SELECT count FROM usage_counters WHERE day = ? AND user_id = ?",
        (day, user_id),
    )
    return int(rows[0]["count"]) if rows else 0


def usage_stats() -> dict:
    today = datetime.datetime.now(datetime.timezone.utc).strftime(DATE_FMT)
    total_rows = query("SELECT COALESCE(SUM(count), 0) AS n FROM usage_counters")
    today_rows = query("SELECT COALESCE(SUM(count), 0) AS n FROM usage_counters WHERE day = ?",
                       (today,))
    conv_rows = query("SELECT COUNT(*) AS n FROM conversations")
    msg_rows = query("SELECT COUNT(*) AS n FROM messages")
    return {
        "requests_total": int(total_rows[0]["n"]) if total_rows else 0,
        "requests_today": int(today_rows[0]["n"]) if today_rows else 0,
        "conversations": int(conv_rows[0]["n"]) if conv_rows else 0,
        "messages": int(msg_rows[0]["n"]) if msg_rows else 0,
    }


# ════════════════════════════════════════════════════════ v3 features ════

# ------------------------------------------------------------- search

def search_messages(user_id: int, q: str, limit: int = 30) -> list[dict]:
    like = f"%{q[:80]}%"
    return query(
        "SELECT m.conversation_id, c.title, m.role, m.content, m.created_at "
        "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE m.user_id = ? AND m.content LIKE ? "
        "ORDER BY m.created_at DESC LIMIT ?",
        (user_id, like, limit),
    )


# -------------------------------------------------- settings / memory

def set_custom_instructions(user_id: int, text: str) -> None:
    execute("UPDATE users SET custom_instructions = ? WHERE id = ?",
            (text.strip()[:2000], user_id))


def get_custom_instructions(user_id: int) -> str:
    rows = query("SELECT custom_instructions FROM users WHERE id = ?", (user_id,))
    return (rows[0].get("custom_instructions") or "") if rows else ""


def set_auto_memory(user_id: int, enabled: bool) -> None:
    execute("UPDATE users SET auto_memory = ? WHERE id = ?",
            (1 if enabled else 0, user_id))


def get_auto_memory(user_id: int) -> bool:
    rows = query("SELECT auto_memory FROM users WHERE id = ?", (user_id,))
    return bool(rows[0].get("auto_memory", 1)) if rows else False


def add_memory(user_id: int, content: str) -> None:
    content = content.strip()[:300]
    if not content:
        return
    rows = query("SELECT COUNT(*) AS n FROM memories WHERE user_id = ?", (user_id,))
    if int(rows[0]["n"]) >= 50:  # keep newest 49, then add
        execute(
            "DELETE FROM memories WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT 49)",
            (user_id, user_id))
    execute("INSERT INTO memories (id, user_id, content) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), user_id, content))


def list_memories(user_id: int, limit: int = 50) -> list[dict]:
    return query(
        "SELECT id, content, created_at FROM memories WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?", (user_id, limit))


def delete_memory(memory_id: str, user_id: int) -> int:
    res = execute("DELETE FROM memories WHERE id = ? AND user_id = ?",
                  (memory_id, user_id))
    return res["affected"]


# -------------------------------------------------------------- personas

def create_persona(user_id: int, name: str, prompt: str) -> dict:
    pid = str(uuid.uuid4())
    execute("INSERT INTO personas (id, user_id, name, prompt) VALUES (?, ?, ?, ?)",
            (pid, user_id, name.strip()[:60], prompt.strip()[:2000]))
    return {"id": pid, "name": name.strip()[:60], "prompt": prompt.strip()[:2000]}


def list_personas(user_id: int) -> list[dict]:
    return query(
        "SELECT id, name, prompt FROM personas WHERE user_id = ? ORDER BY created_at",
        (user_id,))


def get_persona(persona_id: str, user_id: int) -> dict | None:
    rows = query("SELECT id, name, prompt FROM personas WHERE id = ? AND user_id = ?",
                 (persona_id, user_id))
    return rows[0] if rows else None


def delete_persona(persona_id: str, user_id: int) -> int:
    return execute("DELETE FROM personas WHERE id = ? AND user_id = ?",
                   (persona_id, user_id))["affected"]


# ----------------------------------------------------------- shared chats

def create_share(conversation_id: str, user_id: int) -> str | None:
    conv = get_conversation(conversation_id, user_id)
    if not conv:
        return None
    rows = query("SELECT token FROM shared_chats WHERE conversation_id = ?",
                 (conversation_id,))
    if rows:
        return rows[0]["token"]
    token = uuid.uuid4().hex[:12]
    execute("INSERT INTO shared_chats (token, conversation_id) VALUES (?, ?)",
            (token, conversation_id))
    return token


def get_shared(token: str) -> dict | None:
    """Public share lookup (no user check — token IS the capability)."""
    rows = query("SELECT conversation_id FROM shared_chats WHERE token = ?", (token,))
    if not rows:
        return None
    convs = query("SELECT id, title, created_at FROM conversations WHERE id = ?",
                  (rows[0]["conversation_id"],))
    if not convs:
        return None
    msgs = query(
        "SELECT role, content, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 400",
        (rows[0]["conversation_id"],))
    return {"title": convs[0]["title"], "created_at": convs[0]["created_at"],
            "messages": msgs}


def delete_share(conversation_id: str, user_id: int) -> int:
    conv = get_conversation(conversation_id, user_id)
    if not conv:
        return 0
    return execute("DELETE FROM shared_chats WHERE conversation_id = ?",
                   (conversation_id,))["affected"]


# ------------------------------------------------------------------ docs

def save_doc(user_id: int, name: str, text: str) -> str:
    doc_id = str(uuid.uuid4())
    execute("INSERT INTO docs (id, user_id, name, text) VALUES (?, ?, ?, ?)",
            (doc_id, user_id, name[:120], text[:40000]))
    return doc_id


def get_doc(doc_id: str, user_id: int) -> dict | None:
    rows = query("SELECT id, name, text FROM docs WHERE id = ? AND user_id = ?",
                 (doc_id, user_id))
    return rows[0] if rows else None


# ----------------------------------------------------------------- tasks

def create_task(user_id: int, conversation_id: str, prompt: str,
                hour_utc: int) -> dict:
    tid = str(uuid.uuid4())
    execute(
        "INSERT INTO tasks (id, user_id, conversation_id, prompt, hour_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (tid, user_id, conversation_id, prompt.strip()[:500],
         max(0, min(23, int(hour_utc)))))
    return {"id": tid, "conversation_id": conversation_id,
            "prompt": prompt.strip()[:500], "hour_utc": max(0, min(23, int(hour_utc)))}


def list_tasks(user_id: int) -> list[dict]:
    return query(
        "SELECT t.id, t.prompt, t.hour_utc, t.active, t.last_run, "
        "t.conversation_id, c.title AS conversation_title "
        "FROM tasks t LEFT JOIN conversations c ON c.id = t.conversation_id "
        "WHERE t.user_id = ? ORDER BY t.created_at DESC", (user_id,))


def delete_task(task_id: str, user_id: int) -> int:
    return execute("DELETE FROM tasks WHERE id = ? AND user_id = ?",
                   (task_id, user_id))["affected"]


def due_tasks(hour_utc: int, today: str) -> list[dict]:
    return query(
        "SELECT t.*, u.email FROM tasks t JOIN users u ON u.id = t.user_id "
        "WHERE t.active = 1 AND t.hour_utc = ? "
        "AND (t.last_run IS NULL OR t.last_run != ?)", (hour_utc, today))


def mark_task_run(task_id: str, today: str) -> None:
    execute("UPDATE tasks SET last_run = ? WHERE id = ?", (today, task_id))


# ------------------------------------------------- conversation metadata

def update_conv_meta(conv_id: str, user_id: int, *, title: str | None = None,
                     folder: str | None = None, pinned: bool | None = None) -> None:
    sets, params = [], []
    if title is not None:
        sets.append("title = ?"); params.append(title[:120])
    if folder is not None:
        sets.append("folder = ?"); params.append(folder.strip()[:60])
    if pinned is not None:
        sets.append("pinned = ?"); params.append(1 if pinned else 0)
    if not sets:
        return
    params += [conv_id, user_id]
    execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            tuple(params))


def get_conv_any(conv_id: str) -> dict | None:
    rows = query("SELECT id, title, created_at FROM conversations WHERE id = ?",
                 (conv_id,))
    return rows[0] if rows else None


# ═══════════════════════════════════════════════ v4: controls & decks ════

def update_memory(memory_id: str, user_id: int, content: str) -> int:
    return execute(
        "UPDATE memories SET content = ? WHERE id = ? AND user_id = ?",
        (content.strip()[:300], memory_id, user_id))["affected"]


def last_assistant_message(conversation_id: str, user_id: int) -> dict | None:
    rows = query(
        "SELECT id, content, meta FROM messages WHERE conversation_id = ? "
        "AND user_id = ? AND role = 'assistant' ORDER BY rowid DESC LIMIT 1",
        (conversation_id, user_id))
    return rows[0] if rows else None


def delete_message(message_id: str, user_id: int) -> int:
    return execute("DELETE FROM messages WHERE id = ? AND user_id = ?",
                   (message_id, user_id))["affected"]


def truncate_from_message(conversation_id: str, user_id: int,
                          message_id: str) -> int:
    """Delete one message and everything after it (used by "Edit" on the last
    user message: the conversation rewinds to that point, then re-sends)."""
    rows = query("SELECT rowid AS rid FROM messages WHERE id = ? AND user_id = ?",
                 (message_id, user_id))
    if not rows:
        return 0
    rid = rows[0]["rid"]
    return execute(
        "DELETE FROM messages WHERE conversation_id = ? AND user_id = ? "
        "AND rowid >= ?", (conversation_id, user_id, rid))["affected"]


# ------------------------------------------------------------------ decks

def create_deck(user_id: int, title: str, subtitle: str, topic: str,
                outline: dict, status: str = "planning") -> dict:
    deck_id = str(uuid.uuid4())
    execute(
        "INSERT INTO decks (id, user_id, title, subtitle, topic, outline, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (deck_id, user_id, title[:150], subtitle[:200], topic[:2000],
         json.dumps(outline), status))
    return {"id": deck_id, "title": title, "subtitle": subtitle,
            "topic": topic, "outline": outline, "status": status}


def get_deck(deck_id: str, user_id: int) -> dict | None:
    rows = query(
        "SELECT id, title, subtitle, topic, outline, status, created_at, updated_at "
        "FROM decks WHERE id = ? AND user_id = ?", (deck_id, user_id))
    if not rows:
        return None
    deck = rows[0]
    try:
        deck["outline"] = json.loads(deck.get("outline") or "{}")
    except (TypeError, ValueError):
        deck["outline"] = {}
    return deck


def save_deck_outline(deck_id: str, user_id: int, outline: dict,
                      status: str = "ready", title: str | None = None,
                      subtitle: str | None = None) -> int:
    sets = ["outline = ?", "status = ?", "updated_at = CURRENT_TIMESTAMP"]
    params: list = [json.dumps(outline), status]
    if title is not None:
        sets.append("title = ?"); params.append(title[:150])
    if subtitle is not None:
        sets.append("subtitle = ?"); params.append(subtitle[:200])
    params += [deck_id, user_id]
    return execute(f"UPDATE decks SET {', '.join(sets)} "
                   "WHERE id = ? AND user_id = ?", tuple(params))["affected"]


def list_decks(user_id: int, limit: int = 30) -> list[dict]:
    return query(
        "SELECT id, title, subtitle, topic, status, created_at, updated_at "
        "FROM decks WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit))


def delete_deck(deck_id: str, user_id: int) -> int:
    return execute("DELETE FROM decks WHERE id = ? AND user_id = ?",
                   (deck_id, user_id))["affected"]
