from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from fastlite import database


class UserRow(TypedDict):
    id: int
    created_at: str
    display_name: str | None


class UserIPRow(TypedDict):
    ip: str
    user_id: int
    created_at: str


class ChatRow(TypedDict):
    id: int
    user_id: int
    created_at: str
    last_message_at: NotRequired[str | None]
    title: NotRequired[str | None]


class CacheEntryRow(TypedDict):
    id: int
    question_norm: str
    question_hash: str
    answer_text: str
    sources_json: NotRequired[str | None]
    good_count: int
    bad_count: int
    created_at: str
    updated_at: str
    last_used_at: NotRequired[str | None]


class MessageRow(TypedDict):
    id: int
    chat_id: int
    role: str
    content: str
    sources_json: NotRequired[str | None]
    debug_json: NotRequired[str | None]
    created_at: str
    stream_id: NotRequired[str | None]
    cached_from: NotRequired[int | None]
    question_norm: NotRequired[str | None]


class FeedbackRow(TypedDict):
    id: int
    message_id: int
    user_id: int
    rating: int
    note: NotRequired[str | None]
    created_at: str


APP_TABLE_DEFS: dict[str, dict[str, Any]] = {
    "users": {
        "columns": {"id": int, "created_at": str, "display_name": str},
        "pk": "id",
    },
    "user_ips": {
        "columns": {"ip": str, "user_id": int, "created_at": str},
        "pk": "ip",
        "foreign_keys": [("user_id", "users")],
    },
    "chats": {
        "columns": {
            "id": int,
            "user_id": int,
            "created_at": str,
            "last_message_at": str,
            "title": str,
        },
        "pk": "id",
        "foreign_keys": [("user_id", "users")],
        "indexes": [{"columns": ["user_id"]}],
    },
    "cache_entries": {
        "columns": {
            "id": int,
            "question_norm": str,
            "question_hash": str,
            "answer_text": str,
            "sources_json": str,
            "good_count": int,
            "bad_count": int,
            "created_at": str,
            "updated_at": str,
            "last_used_at": str,
        },
        "pk": "id",
        "indexes": [
            {"columns": ["question_hash"], "unique": True},
            {"columns": ["question_norm"], "unique": True},
            {"columns": ["updated_at"]},
        ],
    },
    "messages": {
        "columns": {
            "id": int,
            "chat_id": int,
            "role": str,
            "content": str,
            "sources_json": str,
            "debug_json": str,
            "created_at": str,
            "stream_id": str,
            "cached_from": int,
            "question_norm": str,
        },
        "pk": "id",
        "foreign_keys": [("chat_id", "chats"), ("cached_from", "cache_entries")],
        "indexes": [{"columns": ["chat_id", "created_at"]}, {"columns": ["stream_id"]}],
    },
    "feedback": {
        "columns": {
            "id": int,
            "message_id": int,
            "user_id": int,
            "rating": int,
            "note": str,
            "created_at": str,
        },
        "pk": "id",
        "foreign_keys": [("message_id", "messages"), ("user_id", "users")],
        "indexes": [{"columns": ["message_id"]}, {"columns": ["user_id", "created_at"]}],
    },
}


def ensure_app_schema(db) -> None:
    existing_table_names = {
        row["name"]
        for row in db.q("SELECT name FROM sqlite_master WHERE type='table';")
    }
    for table_name, definition in APP_TABLE_DEFS.items():
        table = getattr(db.t, table_name)
        if table_name in existing_table_names:
            continue

        create_args: dict[str, Any] = {"pk": definition["pk"]}
        if definition.get("foreign_keys"):
            create_args["foreign_keys"] = definition["foreign_keys"]

        table.create(**definition["columns"], **create_args)
        for index in definition.get("indexes", []):
            table.create_index(index["columns"], unique=bool(index.get("unique", False)))
        existing_table_names.add(table_name)


# %%
if __name__ == "__main__":
    test_db = database(":memory:")
    ensure_app_schema(test_db)

    user = test_db.t.users.insert(created_at="now", display_name="check-user")
    test_db.t.user_ips.insert(ip="127.0.0.1", user_id=user["id"], created_at="now")
    chat = test_db.t.chats.insert(user_id=user["id"], created_at="now", title="Check Chat")
    cache = test_db.t.cache_entries.insert(
        question_norm="hello",
        question_hash="hash",
        answer_text="world",
        good_count=1,
        bad_count=0,
        created_at="now",
        updated_at="now",
    )
    message = test_db.t.messages.insert(
        chat_id=chat["id"],
        role="user",
        content="hello",
        created_at="now",
        question_norm="hello",
        cached_from=cache["id"],
    )
    test_db.t.feedback.insert(
        message_id=message["id"],
        user_id=user["id"],
        rating=1,
        note="ok",
        created_at="now",
    )

    assert test_db.t.users[user["id"]] is not None
    assert test_db.t.messages[message["id"]] is not None
    print("Check Passed")
