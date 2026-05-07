from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import service
    from .crawl import canonicalize_internal_url
    from .daily_connections_refresh import _prune_missing_url, run_daily_connections_refresh
    from .fastlite_db import (
        _is_special_sqlite_path,
        _resolve_scraper_db_path,
        bootstrap_scraper_db,
    )
    from .llmapi_flow import stream_answer_with_context
    from .llmapi_retrieval import refresh_retrieval_cache
    from .site_config import get_crawl_url_policy
except ImportError:
    import sys

    _project_root = Path(__file__).resolve().parents[1]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    from core import service  # type: ignore[no-redef]
    from core.crawl import canonicalize_internal_url  # type: ignore[no-redef]
    from core.daily_connections_refresh import _prune_missing_url, run_daily_connections_refresh  # type: ignore[no-redef]
    from core.fastlite_db import (  # type: ignore[no-redef]
        _is_special_sqlite_path,
        _resolve_scraper_db_path,
        bootstrap_scraper_db,
    )
    from core.llmapi_flow import stream_answer_with_context  # type: ignore[no-redef]
    from core.llmapi_retrieval import refresh_retrieval_cache  # type: ignore[no-redef]
    from core.site_config import get_crawl_url_policy  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SANDBOX_ROOT = PROJECT_ROOT / "data" / "source_sandboxes"
_PROPOSAL_LOCKS: dict[int, threading.Lock] = {}
_PROPOSAL_LOCKS_GUARD = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sandbox_root() -> Path:
    configured = os.getenv("HIERAG_SOURCE_SANDBOX_DIR")
    root = Path(configured).expanduser().resolve() if configured else DEFAULT_SANDBOX_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _proposal_lock(proposal_id: int) -> threading.Lock:
    with _PROPOSAL_LOCKS_GUARD:
        return _PROPOSAL_LOCKS.setdefault(int(proposal_id), threading.Lock())


def _live_scraper_db_path() -> Path:
    resolved = _resolve_scraper_db_path()
    if _is_special_sqlite_path(resolved):
        raise ValueError("Source proposals require a file-backed scraper database")
    return Path(resolved).expanduser().resolve()


def _ensure_live_scraper_db() -> Path:
    path = _live_scraper_db_path()
    db = bootstrap_scraper_db(path, seed=True)
    close = getattr(db, "close", None)
    if callable(close):
        close()
    return path


def _copy_sqlite_database(source_path: Path, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{dest_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _parse_json(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _proposal_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["id"] = int(item.get("id") or 0)
    item["url_count"] = len(list(service.db.t.source_proposal_urls.rows_where("proposal_id=?", [item["id"]])))
    item["last_refresh_summary"] = _parse_json(item.get("last_refresh_summary_json"), {})
    return item


def get_source_proposal(proposal_id: int) -> dict[str, Any] | None:
    rows = list(service.db.t.source_proposals.rows_where("id=?", [int(proposal_id)], limit=1))
    return _proposal_row(rows[0]) if rows else None


def list_source_proposals(limit: int = 50) -> list[dict[str, Any]]:
    rows = list(service.db.t.source_proposals())
    rows.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)), reverse=True)
    return [_proposal_row(row) for row in rows[: max(1, int(limit or 50))]]


def list_source_proposal_urls(proposal_id: int) -> list[dict[str, Any]]:
    rows = list(service.db.t.source_proposal_urls.rows_where("proposal_id=?", [int(proposal_id)]))
    rows.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)), reverse=True)
    return rows


def _effective_added_source_urls(proposal_id: int, db, site_id: int) -> list[str]:
    changes = list(service.db.t.source_proposal_urls.rows_where("proposal_id=?", [int(proposal_id)]))
    changes.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)))
    active: dict[str, None] = {}
    for change in changes:
        action = str(change.get("action") or "").strip().lower()
        canonical = _canonicalize_for_site(db, str(change.get("url") or ""), site_id)
        if action == "add":
            active[canonical] = None
        elif action == "remove":
            active.pop(canonical, None)
    return list(active.keys())


