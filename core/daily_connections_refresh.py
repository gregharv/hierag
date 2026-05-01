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


def _log(message: str) -> None:
    print(message, flush=True)


def _normalize_target_urls(
    db,
    site_id: int,
    raw_urls,
    *,
    html_only: bool = True,
    limit_urls: int | None = None,
) -> list[str]:
    site = db.t.sites[site_id]
    root_url = site.get("root_url") or ""
    policy = get_crawl_url_policy(site_id)
    allow_root_seed = bool(policy.get("allow_root_seed", False))
    kind_by_url = {}
    for row in db.t.discovered_urls.rows_where("site_id=?", [site_id]):
        row_canonical = canonicalize_internal_url(
            row["url"],
            row["url"],
            root_url,
            policy=policy,
            allow_root=allow_root_seed,
        )
        if row_canonical and row_canonical not in kind_by_url:
            kind_by_url[row_canonical] = (row.get("kind") or "html").lower()

    urls = []
    seen: set[str] = set()
    for raw_url in raw_urls:
        canonical = canonicalize_internal_url(
            str(raw_url or ""),
            str(raw_url or ""),
            root_url,
            policy=policy,
            allow_root=allow_root_seed,
        )
        if not canonical:
            continue
        if html_only and kind_by_url.get(canonical, "html") != "html":
            continue
        if policy.get("required_query_key") and "?" not in canonical:
            # Seed/root URLs are useful for crawl, but skip during fetch/parse/embed.
            continue
        if canonical in seen:
            continue
        urls.append(canonical)
        seen.add(canonical)

    if limit_urls is not None and limit_urls >= 0:
        return urls[:limit_urls]
    return urls


def _target_discovered_urls(
    db,
    site_id: int,
    *,
    html_only: bool = True,
    limit_urls: int | None = None,
) -> list[str]:
    rows = list(db.t.discovered_urls.rows_where("site_id=?", [site_id]))
    urls = sorted({row["url"] for row in rows})
    return _normalize_target_urls(
        db,
        site_id,
        urls,
        html_only=html_only,
        limit_urls=limit_urls,
    )


def _collect_chunk_ids_for_pages(db, page_ids: list[int]) -> list[int]:
    chunk_ids: list[int] = []
    for page_id in page_ids:
        for extract in db.t.extracts.rows_where("page_id=?", [page_id]):
            for chunk in db.t.chunks.rows_where("extract_id=?", [extract["id"]]):
                chunk_ids.append(chunk["id"])
    return chunk_ids


def _parse_http_status_from_fetch_status(status: str) -> int | None:
    raw = (status or "").strip().lower()
    if not raw.startswith("http_"):
        return None
    value = raw.split("_", 1)[1]
    if not value.isdigit():
        return None
    return int(value)


def _record_fetch_success(db, url: str) -> None:
    rows = list(db.t.discovered_urls.rows_where("url=?", [url], limit=1))
    if not rows:
        return
    db.t.discovered_urls.update(
        {
            "id": rows[0]["id"],
            "consecutive_missing": 0,
            "last_fetch_status": "ok",
            "last_fetch_error": "",
            "last_failed_at": "",
        }
    )


