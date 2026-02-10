from __future__ import annotations

from collections.abc import Generator
from typing import Any

try:
    from . import service
except ImportError:
    import sys
    from pathlib import Path

    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    from backend.app import service  # type: ignore[no-redef]


def get_db() -> Generator[Any, None, None]:
    service.create_db_and_tables()
    yield service.db


# %%
if __name__ == "__main__":
    from fastlite import database

    original_db = service.db
    service.db = database(":memory:")
    try:
        db = next(get_db())
        user = db.t.users.insert(created_at="now", display_name="deps-check")
        db.t.user_ips.insert(ip="127.0.0.1", user_id=user["id"], created_at="now")
        assert db.t.users[user["id"]] is not None
    finally:
        service.db = original_db

    print("Check Passed")