def create_source_proposal(name: str, created_by: str) -> dict[str, Any]:
    service.create_db_and_tables()
    cleaned_name = " ".join(str(name or "").split()).strip() or "Source test set"
    now = _now_iso()
    row = service.db.t.source_proposals.insert(
        name=cleaned_name[:120],
        status="creating",
        created_by=str(created_by or "").strip(),
        created_at=now,
        updated_at=now,
        sandbox_db_path="",
        base_live_db_path="",
        last_refresh_started_at="",
        last_refresh_finished_at="",
        last_refresh_summary_json="",
        error="",
    )
    proposal_id = int(row["id"])
    sandbox_path = _sandbox_root() / f"proposal_{proposal_id}" / "scraper.db"
    try:
        live_path = _ensure_live_scraper_db()
        _copy_sqlite_database(live_path, sandbox_path)
        sandbox_db = bootstrap_scraper_db(sandbox_path, seed=True)
        close = getattr(sandbox_db, "close", None)
        if callable(close):
            close()
        service.db.t.source_proposals.update(
            {
                "id": proposal_id,
                "status": "draft",
                "sandbox_db_path": str(sandbox_path),
                "base_live_db_path": str(live_path),
                "updated_at": _now_iso(),
                "error": "",
            }
        )
    except Exception as exc:
        service.db.t.source_proposals.update(
            {"id": proposal_id, "status": "failed", "updated_at": _now_iso(), "error": str(exc)}
        )
        raise
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise RuntimeError("Created source proposal could not be loaded")
    return proposal


def _open_sandbox_db(proposal: dict[str, Any]):
    path = Path(str(proposal.get("sandbox_db_path") or "")).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Sandbox database not found: {path}")
    return bootstrap_scraper_db(path, seed=True)


def _canonicalize_for_site(db, url: str, site_id: int) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        raise ValueError("URL is required")
    site = db.t.sites[int(site_id)]
    if not site:
        raise ValueError(f"No site with id {site_id}")
    root_url = site.get("root_url") or ""
    policy = get_crawl_url_policy(int(site_id))
    canonical = canonicalize_internal_url(
        cleaned,
        cleaned,
        root_url,
        policy=policy,
        allow_root=bool(policy.get("allow_root_seed", False)),
    )
    if not canonical:
        raise ValueError("URL is outside the allowed source policy for this site")
    return canonical


def add_source_url(proposal_id: int, url: str, created_by: str, *, site_id: int = 2) -> dict[str, Any]:
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise KeyError("Source proposal not found")
    with _proposal_lock(int(proposal_id)):
        db = _open_sandbox_db(proposal)
        canonical = _canonicalize_for_site(db, url, site_id)
        existing = list(db.t.discovered_urls.rows_where("url=?", [canonical], limit=1))
        if existing:
            db.t.discovered_urls.update({"id": existing[0]["id"], "site_id": int(site_id), "kind": "html"})
        else:
            db.t.discovered_urls.insert(
                site_id=int(site_id),
                url=canonical,
                kind="html",
                discovered_at=_now_iso(),
                consecutive_missing=0,
                last_fetch_status="",
                last_fetch_error="",
                last_failed_at="",
            )
        change = service.db.t.source_proposal_urls.insert(
            proposal_id=int(proposal_id),
            url=canonical,
            action="add",
            created_by=str(created_by or "").strip(),
            created_at=_now_iso(),
        )
        service.db.t.source_proposals.update({"id": int(proposal_id), "status": "draft", "updated_at": _now_iso(), "error": ""})
        return dict(change)


def remove_source_url(proposal_id: int, url: str, created_by: str, *, site_id: int = 2) -> dict[str, Any]:
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise KeyError("Source proposal not found")
    with _proposal_lock(int(proposal_id)):
        db = _open_sandbox_db(proposal)
        canonical = _canonicalize_for_site(db, url, site_id)
        _prune_missing_url(db, canonical)
        change = service.db.t.source_proposal_urls.insert(
            proposal_id=int(proposal_id),
            url=canonical,
            action="remove",
            created_by=str(created_by or "").strip(),
            created_at=_now_iso(),
        )
        service.db.t.source_proposals.update({"id": int(proposal_id), "status": "draft", "updated_at": _now_iso(), "error": ""})
        return dict(change)