def _record_fetch_failure(
    db,
    url: str,
    *,
    status: str,
    error: str,
    mark_missing: bool,
) -> int:
    rows = list(db.t.discovered_urls.rows_where("url=?", [url], limit=1))
    if not rows:
        return 0
    row = rows[0]
    previous_streak = int(row.get("consecutive_missing") or 0)
    next_streak = previous_streak + 1 if mark_missing else 0
    db.t.discovered_urls.update(
        {
            "id": row["id"],
            "consecutive_missing": next_streak,
            "last_fetch_status": str(status or "").strip()[:64],
            "last_fetch_error": str(error or "").strip()[:1000],
            "last_failed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return next_streak


def _delete_page_tree(db, page_id: int) -> dict[str, int]:
    counts = {"pages": 0, "extracts": 0, "chunks": 0, "embeddings": 0}
    extracts = list(db.t.extracts.rows_where("page_id=?", [page_id]))
    for extract in extracts:
        chunks = list(db.t.chunks.rows_where("extract_id=?", [extract["id"]]))
        for chunk in chunks:
            embeddings = list(db.t.embeddings.rows_where("chunk_id=?", [chunk["id"]]))
            for embedding in embeddings:
                db.t.embeddings.delete(embedding["id"])
                counts["embeddings"] += 1
            db.t.chunks.delete(chunk["id"])
            counts["chunks"] += 1
        db.t.extracts.delete(extract["id"])
        counts["extracts"] += 1
    db.t.pages.delete(page_id)
    counts["pages"] += 1
    return counts


def _prune_missing_url(db, url: str) -> dict[str, int]:
    counts = {
        "pages": 0,
        "extracts": 0,
        "chunks": 0,
        "embeddings": 0,
        "discovered_urls": 0,
    }
    page_rows = list(db.t.pages.rows_where("url=?", [url], limit=1))
    if page_rows:
        page_counts = _delete_page_tree(db, int(page_rows[0]["id"]))
        for key in ("pages", "extracts", "chunks", "embeddings"):
            counts[key] += page_counts[key]

    discovered_rows = list(db.t.discovered_urls.rows_where("url=?", [url], limit=1))
    if discovered_rows:
        db.t.discovered_urls.delete(discovered_rows[0]["id"])
        counts["discovered_urls"] += 1
    return counts


def _refresh_retrieval_cache_if_available() -> bool:
    try:
        try:
            from .llmapi import refresh_retrieval_cache
        except ImportError:
            from core.llmapi import refresh_retrieval_cache
        refresh_retrieval_cache()
        return True
    except Exception as exc:
        _log(f"Retrieval cache refresh skipped: {exc}")
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
    target_urls: list[str] | tuple[str, ...] | None = None,
    refresh_cache: bool = True,
    scrape_only: bool = False,
    prune_missing: bool = False,
    prune_missing_after: int = 1,
    prune_status_codes: list[int] | tuple[int, ...] | None = None,
    progress_log_every: int = 10,
):
    run_t0 = time.perf_counter()
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    started_at = datetime.now(timezone.utc).isoformat()
    _log(f"Starting daily refresh for site_id={site_id} name={site['name']} at {started_at}")

    discovered_before = len(list(db.t.discovered_urls.rows_where("site_id=?", [site_id])))

    crawl_visited = 0
    if not skip_crawl:
        crawl_t0 = time.perf_counter()
        crawl_visited = crawl_site(db, site_id=site_id, max_pages=max_pages, delay=crawl_delay)
        _log(f"crawl_site visited={crawl_visited} in {time.perf_counter() - crawl_t0:.2f}s")

    discovered_after = len(list(db.t.discovered_urls.rows_where("site_id=?", [site_id])))
    discovered_new = discovered_after - discovered_before
    effective_prune_status_codes = sorted(
        {int(code) for code in (prune_status_codes or [404, 410]) if int(code) > 0}
    )
    if not effective_prune_status_codes:
        effective_prune_status_codes = [404, 410]
    effective_prune_status_set = set(effective_prune_status_codes)
    effective_prune_missing_after = max(1, int(prune_missing_after))
    explicit_target_urls = target_urls is not None

    if target_urls is None:
        target_urls = _target_discovered_urls(
            db,
            site_id,
            html_only=html_only,
            limit_urls=limit_urls,
        )
    else:
        target_urls = _normalize_target_urls(
            db,
            site_id,
            target_urls,
            html_only=html_only,
            limit_urls=limit_urls,
        )
    _log(
        f"Discovered URLs before={discovered_before} after={discovered_after} "
        f"new={discovered_new} target_fetch={len(target_urls)}"
    )

    fetch_t0 = time.perf_counter()
    changed_page_ids: set[int] = set()
    new_url_count = 0
    changed_url_count = 0
    unchanged_url_count = 0
    failed_http_missing = 0
    failed_http_other = 0
    failed_exception = 0
    pruned_urls: list[str] = []
    prune_counts = {
        "pages": 0,
        "extracts": 0,
        "chunks": 0,
        "embeddings": 0,
        "discovered_urls": 0,
    }
    failed_urls: list[tuple[str, str]] = []
    interrupted = False
    interrupted_at_url = ""
    progress_every = max(1, int(progress_log_every))
    fetched_urls_completed = 0

    for idx, url in enumerate(target_urls, start=1):
        if idx == 1 or idx % progress_every == 0:
            elapsed = time.perf_counter() - fetch_t0
            _log(f"Fetch progress {idx}/{len(target_urls)} elapsed_s={elapsed:.2f} url={url}")
        try:
            page_id, status = fetch_page(
                db,
                site_id=site_id,
                url=url,
                verbose=False,
                return_status=True,
                allow_http_errors=True,
            )
        except KeyboardInterrupt:
            interrupted = True
            interrupted_at_url = url
            _log(f"Interrupted during fetch at {idx}/{len(target_urls)} url={url}")
            break
        except Exception as exc:
            failed_exception += 1
            error_text = str(exc)
            _record_fetch_failure(
                db,
                url,
                status="error",
                error=error_text,
                mark_missing=False,
            )
            failed_urls.append((url, error_text))
            fetched_urls_completed = idx
            continue

        if status == "new":
            if page_id is not None:
                changed_page_ids.add(page_id)
            new_url_count += 1
            _record_fetch_success(db, url)
        elif status == "changed":
            if page_id is not None:
                changed_page_ids.add(page_id)
            changed_url_count += 1
            _record_fetch_success(db, url)
        elif status == "unchanged":
            unchanged_url_count += 1
            _record_fetch_success(db, url)
        else:
            http_status = _parse_http_status_from_fetch_status(status)
            if http_status is None:
                failed_http_other += 1
                _record_fetch_failure(
                    db,
                    url,
                    status="error",
                    error=f"unknown fetch status: {status}",
                    mark_missing=False,
                )
                failed_urls.append((url, f"unknown fetch status: {status}"))
            else:
                is_missing_status = http_status in effective_prune_status_set
                streak = _record_fetch_failure(
                    db,
                    url,
                    status=status,
                    error=f"status {http_status}",
                    mark_missing=is_missing_status,
                )
                failed_urls.append((url, f"status {http_status}"))
                if is_missing_status:
                    failed_http_missing += 1
                    if prune_missing and streak >= effective_prune_missing_after:
                        counts = _prune_missing_url(db, url)
                        if counts["pages"] > 0 or counts["discovered_urls"] > 0:
                            pruned_urls.append(url)
                        for key in prune_counts:
                            prune_counts[key] += counts[key]
                else:
                    failed_http_other += 1

        if fetch_delay > 0:
            try:
                time.sleep(fetch_delay)
            except KeyboardInterrupt:
                interrupted = True
                interrupted_at_url = url
                _log(f"Interrupted during fetch delay at {idx}/{len(target_urls)} url={url}")
                break

        fetched_urls_completed = idx

        if idx % 25 == 0 or idx == len(target_urls):
            _log(f"Fetched {idx}/{len(target_urls)} URLs")

    fetch_elapsed = time.perf_counter() - fetch_t0
    _log(f"Fetch phase completed in {fetch_elapsed:.2f}s")
    if prune_missing and pruned_urls:
        _log(
            "Prune phase completed: "
            f"urls_pruned={len(pruned_urls)} "
            f"pages={prune_counts['pages']} "
            f"extracts={prune_counts['extracts']} "
            f"chunks={prune_counts['chunks']} "
            f"embeddings={prune_counts['embeddings']} "
            f"discovered_urls={prune_counts['discovered_urls']}"
        )

    changed_page_list = sorted(changed_page_ids)
    parse_extracts = 0
    parse_chunks = 0
    embed_count = 0

    if interrupted:
        _log("Run interrupted during fetch. Parse, embed, and cache refresh skipped.")
    elif scrape_only:
        _log("Scrape-only mode enabled. Parse and embed skipped.")
    elif changed_page_list:
        parse_t0 = time.perf_counter()
        parse_extracts, parse_chunks = process_pages_to_extracts_and_chunks(
            db,
            changed_page_list,
            clear_existing=True,
            use_upsert=False,
        )
        parse_elapsed = time.perf_counter() - parse_t0
        _log(
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
        _log(f"Embed phase new_embeddings={embed_count} in {embed_elapsed:.2f}s")
    else:
        _log("No changed/new pages detected. Parse and embed skipped.")

    cache_refreshed = False
    if refresh_cache and not interrupted:
        cache_refreshed = _refresh_retrieval_cache_if_available()

    discovered_final = len(list(db.t.discovered_urls.rows_where("site_id=?", [site_id])))
    finished_at = datetime.now(timezone.utc).isoformat()
    duration_seconds = round(time.perf_counter() - run_t0, 2)
    summary = {
        "site_id": site_id,
        "site_name": site["name"],
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "duration_seconds": duration_seconds,
        "crawl_visited": crawl_visited,
        "discovered_before": discovered_before,
        "discovered_after": discovered_after,
        "discovered_new": discovered_new,
        "discovered_final": discovered_final,
        "discovered_pruned": max(0, discovered_after - discovered_final),
        "target_urls": len(target_urls),
        "target_urls_explicit": explicit_target_urls,
        "fetched_urls_completed": fetched_urls_completed,
        "interrupted": interrupted,
        "interrupted_at_url": interrupted_at_url,
        "urls_new": new_url_count,
        "urls_changed": changed_url_count,
        "urls_unchanged": unchanged_url_count,
        "pages_added": new_url_count,
        "pages_modified": changed_url_count,
        "pages_removed": prune_counts["pages"],
        "urls_failed_http_missing": failed_http_missing,
        "urls_failed_http_other": failed_http_other,
        "urls_failed_exception": failed_exception,
        "urls_failed": len(failed_urls),
        "prune_missing_mode": prune_missing,
        "prune_missing_after": effective_prune_missing_after,
        "prune_status_codes": ",".join(str(code) for code in effective_prune_status_codes),
        "urls_pruned": len(pruned_urls),
        "pruned_pages": prune_counts["pages"],
        "pruned_extracts": prune_counts["extracts"],
        "pruned_chunks": prune_counts["chunks"],
        "pruned_embeddings": prune_counts["embeddings"],
        "pruned_discovered_urls": prune_counts["discovered_urls"],
        "pages_reparsed": len(changed_page_list),
        "extracts_written": parse_extracts,
        "chunks_written": parse_chunks,
        "embeddings_written": embed_count,
        "cache_refreshed_local_process": cache_refreshed,
        "scrape_only_mode": scrape_only,
    }

    _log("Summary:")
    for key, value in summary.items():
        _log(f"- {key}: {value}")

    if failed_urls:
        _log("Failed URLs:")
        for url, error in failed_urls[:20]:
            _log(f"- {url} -> {error}")
        if len(failed_urls) > 20:
            _log(f"- ... and {len(failed_urls) - 20} more")

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
    assert _parse_http_status_from_fetch_status("http_404") == 404
    assert _parse_http_status_from_fetch_status("new") is None
    print("Check Passed")


def _parse_status_codes_arg(raw: str) -> list[int]:
    values: list[int] = []
    for part in str(raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"Invalid HTTP status code value: {item}")
        code = int(item)
        if code < 100 or code > 599:
            raise ValueError(f"HTTP status code out of range: {code}")
        values.append(code)
    if not values:
        return [404, 410]
    return sorted(set(values))


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
    parser.add_argument("--prune-missing", action="store_true")
    parser.add_argument("--prune-missing-after", type=int, default=1)
    parser.add_argument("--prune-status-codes", type=str, default="404,410")
    parser.add_argument("--progress-log-every", type=int, default=10)
    parser.add_argument("--check", action="store_true")
    return parser


# %%
if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    try:
        prune_status_codes = _parse_status_codes_arg(args.prune_status_codes)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if args.check:
        _run_check()
    else:
        db = bootstrap_scraper_db(args.db_path, seed=True)
        try:
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
                prune_missing=args.prune_missing,
                prune_missing_after=args.prune_missing_after,
                prune_status_codes=prune_status_codes,
                progress_log_every=args.progress_log_every,
            )
        except KeyboardInterrupt:
            _log("Interrupted by user.")
            raise SystemExit(130)
