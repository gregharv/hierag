from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
DEFAULT_DB = ROOT / "data" / "app_runtime.db"


def utc_cutoff(raw: str) -> str:
    return datetime.combine(date.fromisoformat(raw), time.min, tzinfo=ET).astimezone(timezone.utc).isoformat()


def rewritten(raw: str | None) -> str:
    try:
        return json.loads(raw or "{}").get("query_rewritten", "")
    except Exception:
        return ""


def main() -> int:
    p = argparse.ArgumentParser(description="Export interactions to Excel.")
    p.add_argument("--since", default="2026-04-06", help="Include interactions on/after this date (YYYY-MM-DD).")
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB, help="Path to the app database.")
    p.add_argument("--output", type=Path, default=None, help="Output .xlsx path.")
    a = p.parse_args()

    db = a.db_path.expanduser().resolve()
    out = (a.output or ROOT / "data" / "exports" / f"interactions_since_{a.since}.xlsx").expanduser().resolve()
    since = utc_cutoff(a.since)

    sql = """
    WITH q AS (
      SELECT
        a.id AS assistant_message_id,
        COALESCE(NULLIF(u.display_name, ''), CAST(c.user_id AS TEXT)) AS User,
        COALESCE((SELECT m.content FROM messages m
                  WHERE m.chat_id = a.chat_id AND m.role = 'user'
                    AND (m.created_at < a.created_at OR (m.created_at = a.created_at AND m.id < a.id))
                  ORDER BY m.created_at DESC, m.id DESC LIMIT 1), '') AS Question,
        COALESCE((SELECT m.created_at FROM messages m
                  WHERE m.chat_id = a.chat_id AND m.role = 'user'
                    AND (m.created_at < a.created_at OR (m.created_at = a.created_at AND m.id < a.id))
                  ORDER BY m.created_at DESC, m.id DESC LIMIT 1), a.created_at) AS Datetime,
        a.content AS "LLM Answer",
        a.debug_json,
        COALESCE((SELECT f.rating FROM feedback f
                  WHERE f.message_id = a.id AND f.user_id = c.user_id
                  ORDER BY f.created_at DESC, f.id DESC LIMIT 1), 0) AS Rating
      FROM messages a
      JOIN chats c ON c.id = a.chat_id
      LEFT JOIN users u ON u.id = c.user_id
      WHERE a.role = 'assistant'
    )
    SELECT * FROM q WHERE Datetime >= ? ORDER BY Datetime, assistant_message_id
    """

    with sqlite3.connect(db) as con:
        df = pd.read_sql_query(sql, con, params=[since])

    df["Rewritten Query"] = df["debug_json"].map(rewritten)
    df["Rating"] = df["Rating"].fillna(0).astype(int)
    df[["User", "Datetime", "Question", "Rewritten Query", "Rating", "LLM Answer"]].to_excel(out, index=False, engine="openpyxl")
    print(f"Wrote {len(df)} interactions to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
