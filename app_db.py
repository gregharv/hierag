import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).with_name("scraper.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_question(text: str) -> str:
    parts = " ".join(text.strip().lower().split())
    return parts


def hash_question(question_norm: str) -> str:
    return hashlib.sha256(question_norm.encode("utf-8")).hexdigest()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY,
              created_at TEXT NOT NULL,
              display_name TEXT
            );

            CREATE TABLE IF NOT EXISTS user_ips (
              ip TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS chats (
              id INTEGER PRIMARY KEY,
              user_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              last_message_at TEXT,
              title TEXT,
              FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY,
              chat_id INTEGER NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              sources_json TEXT,
              debug_json TEXT,
              created_at TEXT NOT NULL,
              stream_id TEXT,
              cached_from INTEGER,
              question_norm TEXT,
              FOREIGN KEY (chat_id) REFERENCES chats(id),
              FOREIGN KEY (cached_from) REFERENCES cache_entries(id)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
              ON messages(chat_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_stream_id
              ON messages(stream_id);

            CREATE TABLE IF NOT EXISTS feedback (
              id INTEGER PRIMARY KEY,
              message_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              rating INTEGER NOT NULL,
              note TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (message_id) REFERENCES messages(id),
              FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_message
              ON feedback(message_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_user_created
              ON feedback(user_id, created_at);

            CREATE TABLE IF NOT EXISTS cache_entries (
              id INTEGER PRIMARY KEY,
              question_norm TEXT NOT NULL UNIQUE,
              question_hash TEXT NOT NULL UNIQUE,
              answer_text TEXT NOT NULL,
              sources_json TEXT,
              good_count INTEGER NOT NULL DEFAULT 0,
              bad_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_used_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_cache_question_hash
              ON cache_entries(question_hash);
            CREATE INDEX IF NOT EXISTS idx_cache_updated_at
              ON cache_entries(updated_at);
            """
        )
        message_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(messages);").fetchall()
        }
        if "debug_json" not in message_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN debug_json TEXT;")

        user = conn.execute("SELECT id FROM users WHERE id = 1;").fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (id, created_at, display_name) VALUES (?, ?, ?);",
                (1, _now_iso(), "default"),
            )

        chat = conn.execute("SELECT id FROM chats WHERE id = 1;").fetchone()
        if not chat:
            conn.execute(
                "INSERT INTO chats (id, user_id, created_at, title) VALUES (?, ?, ?, ?);",
                (1, 1, _now_iso(), "Default chat"),
            )


def get_or_create_user_by_ip(ip: str) -> int:
    cleaned = (ip or "").strip()
    if not cleaned:
        cleaned = "unknown"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM user_ips WHERE ip = ?;",
            (cleaned,),
        ).fetchone()
        if row:
            return int(row["user_id"])

        existing = conn.execute("SELECT COUNT(*) AS c FROM user_ips;").fetchone()
        if existing and int(existing["c"]) == 0:
            legacy = conn.execute("SELECT id FROM users WHERE id = 1;").fetchone()
            if legacy:
                conn.execute(
                    "INSERT INTO user_ips (ip, user_id, created_at) VALUES (?, ?, ?);",
                    (cleaned, int(legacy["id"]), _now_iso()),
                )
                return int(legacy["id"])

        cur = conn.execute(
            "INSERT INTO users (created_at, display_name) VALUES (?, ?);",
            (_now_iso(), cleaned),
        )
        user_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO user_ips (ip, user_id, created_at) VALUES (?, ?, ?);",
            (cleaned, user_id, _now_iso()),
        )
        return user_id


def list_profiles(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ip, user_id, created_at
            FROM user_ips
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_recent_messages(chat_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, sources_json, debug_json, created_at
            FROM messages
            WHERE chat_id = ?
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (chat_id, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def list_chats(user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, created_at, last_message_at
            FROM chats
            WHERE user_id = ?
            ORDER BY COALESCE(last_message_at, created_at) DESC
            LIMIT ?;
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def create_chat(user_id: int, title: str = "New chat") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO chats (user_id, created_at, title)
            VALUES (?, ?, ?);
            """,
            (user_id, _now_iso(), title),
        )
        return int(cur.lastrowid)


def chat_belongs_to_user(chat_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM chats WHERE id = ? AND user_id = ?;",
            (chat_id, user_id),
        ).fetchone()
        return bool(row)


def rename_chat(chat_id: int, user_id: int, title: str) -> bool:
    cleaned = " ".join(title.strip().split())
    if not cleaned:
        return False
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET title = ? WHERE id = ? AND user_id = ?;",
            (cleaned[:80], chat_id, user_id),
        )
        return cur.rowcount > 0


def delete_chat(chat_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT 1 FROM chats WHERE id = ? AND user_id = ?;",
            (chat_id, user_id),
        ).fetchone()
        if not owner:
            return False
        conn.execute(
            """
            DELETE FROM feedback
            WHERE message_id IN (
                SELECT id FROM messages WHERE chat_id = ?
            );
            """,
            (chat_id,),
        )
        conn.execute(
            "DELETE FROM messages WHERE chat_id = ?;",
            (chat_id,),
        )
        cur = conn.execute("DELETE FROM chats WHERE id = ?;", (chat_id,))
        return cur.rowcount > 0


def maybe_update_chat_title(chat_id: int, title: str) -> None:
    cleaned = " ".join(title.strip().split())
    if not cleaned:
        return
    short = cleaned[:80]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title FROM chats WHERE id = ?;",
            (chat_id,),
        ).fetchone()
        if not row:
            return
        existing = row["title"]
        if existing and existing.strip() and existing != "New chat":
            return
        conn.execute(
            "UPDATE chats SET title = ? WHERE id = ?;",
            (short, chat_id),
        )


def insert_message(
    chat_id: int,
    role: str,
    content: str,
    sources_json: Optional[str] = None,
    stream_id: Optional[str] = None,
    question_norm: Optional[str] = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages
              (chat_id, role, content, sources_json, created_at, stream_id, question_norm)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                chat_id,
                role,
                content,
                sources_json,
                _now_iso(),
                stream_id,
                question_norm,
            ),
        )
        conn.execute(
            "UPDATE chats SET last_message_at = ? WHERE id = ?;",
            (_now_iso(), chat_id),
        )
        return int(cur.lastrowid)


def update_message(
    message_id: int,
    content: Optional[str] = None,
    sources_json: Optional[str] = None,
    debug_json: Optional[str] = None,
    cached_from: Optional[int] = None,
) -> None:
    updates = []
    params: List[Any] = []
    if content is not None:
        updates.append("content = ?")
        params.append(content)
    if sources_json is not None:
        updates.append("sources_json = ?")
        params.append(sources_json)
    if debug_json is not None:
        updates.append("debug_json = ?")
        params.append(debug_json)
    if cached_from is not None:
        updates.append("cached_from = ?")
        params.append(cached_from)
    if not updates:
        return
    params.append(message_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE messages SET {', '.join(updates)} WHERE id = ?;",
            params,
        )


def get_message(message_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?;",
            (message_id,),
        ).fetchone()
    return dict(row) if row else None


def get_prev_user_message(chat_id: int, created_at: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM messages
            WHERE chat_id = ?
              AND role = 'user'
              AND created_at <= ?
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (chat_id, created_at),
        ).fetchone()
    return dict(row) if row else None


def insert_feedback(
    message_id: int,
    user_id: int,
    rating: int,
    note: Optional[str] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO feedback (message_id, user_id, rating, note, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (message_id, user_id, rating, note, _now_iso()),
        )


def get_cache_answer(question: str) -> Optional[Dict[str, Any]]:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM cache_entries
            WHERE question_hash = ?
              AND good_count >= 1
              AND bad_count = 0;
            """,
            (question_hash,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE cache_entries SET last_used_at = ? WHERE id = ?;",
                (_now_iso(), row["id"]),
            )
    return dict(row) if row else None


def upsert_cache_good(
    question: str,
    answer_text: str,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> int:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    sources_json = json.dumps(sources or [], ensure_ascii=True)
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, good_count, bad_count FROM cache_entries WHERE question_hash = ?;",
            (question_hash,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE cache_entries
                SET answer_text = ?, sources_json = ?, good_count = ?,
                    updated_at = ?, last_used_at = ?
                WHERE id = ?;
                """,
                (
                    answer_text,
                    sources_json,
                    int(row["good_count"]) + 1,
                    now,
                    now,
                    row["id"],
                ),
            )
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO cache_entries
              (question_norm, question_hash, answer_text, sources_json,
               good_count, bad_count, created_at, updated_at, last_used_at)
            VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?);
            """,
            (question_norm, question_hash, answer_text, sources_json, now, now, now),
        )
        return int(cur.lastrowid)


def update_cache_bad(question: str) -> None:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    now = _now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, bad_count FROM cache_entries WHERE question_hash = ?;",
            (question_hash,),
        ).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE cache_entries
            SET bad_count = ?, updated_at = ?
            WHERE id = ?;
            """,
            (int(row["bad_count"]) + 1, now, row["id"]),
        )
