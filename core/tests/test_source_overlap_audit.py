from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.source_overlap_audit import (
    audit_source_overlap,
    canonicalize_source_url,
    extract_source_urls_from_debug_json,
)


def test_canonicalize_source_url_dedupes_encoded_docs_variants():
    encoded = "https://connections/?docs=residential%2Fmyway%2Ftopic#tab-a"
    decoded = "https://connections/?docs=residential/myway/topic"
    assert canonicalize_source_url(encoded) == canonicalize_source_url(decoded)


def test_extract_source_urls_from_debug_json_prefers_canonical_and_dedupes():
    payload = json.dumps(
        {
            "sources": [
                {"url": "https://connections/?docs=residential%2Fmyway%2Ftopic"},
                {"url_canonical": "https://connections/?docs=residential/myway/topic"},
                {"url": "https://connections/?docs=residential/traditional/alpha"},
            ]
        }
    )
    urls = extract_source_urls_from_debug_json(payload)
    assert urls == [
        "https://connections/?docs=residential/myway/topic",
        "https://connections/?docs=residential/traditional/alpha",
    ]


def test_audit_source_overlap_reports_overlap_counts(tmp_path: Path):
    db_path = tmp_path / "app_runtime.db"
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
        "INSERT INTO chats(id, created_at, last_message_at) VALUES(1, '2026-02-17T00:00:00', '2026-02-17T00:03:00')"
    )

    first_debug = json.dumps(
        {
            "sources": [
                {"url": "https://connections/?docs=residential/myway/topic"},
                {"url": "https://connections/?docs=residential/traditional/a"},
            ]
        }
    )
    second_debug = json.dumps(
        {
            "sources": [
                {"url": "https://connections/?docs=residential%2Fmyway%2Ftopic"},
                {"url": "https://connections/?docs=residential/traditional/b"},
            ]
        }
    )

    conn.execute(
        "INSERT INTO messages(id, chat_id, role, created_at, cached_from, debug_json) VALUES(1, 1, 'assistant', '2026-02-17T00:00:01', NULL, ?)",
        [first_debug],
    )
    conn.execute(
        "INSERT INTO messages(id, chat_id, role, created_at, cached_from, debug_json) VALUES(2, 1, 'assistant', '2026-02-17T00:00:02', 123, ?)",
        [second_debug],
    )
    conn.commit()
    conn.close()

    rows = audit_source_overlap(db_path=str(db_path), chat_id=1, limit=10)
    assert len(rows) == 2
    assert rows[0]["source_count"] == 2
    assert rows[0]["overlap_prev_count"] == 0
    assert rows[1]["cache_hit"] is True
    assert rows[1]["source_count"] == 2
    assert rows[1]["overlap_prev_count"] == 1
    assert rows[1]["overlap_prev_ratio"] == 0.5


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
