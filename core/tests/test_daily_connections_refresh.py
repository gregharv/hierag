from __future__ import annotations

import core.daily_connections_refresh as refresh
from core.fastlite_db import bootstrap_scraper_db


def test_prune_missing_after_cli_default_is_one():
    args = refresh._build_arg_parser().parse_args([])
    assert args.prune_missing_after == 1


def _seed_single_url(db, *, url: str = "https://connections/?docs=test", missing_streak: int = 0) -> None:
    site_id = 2
    row = db.t.discovered_urls.insert(
        site_id=site_id,
        url=url,
        kind="html",
        discovered_at="now",
    )
    if missing_streak:
        db.t.discovered_urls.update({"id": row["id"], "consecutive_missing": missing_streak})

    page = db.t.pages.insert(
        site_id=site_id,
        url=url,
        html="<div>content</div>",
        content_hash="hash-1",
        last_scraped="now",
        last_changed="now",
    )
    extract = db.t.extracts.insert(page_id=page["id"], extract_index=0, text="extract")
    chunk = db.t.chunks.insert(extract_id=extract["id"], chunk_index=0, text="chunk")
    db.t.embeddings.insert(chunk_id=chunk["id"], embedding=b"123")


def test_prune_missing_url_after_threshold(monkeypatch):
    db = bootstrap_scraper_db(":memory:")
    target_url = "https://connections/?docs=remove-me"
    _seed_single_url(db, url=target_url, missing_streak=1)

    def fake_fetch_page(*args, **kwargs):
        return None, "http_404"

    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    summary = refresh.run_daily_connections_refresh(
        db,
        site_id=2,
        skip_crawl=True,
        scrape_only=True,
        refresh_cache=False,
        prune_missing=True,
        prune_missing_after=2,
        prune_status_codes=[404, 410],
    )

    assert summary["urls_pruned"] == 1
    assert summary["pruned_pages"] == 1
    assert summary["pruned_extracts"] == 1
    assert summary["pruned_chunks"] == 1
    assert summary["pruned_embeddings"] == 1
    assert summary["pruned_discovered_urls"] == 1
    assert list(db.t.pages.rows_where("url=?", [target_url], limit=1)) == []
    assert list(db.t.discovered_urls.rows_where("url=?", [target_url], limit=1)) == []


def test_non_missing_http_failure_does_not_prune(monkeypatch):
    db = bootstrap_scraper_db(":memory:")
    target_url = "https://connections/?docs=keep-me"
    _seed_single_url(db, url=target_url, missing_streak=5)

    def fake_fetch_page(*args, **kwargs):
        return None, "http_500"

    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    summary = refresh.run_daily_connections_refresh(
        db,
        site_id=2,
        skip_crawl=True,
        scrape_only=True,
        refresh_cache=False,
        prune_missing=True,
        prune_missing_after=2,
        prune_status_codes=[404, 410],
    )

    assert summary["urls_pruned"] == 0
    assert summary["urls_failed_http_other"] == 1
    page_rows = list(db.t.pages.rows_where("url=?", [target_url], limit=1))
    discovered_rows = list(db.t.discovered_urls.rows_where("url=?", [target_url], limit=1))
    assert len(page_rows) == 1
    assert len(discovered_rows) == 1
    assert int(discovered_rows[0].get("consecutive_missing") or 0) == 0


def test_successful_fetch_resets_missing_streak(monkeypatch):
    db = bootstrap_scraper_db(":memory:")
    target_url = "https://connections/?docs=reset-me"
    _seed_single_url(db, url=target_url, missing_streak=3)

    page = list(db.t.pages.rows_where("url=?", [target_url], limit=1))[0]

    def fake_fetch_page(*args, **kwargs):
        return int(page["id"]), "unchanged"

    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    summary = refresh.run_daily_connections_refresh(
        db,
        site_id=2,
        skip_crawl=True,
        scrape_only=True,
        refresh_cache=False,
        prune_missing=True,
        prune_missing_after=2,
        prune_status_codes=[404, 410],
    )

    assert summary["urls_unchanged"] == 1
    discovered_rows = list(db.t.discovered_urls.rows_where("url=?", [target_url], limit=1))
    assert len(discovered_rows) == 1
    assert int(discovered_rows[0].get("consecutive_missing") or 0) == 0
    assert discovered_rows[0].get("last_fetch_status") == "ok"


def test_keyboard_interrupt_during_fetch_returns_partial_summary(monkeypatch):
    db = bootstrap_scraper_db(":memory:")
    target_url = "https://connections/?docs=interrupt-me"
    _seed_single_url(db, url=target_url, missing_streak=0)

    def fake_fetch_page(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    summary = refresh.run_daily_connections_refresh(
        db,
        site_id=2,
        skip_crawl=True,
        scrape_only=False,
        refresh_cache=True,
        prune_missing=False,
    )

    assert summary["interrupted"] is True
    assert summary["interrupted_at_url"] == target_url
    assert summary["fetched_urls_completed"] == 0
    assert summary["cache_refreshed_local_process"] is False


def test_refresh_with_crawl_discovers_and_adds_new_page(monkeypatch):
    db = bootstrap_scraper_db(":memory:")
    target_url = "https://connections/?docs=fresh-page"

    def fake_crawl_site(db_obj, site_id, max_pages=3000, delay=0.1):
        db_obj.t.discovered_urls.insert(
            site_id=site_id,
            url=target_url,
            kind="html",
            discovered_at="now",
        )
        return 1

    def fake_fetch_page(db_obj, site_id, url, **kwargs):
        assert site_id == 2
        assert url == target_url
        page = db_obj.t.pages.insert(
            site_id=site_id,
            url=url,
            html="<div>new page</div>",
            content_hash="hash-new",
            last_scraped="now",
            last_changed="now",
        )
        return int(page["id"]), "new"

    monkeypatch.setattr(refresh, "crawl_site", fake_crawl_site)
    monkeypatch.setattr(refresh, "fetch_page", fake_fetch_page)

    summary = refresh.run_daily_connections_refresh(
        db,
        site_id=2,
        skip_crawl=False,
        scrape_only=True,
        refresh_cache=False,
        prune_missing=True,
        prune_missing_after=3,
        prune_status_codes=[404, 410],
    )

    assert summary["crawl_visited"] == 1
    assert summary["discovered_before"] == 0
    assert summary["discovered_new"] == 1
    assert summary["target_urls"] == 1
    assert summary["urls_new"] == 1
    assert summary["pages_added"] == 1
    page_rows = list(db.t.pages.rows_where("url=?", [target_url], limit=1))
    assert len(page_rows) == 1


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
