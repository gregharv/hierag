from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "app_runtime.db"
DEFAULT_EXPORT_DIR = ROOT / "data" / "exports"

COLUMNS = [
    "User",
    "Asked At",
    "Feedback At",
    "Question",
    "Effective Query",
    "Rewritten Query",
    "Rating",
    "Feedback Note",
    "LLM Answer",
    "Source Count",
    "Sources",
    "Assistant Message ID",
    "Chat ID",
    "App Version",
]


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_EXPORT_DIR / f"negative_ratings_30d_{stamp}.xlsx"


def _format_sources(sources: Any) -> str:
    if not isinstance(sources, list):
        return ""

    formatted: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        title = str(source.get("title") or source.get("heading") or "").strip()
        url = str(source.get("url") or source.get("url_canonical") or "").strip()
        if title and url:
            formatted.append(f"{title} - {url}")
        elif url:
            formatted.append(url)
        elif title:
            formatted.append(title)
        else:
            formatted.append(json.dumps(source, ensure_ascii=False, sort_keys=True))
    return "\n".join(formatted)


def _row_to_export(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "User": row.get("user_id") or "",
        "Asked At": row.get("asked_at") or "",
        "Feedback At": row.get("feedback_created_at") or "",
        "Question": row.get("question") or "",
        "Effective Query": row.get("query_effective") or "",
        "Rewritten Query": row.get("query_rewritten") or "",
        "Rating": int(row.get("rating") or 0),
        "Feedback Note": row.get("note") or "",
        "LLM Answer": row.get("answer") or "",
        "Source Count": int(row.get("source_count") or 0),
        "Sources": _format_sources(row.get("sources")),
        "Assistant Message ID": row.get("assistant_message_id") or "",
        "Chat ID": row.get("chat_id") or "",
        "App Version": row.get("app_version") or "",
    }


def _write_export(df: pd.DataFrame, output: Path) -> Path:
    suffix = output.suffix.lower()
    if suffix == "":
        output = output.with_suffix(".xlsx")
        suffix = output.suffix.lower()

    output.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        df.to_csv(output, index=False)
        return output
    if suffix == ".xlsx":
        df.to_excel(output, index=False, engine="openpyxl")
        return output
    raise ValueError("Output must end in .xlsx or .csv")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export negative-rated interactions from the same rolling 30d range "
            "used by the admin Interaction Review."
        )
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB, help="Path to the app runtime SQLite database.")
    parser.add_argument("--output", type=Path, default=None, help="Output .xlsx or .csv path.")
    args = parser.parse_args()

    db_path = args.db_path.expanduser().resolve()
    output = (args.output or _default_output_path()).expanduser().resolve()

    # core.service reads HIERAG_APP_DB_PATH at import time, so set it before importing.
    os.environ["HIERAG_APP_DB_PATH"] = str(db_path)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from core import service  # noqa: PLC0415

    rows = service.filter_admin_interactions(range_name="30d", rating="negative")
    df = pd.DataFrame([_row_to_export(row) for row in rows], columns=COLUMNS)
    output = _write_export(df, output)

    print(f"Wrote {len(df)} negative-rated interactions from the rolling 30d range to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
