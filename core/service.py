from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
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


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_page(page: int | None) -> int:
    try:
        parsed = int(page or 1)
    except Exception:
        return 1
    return parsed if parsed >= 1 else 1


def _coerce_page_size(page_size: int | None, *, default: int = 25, maximum: int = 100) -> int:
    try:
        parsed = int(page_size or default)
    except Exception:
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum)


def _paginate_rows(rows: list[dict[str, Any]], *, page: int, page_size: int) -> dict[str, Any]:
    total = len(rows)
    total_pages = max(1, math.ceil(total / page_size)) if page_size else 1
    safe_page = min(max(page, 1), total_pages)
    start_idx = (safe_page - 1) * page_size
    end_idx = start_idx + page_size
    return {
        "items": rows[start_idx:end_idx],
        "pagination": {
            "page": safe_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


def _feedback_rating_matches(value: int | None, rating_filter: str | None) -> bool:
    normalized = str(rating_filter or "").strip().lower()
    if not normalized or normalized == "all":
        return True
    if normalized in {"positive", "1", "+1"}:
        return int(value or 0) == 1
    if normalized in {"negative", "-1"}:
        return int(value or 0) == -1
    if normalized in {"unrated", "0", "none"}:
        return int(value or 0) == 0
    return True


def _matches_search(row: dict[str, Any], search: str | None) -> bool:
    needle = str(search or "").strip().lower()
    if not needle:
        return True
    haystacks = [
        row.get("user_id"),
        row.get("question"),
        row.get("query_effective"),
        row.get("query_rewritten"),
        row.get("answer"),
        row.get("note"),
    ]
    for source in row.get("sources") or []:
        if isinstance(source, dict):
            haystacks.append(source.get("url"))
            haystacks.append(source.get("url_canonical"))
    return any(needle in str(item or "").lower() for item in haystacks)


def _resolve_time_bounds(
    *,
    range_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    current = now or datetime.now(timezone.utc)
    normalized = str(range_name or "30d").strip().lower()
    if normalized in {"", "all"}:
        return None, None
    if normalized == "24h":
        return current - timedelta(hours=24), None
    if normalized == "7d":
        return current - timedelta(days=7), None
    if normalized == "30d":
        return current - timedelta(days=30), None
    if normalized == "custom":
        return _parse_iso_datetime(start), _parse_iso_datetime(end)
    return None, None


def normalize_question(text: str) -> str:
    return " ".join(text.strip().lower().split())


def hash_question(question_norm: str) -> str:
    return hashlib.sha256(question_norm.encode("utf-8")).hexdigest()


def normalize_login_code(value: str) -> str:
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum()).upper()
    return cleaned if 6 <= len(cleaned) <= 7 else ""


def _ensure_optional_columns() -> None:
    user_cols = {row["name"] for row in db.q("PRAGMA table_info(users);")}
    if "login_code" not in user_cols:
        db.q("ALTER TABLE users ADD COLUMN login_code TEXT;")
    db.q("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_code ON users(login_code);")

    message_cols = {row["name"] for row in db.q("PRAGMA table_info(messages);")}
    if "debug_json" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN debug_json TEXT;")
    if "cached_from" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN cached_from INTEGER;")
    if "question_norm" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN question_norm TEXT;")
    if "app_version" not in message_cols:
        db.q("ALTER TABLE messages ADD COLUMN app_version TEXT;")

    cache_cols = {row["name"] for row in db.q("PRAGMA table_info(cache_entries);")}
    if "last_used_at" not in cache_cols:
        db.q("ALTER TABLE cache_entries ADD COLUMN last_used_at TEXT;")

    db.q(
        """
        CREATE TABLE IF NOT EXISTS user_ip_logs (
            id INTEGER PRIMARY KEY,
            ip TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    db.q("CREATE INDEX IF NOT EXISTS idx_user_ip_logs_user_id ON user_ip_logs(user_id);")
    db.q("CREATE INDEX IF NOT EXISTS idx_user_ip_logs_ip ON user_ip_logs(ip);")
    db.q("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ip_logs_user_ip ON user_ip_logs(user_id, ip);")


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


def record_user_ip(user_id: int, ip: str) -> None:
    cleaned = (ip or "").strip()
    if not cleaned:
        return
    existing = list(db.t.user_ip_logs.rows_where("user_id=? AND ip=?", [user_id, cleaned], limit=1))
    if existing:
        return
    db.t.user_ip_logs.insert(ip=cleaned, user_id=user_id, created_at=_now_iso())


def get_or_create_user_by_login(login_code: str, ip: str = "") -> int:
    cleaned = normalize_login_code(login_code)
    if not cleaned:
        raise ValueError("Invalid login code")

    existing = list(db.t.users.rows_where("login_code=?", [cleaned], limit=1))
    if existing:
        user_id = int(existing[0]["id"])
        record_user_ip(user_id, ip)
        return user_id

    user = db.t.users.insert(created_at=_now_iso(), display_name=cleaned, login_code=cleaned)
    user_id = int(user["id"])
    record_user_ip(user_id, ip)
    return user_id


def list_profiles(limit: int = 100) -> list[dict[str, Any]]:
    rows = [row for row in db.t.users() if (row.get("login_code") or "").strip()]
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[:limit]


def _user_public_id(user_row: dict[str, Any]) -> str:
    login_code = str(user_row.get("login_code") or "").strip()
    if login_code:
        return login_code
    display_name = str(user_row.get("display_name") or "").strip()
    if display_name:
        return display_name
    return str(user_row.get("id") or "")


def _load_latest_feedback_by_message_user() -> dict[tuple[int, int], dict[str, Any]]:
    rows = list(db.t.feedback())
    rows.sort(key=lambda row: ((row.get("created_at") or ""), int(row.get("id") or 0)))
    latest: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            key = (int(row["message_id"]), int(row["user_id"]))
        except Exception:
            continue
        latest[key] = row
    return latest


def _parse_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_json_list(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except Exception:
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _interaction_sort_key(row: dict[str, Any]) -> tuple[str, int]:
    return (str(row.get("asked_at") or row.get("assistant_created_at") or ""), int(row.get("assistant_message_id") or 0))


def build_admin_interaction_rows() -> list[dict[str, Any]]:
    users_by_id = {int(row["id"]): row for row in db.t.users() if row.get("id") is not None}
    feedback_by_key = _load_latest_feedback_by_message_user()
    messages = list(db.t.messages())
    messages.sort(key=lambda row: ((row.get("chat_id") or 0), (row.get("created_at") or ""), int(row.get("id") or 0)))

    latest_user_by_chat: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for message in messages:
        try:
            chat_id = int(message["chat_id"])
        except Exception:
            continue
        role = str(message.get("role") or "")
        if role == "user":
            latest_user_by_chat[chat_id] = message
            continue
        if role != "assistant":
            continue

        chat_rows = list(db.t.chats.rows_where("id=?", [chat_id], limit=1))
        if not chat_rows:
            continue
        chat_row = chat_rows[0]
        owner_user_id = int(chat_row["user_id"])
        owner_row = users_by_id.get(owner_user_id, {"id": owner_user_id})
        user_message = latest_user_by_chat.get(chat_id)
        question = str((user_message or {}).get("content") or "").strip()
        question_created_at = (user_message or {}).get("created_at") or message.get("created_at")
        debug_payload = _parse_json_object(message.get("debug_json"))
        sources = _parse_json_list(message.get("sources_json"))
        debug_sources = debug_payload.get("sources")
        if isinstance(debug_sources, list):
            parsed_debug_sources = [item for item in debug_sources if isinstance(item, dict)]
            if parsed_debug_sources:
                sources = parsed_debug_sources

        feedback_row = feedback_by_key.get((int(message["id"]), owner_user_id))
        if feedback_row is None:
            fallback_candidates = [
                item
                for (msg_id, _feedback_user_id), item in feedback_by_key.items()
                if msg_id == int(message["id"])
            ]
            if fallback_candidates:
                fallback_candidates.sort(
                    key=lambda row: ((row.get("created_at") or ""), int(row.get("id") or 0)),
                    reverse=True,
                )
                feedback_row = fallback_candidates[0]

        query_effective = str(debug_payload.get("query_effective") or "").strip()
        query_rewritten = str(debug_payload.get("query_rewritten") or "").strip()
        query_rewrite = debug_payload.get("query_rewrite")
        if not isinstance(query_rewrite, dict):
            query_rewrite = {}

        rows.append(
            {
                "assistant_message_id": int(message["id"]),
                "chat_id": chat_id,
                "user_db_id": owner_user_id,
                "user_id": _user_public_id(owner_row),
                "question": question,
                "question_norm": str((user_message or {}).get("question_norm") or "").strip(),
                "asked_at": question_created_at,
                "assistant_created_at": message.get("created_at"),
                "answer": str(message.get("content") or ""),
                "query_effective": query_effective,
                "query_rewritten": query_rewritten,
                "rewrite_used": bool(query_rewrite.get("used")),
                "rewrite_reason": str(query_rewrite.get("reason") or "").strip(),
                "sources": sources,
                "source_count": len(sources),
                "rating": int(feedback_row.get("rating") or 0) if feedback_row else 0,
                "note": str(feedback_row.get("note") or "").strip() if feedback_row else "",
                "feedback_created_at": feedback_row.get("created_at") if feedback_row else None,
                "has_debug": bool(message.get("debug_json")),
                "app_version": str(message.get("app_version") or "").strip(),
            }
        )

    rows.sort(key=_interaction_sort_key, reverse=True)
    return rows


def filter_admin_interactions(
    *,
    range_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user_id: str | None = None,
    rating: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    start_dt, end_dt = _resolve_time_bounds(range_name=range_name, start=start, end=end)
    user_filter = normalize_login_code(user_id or "") or str(user_id or "").strip().upper()
    filtered: list[dict[str, Any]] = []
    for row in build_admin_interaction_rows():
        asked_at = _parse_iso_datetime(str(row.get("asked_at") or ""))
        if start_dt and (asked_at is None or asked_at < start_dt):
            continue
        if end_dt and (asked_at is None or asked_at > end_dt):
            continue
        if user_filter and str(row.get("user_id") or "").upper() != user_filter:
            continue
        if not _feedback_rating_matches(int(row.get("rating") or 0), rating):
            continue
        if not _matches_search(row, search):
            continue
        filtered.append(row)
    return filtered


def list_admin_interactions(
    *,
    range_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user_id: str | None = None,
    rating: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    rows = filter_admin_interactions(
        range_name=range_name,
        start=start,
        end=end,
        user_id=user_id,
        rating=rating,
        search=search,
    )
    paginated = _paginate_rows(
        rows,
        page=_coerce_page(page),
        page_size=_coerce_page_size(page_size),
    )
    return {
        "items": paginated["items"],
        "pagination": paginated["pagination"],
    }


def get_admin_interaction(message_id: int) -> dict[str, Any] | None:
    for row in build_admin_interaction_rows():
        if int(row.get("assistant_message_id") or 0) == int(message_id):
            return row
    return None


def list_admin_user_stats(
    *,
    range_name: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user_id_search: str | None = None,
    sort: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    interactions = filter_admin_interactions(
        range_name=range_name,
        start=start,
        end=end,
        search=None,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in interactions:
        user_id = str(row.get("user_id") or "").strip()
        if not user_id:
            continue
        item = grouped.setdefault(
            user_id,
            {
                "user_id": user_id,
                "question_count": 0,
                "interaction_count": 0,
                "positive_feedback_count": 0,
                "negative_feedback_count": 0,
                "rated_interaction_count": 0,
                "unrated_interaction_count": 0,
                "last_interaction_at": "",
            },
        )
        item["question_count"] += 1
        item["interaction_count"] += 1
        rating_value = int(row.get("rating") or 0)
        if rating_value == 1:
            item["positive_feedback_count"] += 1
            item["rated_interaction_count"] += 1
        elif rating_value == -1:
            item["negative_feedback_count"] += 1
            item["rated_interaction_count"] += 1
        else:
            item["unrated_interaction_count"] += 1
        asked_at = str(row.get("asked_at") or "")
        if asked_at > str(item.get("last_interaction_at") or ""):
            item["last_interaction_at"] = asked_at

    rows = list(grouped.values())
    search_value = str(user_id_search or "").strip().upper()
    if search_value:
        rows = [row for row in rows if search_value in str(row.get("user_id") or "").upper()]

    sort_key = str(sort or "last_interaction_at:desc").strip().lower()
    sort_field, _, sort_dir = sort_key.partition(":")
    reverse = sort_dir != "asc"
    allowed_sort_fields = {
        "user_id",
        "question_count",
        "interaction_count",
        "positive_feedback_count",
        "negative_feedback_count",
        "rated_interaction_count",
        "unrated_interaction_count",
        "last_interaction_at",
    }
    if sort_field not in allowed_sort_fields:
        sort_field = "last_interaction_at"
        reverse = True
    rows.sort(key=lambda row: (row.get(sort_field) or 0, row.get("user_id") or ""), reverse=reverse)

    summary = {
        "user_count": len(rows),
        "question_count": sum(int(row.get("question_count") or 0) for row in rows),
        "interaction_count": sum(int(row.get("interaction_count") or 0) for row in rows),
        "positive_feedback_count": sum(int(row.get("positive_feedback_count") or 0) for row in rows),
        "negative_feedback_count": sum(int(row.get("negative_feedback_count") or 0) for row in rows),
        "rated_interaction_count": sum(int(row.get("rated_interaction_count") or 0) for row in rows),
        "unrated_interaction_count": sum(int(row.get("unrated_interaction_count") or 0) for row in rows),
    }
    paginated = _paginate_rows(
        rows,
        page=_coerce_page(page),
        page_size=_coerce_page_size(page_size),
    )
    return {
        "summary": summary,
        "items": paginated["items"],
        "pagination": paginated["pagination"],
    }


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
    app_version: str | None = None,
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
        app_version=app_version,
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
        user_id = get_or_create_user_by_login("1234AB", "127.0.0.1")
        chat_id = create_chat(user_id=user_id, title="Check Chat")
        message_id = insert_message(
            chat_id=chat_id,
            role="user",
            content="hello world",
            question_norm=normalize_question("hello world"),
            app_version="0.0.0-test",
        )
        message = get_message(message_id)
        assert chat_belongs_to_user(chat_id, user_id)
        assert message is not None
        assert (db.t.users[user_id].get("login_code") or "") == "1234AB"
        assert list(db.t.user_ip_logs.rows_where("user_id=? AND ip=?", [user_id, "127.0.0.1"], limit=1))
        assert message.get("app_version") == "0.0.0-test"
        assert hash_question(normalize_question("hello world"))
        upsert_cache_good("hello world", "answer", [{"url": "https://example.com"}])
        assert get_cache_answer("hello world") is not None
    finally:
        db = _original_db  # type: ignore[assignment]
        _ = original_path

    print("Check Passed")
