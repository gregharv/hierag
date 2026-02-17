from __future__ import annotations

from collections import deque
from datetime import datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse
import time

import httpx
from bs4 import BeautifulSoup

from .fastlite_db import bootstrap_scraper_db, ensure_pipeline_schema, seed_sites
from .site_config import get_crawl_url_policy


def _link_kind(url: str) -> str:
    if urlparse(url).path.lower().endswith(".pdf"):
        return "pdf"
    try:
        response = httpx.head(url, timeout=6, follow_redirects=True, verify=False)
        if "application/pdf" in response.headers.get("content-type", "").lower():
            return "pdf"
    except Exception:
        pass
    return "html"


def _normalize_netloc(value: str) -> str:
    host = (value or "").strip().lower()
    if host.startswith("www."):
        return host[4:]
    return host


def canonicalize_internal_url(
    raw_url: str,
    base_url: str,
    root_url: str,
    *,
    policy: dict | None = None,
    allow_root: bool = False,
) -> str | None:
    href = (raw_url or "").strip()
    if not href or href.startswith("#"):
        return None

    parsed_href = urlparse(href)
    if parsed_href.scheme and parsed_href.scheme.lower() not in {"http", "https"}:
        return None

    root_parsed = urlparse(root_url)
    root_netloc = root_parsed.netloc
    root_scheme = root_parsed.scheme or "https"
    if not root_netloc:
        return None

    parsed = urlparse(urljoin(base_url, href))
    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    normalized_root = _normalize_netloc(root_netloc)
    normalized_netloc = _normalize_netloc(parsed.netloc)
    if normalized_netloc != normalized_root:
        return None

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    if path.lower().endswith((".jpg", ".png", ".gif", ".zip")):
        return None

    active_policy = policy or {}
    required_query_key = str(active_policy.get("required_query_key") or "").strip()
    allow_root_without_required_query = bool(
        active_policy.get("allow_root_without_required_query", False)
    )

    if required_query_key:
        value = ""
        if parsed.query:
            query_items = parse_qs(parsed.query, keep_blank_values=False)
            values = query_items.get(required_query_key) or []
            value = next((str(v).strip() for v in values if str(v).strip()), "")

        if value:
            # Keep path separators readable/consistent for docs-like slugs.
            query = f"?{required_query_key}={quote(value, safe='/-._~')}"
        elif allow_root and path == "/" and allow_root_without_required_query:
            query = ""
        else:
            return None
    else:
        query = f"?{parsed.query}" if parsed.query else ""
    return f"{root_scheme}://{root_netloc}{path}{query}"


def get_internal_links(soup: BeautifulSoup, base_url: str, root_url: str) -> set[str]:
    """Extract all links that remain on the same root domain."""
    links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        clean_url = canonicalize_internal_url(anchor["href"], base_url, root_url)
        if clean_url:
            links.add(clean_url)
    return links


