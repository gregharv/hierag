from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_DB_PATH = PROJECT_ROOT / "data" / "app_runtime.db"


def canonicalize_source_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
    except Exception:
        return value

    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = unquote(parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"

    query_items = parse_qs(parsed.query, keep_blank_values=False)
    docs_values = query_items.get("docs") or []
    docs_value = next((unquote(str(v)).strip().strip("/") for v in docs_values if str(v).strip()), "")
    if docs_value:
        docs_norm = quote(docs_value, safe="/-._~")
        return f"{scheme}://{netloc}{path}?docs={docs_norm}"

    pairs: list[tuple[str, str]] = []
    for key in sorted(query_items.keys()):
        norm_key = unquote(str(key)).strip()
        if not norm_key:
            continue
        for val in query_items.get(key) or []:
            pairs.append((norm_key, unquote(str(val)).strip()))
    query = urlencode(pairs, doseq=True, safe="/-._~")
    if query:
        return f"{scheme}://{netloc}{path}?{query}"
    return f"{scheme}://{netloc}{path}"


def extract_source_urls_from_debug_json(debug_json: str | None) -> list[str]:
    if not debug_json:
        return []
    try:
        payload = json.loads(debug_json)
    except Exception:
        return []

    sources = payload.get("sources") or []
    urls: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        candidate = str(source.get("url_canonical") or source.get("url") or "").strip()
        if not candidate:
            continue
        canonical = canonicalize_source_url(candidate)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
    return urls


def _resolve_db_path(db_path: str | None = None) -> Path:
    if db_path:
        return Path(db_path).expanduser().resolve()
    return DEFAULT_APP_DB_PATH.resolve()


def _connect_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _chat_ids_for_audit(conn: sqlite3.Connection, *, chat_id: int | None, recent_chats: int | None) -> list[int]:
    cur = conn.cursor()
    if chat_id is not None:
        return [int(chat_id)]

    if recent_chats is not None:
        rows = cur.execute(
            """
            SELECT id
            FROM chats
            ORDER BY COALESCE(last_message_at, created_at, '') DESC
            LIMIT ?
            """,
            [int(recent_chats)],
        ).fetchall()
        return [int(row["id"]) for row in rows]

    rows = cur.execute(
        """
        SELECT chat_id
        FROM messages
        WHERE role='assistant'
        GROUP BY chat_id
        ORDER BY MAX(COALESCE(created_at, '')) DESC
        """
    ).fetchall()
    return [int(row["chat_id"]) for row in rows]


def _assistant_rows_for_chat(conn: sqlite3.Connection, *, chat_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, chat_id, created_at, cached_from, debug_json
        FROM messages
        WHERE chat_id=? AND role='assistant'
        ORDER BY COALESCE(created_at, '') DESC, id DESC
        LIMIT ?
        """,
        [int(chat_id), int(limit)],
    ).fetchall()

    ordered = list(reversed(rows))
    items: list[dict[str, Any]] = []
    for row in ordered:
        items.append(
            {
                "message_id": int(row["id"]),
                "chat_id": int(row["chat_id"]),
                "created_at": row["created_at"],
                "cache_hit": bool(row["cached_from"] is not None),
                "debug_json": row["debug_json"],
            }
        )
    return items


def _compute_overlap_for_chat(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev_urls: set[str] | None = None
    output: list[dict[str, Any]] = []
    for row in rows:
        urls = extract_source_urls_from_debug_json(row.get("debug_json"))
        current_urls = set(urls)
        overlap_prev_count = len(current_urls & prev_urls) if prev_urls is not None else 0
        overlap_prev_ratio = (overlap_prev_count / len(current_urls)) if current_urls else 0.0
        output.append(
            {
                "chat_id": int(row["chat_id"]),
                "message_id": int(row["message_id"]),
                "created_at": row.get("created_at"),
                "cache_hit": bool(row.get("cache_hit")),
                "source_count": len(current_urls),
                "overlap_prev_count": overlap_prev_count,
                "overlap_prev_ratio": round(float(overlap_prev_ratio), 4),
                "source_urls": urls,
            }
        )
        prev_urls = current_urls
    return output


def audit_source_overlap(
    *,
    db_path: str | None = None,
    chat_id: int | None = None,
    recent_chats: int | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    path = _resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"App DB not found: {path}")

    conn = _connect_sqlite(path)
    try:
        target_chat_ids = _chat_ids_for_audit(conn, chat_id=chat_id, recent_chats=recent_chats)
        results: list[dict[str, Any]] = []
        for cid in target_chat_ids:
            rows = _assistant_rows_for_chat(conn, chat_id=cid, limit=limit)
            results.extend(_compute_overlap_for_chat(rows))
        return results
    finally:
        conn.close()


def _print_human(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No assistant messages found.")
        return

    by_chat: dict[int, list[dict[str, Any]]] = {}
    for item in results:
        by_chat.setdefault(int(item["chat_id"]), []).append(item)

    for chat_id in sorted(by_chat.keys(), reverse=True):
        print(f"\nchat_id={chat_id}")
        print("message_id | created_at                   | cache_hit | sources | overlap_prev | overlap_ratio")
        print("-----------+------------------------------+-----------+---------+--------------+--------------")
        for item in by_chat[chat_id]:
            created_at = str(item.get("created_at") or "")[:28]
            print(
                f"{int(item['message_id']):>10} | "
                f"{created_at:<28} | "
                f"{str(bool(item['cache_hit'])):<9} | "
                f"{int(item['source_count']):>7} | "
                f"{int(item['overlap_prev_count']):>12} | "
                f"{float(item['overlap_prev_ratio']):>12.4f}"
            )
            for url in item.get("source_urls") or []:
                print(f"  - {url}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit assistant source overlap across chat turns.")
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--recent-chats", type=int, default=None)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


def _run_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "app_runtime.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE chats (id INTEGER PRIMARY KEY, created_at TEXT, last_message_at TEXT)"
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                role TEXT,
                created_at TEXT,
                cached_from INTEGER,
                debug_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO chats(id, created_at, last_message_at) VALUES(1, '2026-02-17T00:00:00', '2026-02-17T00:02:00')"
        )

        debug_a = json.dumps(
            {
                "sources": [
                    {"url": "https://connections/?docs=residential%2Fmyway%2Ftopic#tab"},
                    {"url": "https://connections/?docs=residential/traditional/alpha"},
                ]
            }
        )
        debug_b = json.dumps(
            {
                "sources": [
                    {"url_canonical": "https://connections/?docs=residential/myway/topic"},
                    {"url": "https://connections/?docs=residential/traditional/beta"},
                ]
            }
        )
        conn.execute(
            "INSERT INTO messages(id, chat_id, role, created_at, cached_from, debug_json) VALUES(1, 1, 'assistant', '2026-02-17T00:00:01', NULL, ?)",
            [debug_a],
        )
        conn.execute(
            "INSERT INTO messages(id, chat_id, role, created_at, cached_from, debug_json) VALUES(2, 1, 'assistant', '2026-02-17T00:00:02', NULL, ?)",
            [debug_b],
        )
        conn.commit()
        conn.close()

        results = audit_source_overlap(db_path=str(db_path), chat_id=1, limit=10)
        assert len(results) == 2
        assert results[0]["source_count"] == 2
        assert results[1]["source_count"] == 2
        assert results[1]["overlap_prev_count"] == 1
        assert results[1]["overlap_prev_ratio"] == 0.5
        print("Check Passed")


# %%
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    if args.check:
        _run_check()
        raise SystemExit(0)

    if args.limit <= 0:
        raise SystemExit("--limit must be > 0")

    audit_rows = audit_source_overlap(
        db_path=args.db_path,
        chat_id=args.chat_id,
        recent_chats=args.recent_chats,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(audit_rows, ensure_ascii=True, indent=2))
    else:
        _print_human(audit_rows)