def refresh_source_proposal(
    proposal_id: int,
    *,
    site_id: int = 2,
    max_pages: int = 3000,
    crawl_delay: float = 0.1,
    fetch_delay: float = 0.0,
    batch_size: int = 64,
    prune_missing: bool = True,
    prune_missing_after: int = 1,
) -> dict[str, Any]:
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise KeyError("Source proposal not found")
    lock = _proposal_lock(int(proposal_id))
    if not lock.acquire(blocking=False):
        raise RuntimeError("Source proposal refresh is already running")
    try:
        started = _now_iso()
        service.db.t.source_proposals.update(
            {
                "id": int(proposal_id),
                "status": "refresh_running",
                "last_refresh_started_at": started,
                "updated_at": started,
                "error": "",
            }
        )
        db = _open_sandbox_db(proposal)
        added_urls = _effective_added_source_urls(int(proposal_id), db, int(site_id))
        summary = run_daily_connections_refresh(
            db,
            site_id=int(site_id),
            max_pages=int(max_pages),
            crawl_delay=float(crawl_delay),
            fetch_delay=float(fetch_delay),
            batch_size=int(batch_size),
            skip_crawl=True,
            target_urls=added_urls,
            refresh_cache=False,
            prune_missing=bool(prune_missing),
            prune_missing_after=int(prune_missing_after),
        )
        finished = _now_iso()
        service.db.t.source_proposals.update(
            {
                "id": int(proposal_id),
                "status": "ready_for_review",
                "last_refresh_finished_at": finished,
                "last_refresh_summary_json": json.dumps(summary, ensure_ascii=True),
                "updated_at": finished,
                "error": "",
            }
        )
        return summary
    except Exception as exc:
        service.db.t.source_proposals.update(
            {"id": int(proposal_id), "status": "failed", "updated_at": _now_iso(), "error": str(exc)}
        )
        raise
    finally:
        lock.release()


def queue_source_proposal_refresh(proposal_id: int) -> None:
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise KeyError("Source proposal not found")
    service.db.t.source_proposals.update(
        {"id": int(proposal_id), "status": "refresh_queued", "updated_at": _now_iso(), "error": ""}
    )


def _collect_query_result(db, query: str) -> dict[str, Any]:
    text_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    debug: dict[str, Any] = {}
    errors: list[str] = []
    for event in stream_answer_with_context(
        db,
        query,
        top_k=10,
        max_extracts=6,
        history=None,
        use_answer_cache=False,
    ):
        etype = event.get("type")
        if etype == "delta":
            text_parts.append(str(event.get("text") or ""))
        elif etype == "sources":
            sources = [item for item in event.get("sources", []) if isinstance(item, dict)]
        elif etype == "debug":
            payload = event.get("debug")
            debug = payload if isinstance(payload, dict) else {}
        elif etype == "error":
            errors.append(str(event.get("error") or "Unknown error"))
    return {
        "answer": "".join(text_parts).strip(),
        "sources": sources,
        "debug": debug,
        "errors": errors,
    }


def test_source_proposal_query(proposal_id: int, query: str, created_by: str, *, compare_to_live: bool = True) -> dict[str, Any]:
    proposal = get_source_proposal(proposal_id)
    if proposal is None:
        raise KeyError("Source proposal not found")
    cleaned_query = " ".join(str(query or "").split()).strip()
    if not cleaned_query:
        raise ValueError("Query is required")

    sandbox_db = _open_sandbox_db(proposal)
    refresh_retrieval_cache(sandbox_db)
    sandbox_result = _collect_query_result(sandbox_db, cleaned_query)

    live_result: dict[str, Any] | None = None
    if compare_to_live:
        live_db = bootstrap_scraper_db(seed=False)
        refresh_retrieval_cache(live_db)
        live_result = _collect_query_result(live_db, cleaned_query)

    row = service.db.t.source_proposal_test_queries.insert(
        proposal_id=int(proposal_id),
        query=cleaned_query,
        live_answer=str((live_result or {}).get("answer") or ""),
        sandbox_answer=str(sandbox_result.get("answer") or ""),
        live_sources_json=json.dumps((live_result or {}).get("sources") or [], ensure_ascii=True),
        sandbox_sources_json=json.dumps(sandbox_result.get("sources") or [], ensure_ascii=True),
        created_by=str(created_by or "").strip(),
        created_at=_now_iso(),
    )
    return {
        "id": int(row["id"]),
        "proposal_id": int(proposal_id),
        "query": cleaned_query,
        "live": live_result,
        "sandbox": sandbox_result,
    }


# %%
if __name__ == "__main__":
    service.create_db_and_tables()
    root = _sandbox_root()
    assert root.exists()
    print("Check Passed")
