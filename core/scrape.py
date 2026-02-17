from __future__ import annotations

from datetime import datetime
import hashlib
import re
import time

import httpx
from bs4 import BeautifulSoup, Comment

from .fastlite_db import bootstrap_scraper_db

CONTENT_HASH_VERSION = "v2"


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _select_hash_node(html: str, selector: str | None):
    soup = BeautifulSoup(html or "", "lxml")

    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    if selector:
        selected = soup.select_one(selector)
        if selected is not None:
            return selected
    if soup.body is not None:
        return soup.body
    return soup


def compute_content_hash(html: str, selector: str | None) -> str:
    """
    Build a stable, content-focused hash to avoid false positives from dynamic page chrome.
    """
    node = _select_hash_node(html, selector)

    text = _normalize_space(" ".join(node.stripped_strings))
    links = []
    for anchor in node.select("a[href]"):
        href = _normalize_space(anchor.get("href") or "")
        if not href:
            continue
        label = _normalize_space(anchor.get_text(" ", strip=True))
        links.append(f"{href}|{label}")

    payload = text + "\n" + "\n".join(sorted(set(links)))
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()
    return f"{CONTENT_HASH_VERSION}:{digest}"


def _is_effectively_unchanged(
    old_hash: str | None,
    old_html: str | None,
    new_hash: str,
    selector: str | None,
) -> bool:
    if (old_hash or "") == new_hash:
        return True

    # Backward-compatible fallback for legacy rows that stored raw HTML md5.
    if old_hash and not str(old_hash).startswith(f"{CONTENT_HASH_VERSION}:") and old_html:
        return compute_content_hash(old_html, selector) == new_hash
    return False


def scrape_discovered_pages(db, site_id: int | None = None, url_filter=None, delay: float = 0.5) -> int:
    """
    Scrape HTML from discovered URLs and persist into pages.
    """
    if site_id is not None:
        discovered_rows = list(db.t.discovered_urls.rows_where("site_id=?", [site_id]))
    else:
        discovered_rows = list(db.t.discovered_urls())

    existing_urls = {row["url"] for row in db.t.pages()}
    pages_to_scrape = [row for row in discovered_rows if row["url"] not in existing_urls]
    if url_filter:
        pages_to_scrape = [row for row in pages_to_scrape if url_filter(row["url"])]

    scraped = 0
    for row in pages_to_scrape:
        site_id_val = row["site_id"]
        url = row["url"]
        try:
            _, status = fetch_page(
                db,
                site_id=site_id_val,
                url=url,
                verbose=True,
                return_status=True,
            )
            if status == "new":
                scraped += 1

            time.sleep(delay)
        except Exception as exc:
            print(f"{url}: {exc}")

    return scraped


def fetch_page(
    db,
    site_id: int,
    url: str,
    verbose: bool = True,
    return_status: bool = False,
):
    """Fetch one URL and insert/update it in pages."""
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    response = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
    if response.status_code != 200:
        raise RuntimeError(f"{url}: status {response.status_code}")

    html = response.text
    selector = site.get("selector")
    content_hash = compute_content_hash(html, selector)
    now = datetime.utcnow().isoformat()

    existing_by_url = list(db.t.pages.rows_where("url=?", [url], limit=1))
    if existing_by_url:
        page = existing_by_url[0]
        if not _is_effectively_unchanged(
            old_hash=page.get("content_hash"),
            old_html=page.get("html"),
            new_hash=content_hash,
            selector=selector,
        ):
            db.t.pages.update(
                {
                    "id": page["id"],
                    "html": html,
                    "content_hash": content_hash,
                    "last_scraped": now,
                    "last_changed": now,
                }
            )
            if verbose:
                print(f"Updated: {url}")
            status = "changed"
        else:
            update_payload = {"id": page["id"], "last_scraped": now}
            # Migrate legacy hashes forward without marking as changed.
            if page.get("content_hash") != content_hash:
                update_payload["content_hash"] = content_hash
            db.t.pages.update(update_payload)
            if verbose:
                print(f"{url} (unchanged)")
            status = "unchanged"
        return (page["id"], status) if return_status else page["id"]

    row = db.t.pages.insert(
        site_id=site_id,
        url=url,
        html=html,
        content_hash=content_hash,
        last_scraped=now,
        last_changed=now,
    )
    if verbose:
        print(f"New: {url}")
    return (row["id"], "new") if return_status else row["id"]


def prepare_pipeline_db(db_path: str | None = None):
    return bootstrap_scraper_db(db_path, seed=True)


# %%
if __name__ == "__main__":
    test_db = bootstrap_scraper_db(":memory:")
    html_a = """
    <html><body>
      <script nonce="abc">window.ts=123;</script>
      <div id="post"><div class="doc-scrollable editor-content"><p>Hello world</p></div></div>
    </body></html>
    """
    html_b = """
    <html><body>
      <script nonce="xyz">window.ts=999;</script>
      <div id="post"><div class="doc-scrollable editor-content"><p>Hello world</p></div></div>
    </body></html>
    """
    html_c = """
    <html><body>
      <div id="post"><div class="doc-scrollable editor-content"><p>Hello universe</p></div></div>
    </body></html>
    """
    selector = "#post > div.doc-scrollable.editor-content"
    hash_a = compute_content_hash(html_a, selector)
    hash_b = compute_content_hash(html_b, selector)
    hash_c = compute_content_hash(html_c, selector)
    assert hash_a == hash_b
    assert hash_a != hash_c
    assert test_db.t.pages is not None
    assert scrape_discovered_pages(test_db, site_id=1, delay=0.0) == 0
    print("Check Passed")
