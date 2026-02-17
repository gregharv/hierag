from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from .crawl import canonicalize_internal_url, crawl_site
    from .embed import generate_embeddings_for_chunk_ids
    from .fastlite_db import bootstrap_scraper_db
    from .parse_content import process_pages_to_extracts_and_chunks
    from .scrape import fetch_page
    from .site_config import get_crawl_url_policy
except ImportError:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.crawl import canonicalize_internal_url, crawl_site
    from core.embed import generate_embeddings_for_chunk_ids
    from core.fastlite_db import bootstrap_scraper_db
    from core.parse_content import process_pages_to_extracts_and_chunks
    from core.scrape import fetch_page
    from core.site_config import get_crawl_url_policy


def _target_discovered_urls(
    db,
    site_id: int,
    *,
    html_only: bool = True,
    limit_urls: int | None = None,
) -> list[str]:
    rows = list(db.t.discovered_urls.rows_where("site_id=?", [site_id]))
    site = db.t.sites[site_id]
    root_url = site.get("root_url") or ""
    policy = get_crawl_url_policy(site_id)
    allow_root_seed = bool(policy.get("allow_root_seed", False))

    urls = []
    for row in rows:
        kind = (row.get("kind") or "html").lower()
        if html_only and kind != "html":
            continue
        canonical = canonicalize_internal_url(
            row["url"],
            row["url"],
            root_url,
            policy=policy,
            allow_root=allow_root_seed,
        )
        if not canonical:
            continue
        if policy.get("required_query_key") and "?" not in canonical:
            # Seed/root URLs are useful for crawl, but skip during fetch/parse/embed.
            continue
        urls.append(canonical)

    unique_urls = sorted(set(urls))
    if limit_urls is not None and limit_urls >= 0:
        return unique_urls[:limit_urls]
    return unique_urls


def _collect_chunk_ids_for_pages(db, page_ids: list[int]) -> list[int]:
    chunk_ids: list[int] = []
    for page_id in page_ids:
        for extract in db.t.extracts.rows_where("page_id=?", [page_id]):
            for chunk in db.t.chunks.rows_where("extract_id=?", [extract["id"]]):
                chunk_ids.append(chunk["id"])
    return chunk_ids


def _refresh_retrieval_cache_if_available() -> bool:
    try:
        try:
            from .llmapi import refresh_retrieval_cache
        except ImportError:
            from core.llmapi import refresh_retrieval_cache
        refresh_retrieval_cache()
        return True
    except Exception as exc:
        print(f"Retrieval cache refresh skipped: {exc}")
        return False


