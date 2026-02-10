from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastlite import database

try:
    from .site_config import SITES
except ImportError:
    import sys

    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    from backend.app.site_config import SITES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRAPER_DB_PATH = PROJECT_ROOT / "data" / "scraper.db"


def _normalize_path(path_value: str | Path) -> Path:
    return Path(path_value).expanduser().resolve()


def _is_special_sqlite_path(path_value: str | Path) -> bool:
    text = str(path_value).strip()
    return text == ":memory:" or text.startswith("file::memory:")


def _resolve_scraper_db_path(db_path: str | Path | None = None) -> str | Path:
    configured = db_path or os.getenv("HIERAG_SCRAPER_DB_PATH")
    if configured:
        if _is_special_sqlite_path(configured):
            return str(configured)
        return _normalize_path(configured)

    target = DEFAULT_SCRAPER_DB_PATH.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def get_scraper_db(db_path: str | Path | None = None):
    target = _resolve_scraper_db_path(db_path)
    return database(str(target))


def _ensure_extracts_pdf_column(db) -> None:
    cols = {row["name"] for row in db.q("PRAGMA table_info(extracts)")}
    if "pdf_id" not in cols:
        db.q("ALTER TABLE extracts ADD COLUMN pdf_id int")


def ensure_pipeline_schema(db) -> None:
    sites = db.t.sites
    if sites not in db.t:
        sites.create(
            id=int,
            root_url=str,
            selector=str,
            breadcrumb_selector=str,
            split_function=str,
            name=str,
            pk="id",
        )
        sites.create_index(["root_url"], unique=True)

    discovered_urls = db.t.discovered_urls
    if discovered_urls not in db.t:
        discovered_urls.create(
            id=int,
            site_id=int,
            url=str,
            kind=str,
            discovered_at=str,
            pk="id",
            foreign_keys=[("site_id", "sites")],
        )
        discovered_urls.create_index(["url"], unique=True)

    pages = db.t.pages
    if pages not in db.t:
        pages.create(
            id=int,
            site_id=int,
            url=str,
            html=str,
            content_hash=str,
            last_scraped=str,
            last_changed=str,
            pk="id",
            foreign_keys=[("site_id", "sites")],
        )
        pages.create_index(["url"], unique=True)

    extracts = db.t.extracts
    if extracts not in db.t:
        extracts.create(
            id=int,
            page_id=int,
            pdf_id=int,
            extract_index=int,
            text=str,
            pk="id",
            foreign_keys=[("page_id", "pages")],
        )

    chunks = db.t.chunks
    if chunks not in db.t:
        chunks.create(
            id=int,
            extract_id=int,
            chunk_index=int,
            text=str,
            pk="id",
            foreign_keys=[("extract_id", "extracts")],
        )

    embeddings = db.t.embeddings
    if embeddings not in db.t:
        embeddings.create(
            id=int,
            chunk_id=int,
            embedding=bytes,
            pk="id",
            foreign_keys=[("chunk_id", "chunks")],
        )

    pdfs = db.t.pdfs
    if pdfs not in db.t:
        pdfs.create(
            id=int,
            site_id=int,
            url=str,
            source_url=str,
            content_type=str,
            content_hash=str,
            bytes=bytes,
            pages=int,
            last_scraped=str,
            last_changed=str,
            pk="id",
            foreign_keys=[("site_id", "sites")],
        )
        pdfs.create_index(["url"], unique=True)
        pdfs.create_index(["content_hash"])

    _ensure_extracts_pdf_column(db)


def seed_sites(db, sites: list[dict[str, Any]] | None = None) -> None:
    site_rows = sites or SITES
    for site in site_rows:
        db.t.sites.upsert(**site)


def bootstrap_scraper_db(db_path: str | Path | None = None, *, seed: bool = True):
    db = get_scraper_db(db_path)
    ensure_pipeline_schema(db)
    if seed:
        seed_sites(db)
    return db


# %%
if __name__ == "__main__":
    test_db = bootstrap_scraper_db(":memory:")
    assert test_db.t.sites is not None
    assert len(list(test_db.t.sites())) >= 1
    print("Check Passed")