def crawl_site(db, site_id: int, max_pages: int = 10, delay: float = 0.5) -> int:
    """Crawl a site and persist discovered URLs."""
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    policy = get_crawl_url_policy(site_id)
    root_url = site["root_url"]
    allow_root_seed = bool(policy.get("allow_root_seed", False))
    root_seed = (
        canonicalize_internal_url(
            root_url,
            root_url,
            root_url,
            policy=policy,
            allow_root=allow_root_seed,
        )
        or root_url
    )
    visited: set[str] = set()
    queue = deque([root_seed])
    queued: set[str] = {root_seed}
    discovered_by_url: dict[str, dict[str, object]] = {}
    for row in db.t.discovered_urls():
        key = canonicalize_internal_url(
            row["url"],
            row["url"],
            root_url,
            policy=policy,
            allow_root=allow_root_seed,
        )
        if not key:
            continue
        if key not in discovered_by_url:
            discovered_by_url[key] = {"id": row["id"], "kind": row.get("kind")}
    kind_cache: dict[str, str] = {}

    def resolve_kind(target_url: str) -> str:
        if target_url in kind_cache:
            return kind_cache[target_url]
        kind = _link_kind(target_url)
        kind_cache[target_url] = kind
        return kind

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        queued.discard(url)
        if url in visited:
            continue
        visited.add(url)

        try:
            response = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
            if response.status_code != 200:
                print(f"{url}: status {response.status_code}")
                continue

            existing = discovered_by_url.get(url)
            if not existing:
                kind = resolve_kind(url)
                row = db.t.discovered_urls.insert(
                    site_id=site_id,
                    url=url,
                    kind=kind,
                    discovered_at=datetime.utcnow().isoformat(),
                )
                discovered_by_url[url] = {"id": row["id"], "kind": kind}
                print(f"{url} (discovered)")
            else:
                if not existing.get("kind"):
                    kind = resolve_kind(url)
                    db.t.discovered_urls.update({"id": existing["id"], "kind": kind})
                    existing["kind"] = kind
                print(f"{url} (already discovered)")

            soup = BeautifulSoup(response.text, "lxml")
            for raw_link in get_internal_links(soup, url, root_url):
                link = canonicalize_internal_url(
                    raw_link,
                    url,
                    root_url,
                    policy=policy,
                    allow_root=False,
                )
                if not link:
                    continue
                if link not in visited and link not in queued:
                    queue.append(link)
                    queued.add(link)

                existing_link = discovered_by_url.get(link)
                if not existing_link:
                    kind = resolve_kind(link)
                    row = db.t.discovered_urls.insert(
                        site_id=site_id,
                        url=link,
                        kind=kind,
                        discovered_at=datetime.utcnow().isoformat(),
                    )
                    discovered_by_url[link] = {"id": row["id"], "kind": kind}
                elif not existing_link.get("kind"):
                    kind = resolve_kind(link)
                    db.t.discovered_urls.update({"id": existing_link["id"], "kind": kind})
                    existing_link["kind"] = kind

            if len(visited) % 25 == 0:
                print(
                    f"crawl progress visited={len(visited)} queued={len(queue)} "
                    f"known_discovered={len(discovered_by_url)}"
                )

            time.sleep(delay)
        except Exception as exc:
            print(f"{url}: {exc}")

    return len(visited)


def prepare_pipeline_db(db_path: str | None = None):
    """Initialize the pipeline DB schema and seed site configs."""
    db = bootstrap_scraper_db(db_path, seed=True)
    ensure_pipeline_schema(db)
    seed_sites(db)
    return db


# %%
if __name__ == "__main__":
    from bs4 import BeautifulSoup

    test_db = bootstrap_scraper_db(":memory:")
    html = """
    <html><body>
      <a href="/a">A</a>
      <a href="https://example.com/b">B</a>
      <a href="https://other.com/c">C</a>
    </body></html>
    """
    links = get_internal_links(
        BeautifulSoup(html, "lxml"),
        base_url="https://example.com/start",
        root_url="https://example.com",
    )
    docs_policy = {"required_query_key": "docs", "allow_root_without_required_query": True}
    assert (
        canonicalize_internal_url(
            "https://connections/?docs=abc&format=list",
            "https://connections",
            "https://connections",
            policy=docs_policy,
            allow_root=False,
        )
        == "https://connections/?docs=abc"
    )
    assert (
        canonicalize_internal_url(
            "https://connections/?docs=residential%2Fsolar",
            "https://connections",
            "https://connections",
            policy=docs_policy,
            allow_root=False,
        )
        == "https://connections/?docs=residential/solar"
    )
    assert (
        canonicalize_internal_url(
            "https://connections/?page_id=7621",
            "https://connections",
            "https://connections",
            policy=docs_policy,
            allow_root=False,
        )
        is None
    )
    assert "https://example.com/a" in links
    assert "https://example.com/b" in links
    assert all("other.com" not in link for link in links)
    assert len(list(test_db.t.sites())) >= 1
    print("Check Passed")