def run_daily_connections_refresh(
    db,
    *,
    site_id: int = 2,
    max_pages: int = 3000,
    crawl_delay: float = 0.1,
    fetch_delay: float = 0.0,
    batch_size: int = 64,
    skip_crawl: bool = False,
    html_only: bool = True,
    limit_urls: int | None = None,
    refresh_cache: bool = True,
    scrape_only: bool = False,
):
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    started_at = datetime.now(timezone.utc).isoformat()
    print(f"Starting daily refresh for site_id={site_id} name={site['name']} at {started_at}")

    discovered_before = len(list(db.t.discovered_urls.rows_where("site_id=?", [site_id])))

    crawl_visited = 0
    if not skip_crawl:
        crawl_t0 = time.perf_counter()
        crawl_visited = crawl_site(db, site_id=site_id, max_pages=max_pages, delay=crawl_delay)
        print(f"crawl_site visited={crawl_visited} in {time.perf_counter() - crawl_t0:.2f}s")

    discovered_after = len(list(db.t.discovered_urls.rows_where("site_id=?", [site_id])))
    discovered_new = discovered_after - discovered_before
    target_urls = _target_discovered_urls(
        db,
        site_id,
        html_only=html_only,
        limit_urls=limit_urls,
    )
    print(
        f"Discovered URLs before={discovered_before} after={discovered_after} "
        f"new={discovered_new} target_fetch={len(target_urls)}"
    )

    fetch_t0 = time.perf_counter()
    changed_page_ids: set[int] = set()
    new_url_count = 0
    changed_url_count = 0
    unchanged_url_count = 0
    failed_urls: list[tuple[str, str]] = []

    for idx, url in enumerate(target_urls, start=1):
        try:
            page_id, status = fetch_page(
                db,
                site_id=site_id,
                url=url,
                verbose=False,
                return_status=True,
            )
        except Exception as exc:
            failed_urls.append((url, str(exc)))
            continue

        if status == "new":
            changed_page_ids.add(page_id)
            new_url_count += 1
        elif status == "changed":
            changed_page_ids.add(page_id)
            changed_url_count += 1
        else:
            unchanged_url_count += 1

        if fetch_delay > 0:
            time.sleep(fetch_delay)

        if idx % 25 == 0 or idx == len(target_urls):
            print(f"Fetched {idx}/{len(target_urls)} URLs")

    fetch_elapsed = time.perf_counter() - fetch_t0
    print(f"Fetch phase completed in {fetch_elapsed:.2f}s")

    changed_page_list = sorted(changed_page_ids)
    parse_extracts = 0
    parse_chunks = 0
    embed_count = 0

    if scrape_only:
        print("Scrape-only mode enabled. Parse and embed skipped.")
    elif changed_page_list:
        parse_t0 = time.perf_counter()
        parse_extracts, parse_chunks = process_pages_to_extracts_and_chunks(
            db,
            changed_page_list,
            clear_existing=True,
            use_upsert=False,
        )
        parse_elapsed = time.perf_counter() - parse_t0
        print(
            f"Parse phase pages={len(changed_page_list)} extracts={parse_extracts} "
            f"chunks={parse_chunks} in {parse_elapsed:.2f}s"
        )

        chunk_ids = _collect_chunk_ids_for_pages(db, changed_page_list)
        embed_t0 = time.perf_counter()
        embed_count = generate_embeddings_for_chunk_ids(
            db,
            chunk_ids,
            batch_size=batch_size,
        )
        embed_elapsed = time.perf_counter() - embed_t0
        print(f"Embed phase new_embeddings={embed_count} in {embed_elapsed:.2f}s")
    else:
        print("No changed/new pages detected. Parse and embed skipped.")

    cache_refreshed = False
    if refresh_cache:
        cache_refreshed = _refresh_retrieval_cache_if_available()

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "site_id": site_id,
        "site_name": site["name"],
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "crawl_visited": crawl_visited,
        "discovered_before": discovered_before,
        "discovered_after": discovered_after,
        "discovered_new": discovered_new,
        "target_urls": len(target_urls),
        "urls_new": new_url_count,
        "urls_changed": changed_url_count,
        "urls_unchanged": unchanged_url_count,
        "urls_failed": len(failed_urls),
        "pages_reparsed": len(changed_page_list),
        "extracts_written": parse_extracts,
        "chunks_written": parse_chunks,
        "embeddings_written": embed_count,
        "cache_refreshed_local_process": cache_refreshed,
        "scrape_only_mode": scrape_only,
    }

    print("Summary:")
    for key, value in summary.items():
        print(f"- {key}: {value}")

    if failed_urls:
        print("Failed URLs:")
        for url, error in failed_urls[:20]:
            print(f"- {url} -> {error}")
        if len(failed_urls) > 20:
            print(f"- ... and {len(failed_urls) - 20} more")

    return summary


def _run_check() -> None:
    db = bootstrap_scraper_db(":memory:")
    site_id = 2
    url = "https://connections/?docs=test"

    db.t.discovered_urls.insert(
        site_id=site_id,
        url=url,
        kind="html",
        discovered_at="now",
    )
    page = db.t.pages.insert(
        site_id=site_id,
        url=url,
        html="""
        <div id="post">
          <div class="doc-scrollable editor-content">
            <div class="doc-content-wrap"><p>Intro text</p></div>
            <h2>Header</h2>
            <p>Body text</p>
          </div>
        </div>
        """,
        content_hash="hash-v1",
        last_scraped="now",
        last_changed="now",
    )

    urls = _target_discovered_urls(db, site_id, html_only=True)
    assert urls == [url]

    extracts_count, chunks_count = process_pages_to_extracts_and_chunks(db, [page["id"]])
    assert extracts_count >= 1
    assert chunks_count >= 1

    chunk_ids = _collect_chunk_ids_for_pages(db, [page["id"]])
    assert len(chunk_ids) >= 1
    print("Check Passed")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily crawl/scrape/parse/embed refresh for connections")
    parser.add_argument("--db-path", type=str, default=None)
    parser.add_argument("--site-id", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=3000)
    parser.add_argument("--crawl-delay", type=float, default=0.1)
    parser.add_argument("--fetch-delay", type=float, default=0.0)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit-urls", type=int, default=None)
    parser.add_argument("--skip-crawl", action="store_true")
    parser.add_argument("--scrape-only", action="store_true")
    parser.add_argument("--include-non-html", action="store_true")
    parser.add_argument("--no-cache-refresh", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser


# %%
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    if args.check:
        _run_check()
    else:
        db = bootstrap_scraper_db(args.db_path, seed=True)
        run_daily_connections_refresh(
            db,
            site_id=args.site_id,
            max_pages=args.max_pages,
            crawl_delay=args.crawl_delay,
            fetch_delay=args.fetch_delay,
            batch_size=args.batch_size,
            skip_crawl=args.skip_crawl,
            html_only=not args.include_non_html,
            limit_urls=args.limit_urls,
            refresh_cache=not args.no_cache_refresh,
            scrape_only=args.scrape_only,
        )
