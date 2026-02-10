from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin, urlparse
import time

import httpx
from bs4 import BeautifulSoup

from .fastlite_db import bootstrap_scraper_db, ensure_pipeline_schema, seed_sites


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


def get_internal_links(soup: BeautifulSoup, base_url: str, root_url: str) -> set[str]:
    """Extract all links that remain on the same root domain."""
    root_parsed = urlparse(root_url)
    root_netloc = root_parsed.netloc
    root_scheme = root_parsed.scheme or "https"
    netloc_variants = {
        root_netloc,
        "",
        root_netloc[4:] if root_netloc.startswith("www.") else f"www.{root_netloc}",
    }

    links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(urljoin(base_url, anchor["href"]))
        if parsed.netloc in netloc_variants:
            path = parsed.path or "/"
            query = f"?{parsed.query}" if parsed.query else ""
            fragment = f"#{parsed.fragment}" if parsed.fragment else ""
            clean_url = f"{root_scheme}://{root_netloc}{path}{query}{fragment}"
            if not path.lower().endswith((".jpg", ".png", ".gif", ".zip")):
                links.add(clean_url)
    return links


def crawl_site(db, site_id: int, max_pages: int = 10, delay: float = 0.5) -> int:
    """Crawl a site and persist discovered URLs."""
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    root_url = site["root_url"]
    domain = urlparse(root_url).netloc
    visited: set[str] = set()
    queue: list[str] = [root_url]

    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            response = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
            if response.status_code != 200:
                print(f"{url}: status {response.status_code}")
                continue

            existing = list(db.t.discovered_urls.rows_where("url=?", [url], limit=1))
            if not existing:
                db.t.discovered_urls.insert(
                    site_id=site_id,
                    url=url,
                    kind=_link_kind(url),
                    discovered_at=datetime.utcnow().isoformat(),
                )
                print(f"{url} (discovered)")
            else:
                kind = _link_kind(url)
                if existing[0].get("kind") != kind:
                    db.t.discovered_urls.update({"id": existing[0]["id"], "kind": kind})
                print(f"{url} (already discovered)")

            soup = BeautifulSoup(response.text, "lxml")
            for link in get_internal_links(soup, url, root_url):
                if urlparse(link).netloc in (
                    domain,
                    f"www.{domain}",
                    domain.replace("www.", ""),
                ) and link not in visited:
                    queue.append(link)

                existing_link = list(db.t.discovered_urls.rows_where("url=?", [link], limit=1))
                kind = _link_kind(link)
                if not existing_link:
                    db.t.discovered_urls.insert(
                        site_id=site_id,
                        url=link,
                        kind=kind,
                        discovered_at=datetime.utcnow().isoformat(),
                    )
                elif existing_link[0].get("kind") != kind:
                    db.t.discovered_urls.update({"id": existing_link[0]["id"], "kind": kind})

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
    assert "https://example.com/a" in links
    assert "https://example.com/b" in links
    assert all("other.com" not in link for link in links)
    assert len(list(test_db.t.sites())) >= 1
    print("Check Passed")
