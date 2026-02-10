from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastlite import database

try:
    from .models import ensure_app_schema
except ImportError:
    import sys

    _project_root = Path(__file__).resolve().parents[1]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    from core.models import ensure_app_schema

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app_runtime.db"


def _resolve_db_path() -> Path:
    configured = os.getenv("HIERAG_APP_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    default_path = DEFAULT_DB_PATH.resolve()
    default_path.parent.mkdir(parents=True, exist_ok=True)
    return default_path


DB_PATH = _resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
db = database(str(DB_PATH))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_question(text: str) -> str:
    return " ".join(text.strip().lower().split())


def hash_question(question_norm: str) -> str:
    return hashlib.sha256(question_norm.encode("utf-8")).hexdigest()


def _ensure_optional_columns() -> None:
    message_cols = {row["name"] for row in db.q("PRAGMA table_info(messages);")}
    if "debug_json" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN debug_json TEXT;")
    if "cached_from" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN cached_from INTEGER;")
    if "question_norm" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN question_norm TEXT;")

    cache_cols = {row["name"] for row in db.q("PRAGMA table_info(cache_entries);")}
    if "last_used_at" not in cache_cols:
        db.q("ALTER TABLE cache_entries ADD COLUMN last_used_at TEXT;")


def _ensure_default_user_and_chat() -> None:
    user = list(db.t.users.rows_where("id=?", [1], limit=1))
    if not user:
        db.t.users.insert(id=1, created_at=_now_iso(), display_name="default")

    chat = list(db.t.chats.rows_where("id=?", [1], limit=1))
    if not chat:
        db.t.chats.insert(id=1, user_id=1, created_at=_now_iso(), title="Default chat")


def create_db_and_tables() -> None:
    ensure_app_schema(db)
    _ensure_optional_columns()
    _ensure_default_user_and_chat()


def get_or_create_user_by_ip(ip: str) -> int:
    cleaned = (ip or "").strip() or "unknown"
    existing = list(db.t.user_ips.rows_where("ip=?", [cleaned], limit=1))
    if existing:
        return int(existing[0]["user_id"])

    profile_count = len(list(db.t.user_ips()))
    if profile_count == 0:
        default_user = list(db.t.users.rows_where("id=?", [1], limit=1))
        if default_user:
            db.t.user_ips.insert(ip=cleaned, user_id=1, created_at=_now_iso())
            return 1

    user = db.t.users.insert(created_at=_now_iso(), display_name=cleaned)
    user_id = int(user["id"])
    db.t.user_ips.insert(ip=cleaned, user_id=user_id, created_at=_now_iso())
    return user_id


def list_profiles(limit: int = 100) -> list[dict[str, Any]]:
    rows = list(db.t.user_ips())
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[:limit]


def list_recent_messages(chat_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = list(db.t.messages.rows_where("chat_id=?", [chat_id]))
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return list(reversed(rows[:limit]))


def list_chats(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = list(db.t.chats.rows_where("user_id=?", [user_id]))
    rows.sort(
        key=lambda row: row.get("last_message_at") or row.get("created_at") or "",
        reverse=True,
    )
    return rows[:limit]


def create_chat(user_id: int, title: str = "New chat") -> int:
    row = db.t.chats.insert(user_id=user_id, created_at=_now_iso(), title=title)
    return int(row["id"])


def chat_belongs_to_user(chat_id: int, user_id: int) -> bool:
    row = list(db.t.chats.rows_where("id=? AND user_id=?", [chat_id, user_id], limit=1))
    return bool(row)


def rename_chat(chat_id: int, user_id: int, title: str) -> bool:
    cleaned = " ".join(title.strip().split())
    if not cleaned:
        return False

    row = list(db.t.chats.rows_where("id=? AND user_id=?", [chat_id, user_id], limit=1))
    if not row:
        return False
    db.t.chats.update({"id": chat_id, "title": cleaned[:80]})
    return True


def delete_chat(chat_id: int, user_id: int) -> bool:
    owner = list(db.t.chats.rows_where("id=? AND user_id=?", [chat_id, user_id], limit=1))
    if not owner:
        return False

    messages = list(db.t.messages.rows_where("chat_id=?", [chat_id]))
    for message in messages:
        for feedback in db.t.feedback.rows_where("message_id=?", [message["id"]]):
            db.t.feedback.delete(feedback["id"])
        db.t.messages.delete(message["id"])

    db.t.chats.delete(chat_id)
    return True


def maybe_update_chat_title(chat_id: int, title: str) -> None:
    cleaned = " ".join(title.strip().split())
    if not cleaned:
        return

    row = list(db.t.chats.rows_where("id=?", [chat_id], limit=1))
    if not row:
        return

    existing = (row[0].get("title") or "").strip()
    if existing and existing != "New chat":
        return
    db.t.chats.update({"id": chat_id, "title": cleaned[:80]})


def insert_message(
    chat_id: int,
    role: str,
    content: str,
    sources_json: str | None = None,
    stream_id: str | None = None,
    question_norm: str | None = None,
) -> int:
    now = _now_iso()
    row = db.t.messages.insert(
        chat_id=chat_id,
        role=role,
        content=content,
        sources_json=sources_json,
        created_at=now,
        stream_id=stream_id,
        question_norm=question_norm,
    )
    db.t.chats.update({"id": chat_id, "last_message_at": now})
    return int(row["id"])


def update_message(
    message_id: int,
    content: str | None = None,
    sources_json: str | None = None,
    debug_json: str | None = None,
    cached_from: int | None = None,
) -> None:
    if all(value is None for value in (content, sources_json, debug_json, cached_from)):
        return

    row = list(db.t.messages.rows_where("id=?", [message_id], limit=1))
    if not row:
        return

    payload: dict[str, Any] = {"id": message_id}
    if content is not None:
        payload["content"] = content
    if sources_json is not None:
        payload["sources_json"] = sources_json
    if debug_json is not None:
        payload["debug_json"] = debug_json
    if cached_from is not None:
        payload["cached_from"] = cached_from
    db.t.messages.update(payload)


def get_message(message_id: int) -> dict[str, Any] | None:
    row = list(db.t.messages.rows_where("id=?", [message_id], limit=1))
    return row[0] if row else None


def get_prev_user_message(chat_id: int, created_at: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in db.t.messages.rows_where("chat_id=?", [chat_id])
        if row.get("role") == "user" and (row.get("created_at") or "") <= created_at
    ]
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[0] if rows else None


def insert_feedback(
    message_id: int,
    user_id: int,
    rating: int,
    note: str | None = None,
) -> None:
    db.t.feedback.insert(
        message_id=message_id,
        user_id=user_id,
        rating=rating,
        note=note,
        created_at=_now_iso(),
    )


def get_cache_answer(question: str) -> dict[str, Any] | None:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    rows = list(
        db.t.cache_entries.rows_where(
            "question_hash=? AND good_count>=1 AND bad_count=0",
            [question_hash],
            limit=1,
        )
    )
    if not rows:
        return None

    row = rows[0]
    db.t.cache_entries.update({"id": row["id"], "last_used_at": _now_iso()})
    row["last_used_at"] = _now_iso()
    return row


def upsert_cache_good(
    question: str,
    answer_text: str,
    sources: list[dict[str, Any]] | None = None,
) -> int:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    sources_json = json.dumps(sources or [], ensure_ascii=True)
    now = _now_iso()

    existing = list(db.t.cache_entries.rows_where("question_hash=?", [question_hash], limit=1))
    if existing:
        row = existing[0]
        db.t.cache_entries.update(
            {
                "id": row["id"],
                "answer_text": answer_text,
                "sources_json": sources_json,
                "good_count": int(row.get("good_count") or 0) + 1,
                "updated_at": now,
                "last_used_at": now,
            }
        )
        return int(row["id"])

    row = db.t.cache_entries.insert(
        question_norm=question_norm,
        question_hash=question_hash,
        answer_text=answer_text,
        sources_json=sources_json,
        good_count=1,
        bad_count=0,
        created_at=now,
        updated_at=now,
        last_used_at=now,
    )
    return int(row["id"])


def update_cache_bad(question: str) -> None:
    question_norm = normalize_question(question)
    question_hash = hash_question(question_norm)
    existing = list(db.t.cache_entries.rows_where("question_hash=?", [question_hash], limit=1))
    if not existing:
        return

    row = existing[0]
    db.t.cache_entries.update(
        {
            "id": row["id"],
            "bad_count": int(row.get("bad_count") or 0) + 1,
            "updated_at": _now_iso(),
        }
    )


# %%
if __name__ == "__main__":
    original_path = DB_PATH
    temp_db = database(":memory:")
    _original_db = db
    db = temp_db  # type: ignore[assignment]
    try:
        create_db_and_tables()
        user_id = get_or_create_user_by_ip("127.0.0.1")
        chat_id = create_chat(user_id=user_id, title="Check Chat")
        message_id = insert_message(
            chat_id=chat_id,
            role="user",
            content="hello world",
            question_norm=normalize_question("hello world"),
        )
        assert chat_belongs_to_user(chat_id, user_id)
        assert get_message(message_id) is not None
        assert hash_question(normalize_question("hello world"))
        upsert_cache_good("hello world", "answer", [{"url": "https://example.com"}])
        assert get_cache_answer("hello world") is not None
    finally:
        db = _original_db  # type: ignore[assignment]
        _ = original_path

    print("Check Passed")
