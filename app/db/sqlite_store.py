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
        "SELECT id, title, created_at, updated_at FROM conversations "
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
