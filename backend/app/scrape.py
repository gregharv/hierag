from __future__ import annotations

from datetime import datetime
import hashlib
import time

import httpx

from .fastlite_db import bootstrap_scraper_db


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
            response = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
            if response.status_code != 200:
                print(f"{url}: status {response.status_code}")
                continue

            html = response.text
            content_hash = hashlib.md5(html.encode()).hexdigest()
            now = datetime.utcnow().isoformat()

            existing_with_hash = list(db.t.pages.rows_where("content_hash=?", [content_hash], limit=1))
            if existing_with_hash:
                existing_page = existing_with_hash[0]
                db.t.pages.insert(
                    site_id=site_id_val,
                    url=url,
                    html=existing_page["html"],
                    content_hash=content_hash,
                    last_scraped=now,
                    last_changed=existing_page.get("last_changed", now),
                )
                print(f"{url} (duplicate HTML)")
            else:
                db.t.pages.insert(
                    site_id=site_id_val,
                    url=url,
                    html=html,
                    content_hash=content_hash,
                    last_scraped=now,
                    last_changed=now,
                )
                print(f"{url} (scraped)")
                scraped += 1

            time.sleep(delay)
        except Exception as exc:
            print(f"{url}: {exc}")

    return scraped


def fetch_page(db, site_id: int, url: str):
    """Fetch one URL and insert/update it in pages."""
    site = db.t.sites[site_id]
    if not site:
        raise ValueError(f"No site with id {site_id}")

    response = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
    html = response.text
    content_hash = hashlib.md5(html.encode()).hexdigest()
    now = datetime.utcnow().isoformat()

    existing_by_url = list(db.t.pages.rows_where("url=?", [url], limit=1))
    if existing_by_url:
        page = existing_by_url[0]
        if page["content_hash"] != content_hash:
            db.t.pages.update(
                {
                    "id": page["id"],
                    "html": html,
                    "content_hash": content_hash,
                    "last_scraped": now,
                    "last_changed": now,
                }
            )
            print(f"Updated: {url}")
        else:
            db.t.pages.update({"id": page["id"], "last_scraped": now})
            print(f"{url} (unchanged)")
        return page["id"]

    existing_with_hash = list(db.t.pages.rows_where("content_hash=?", [content_hash], limit=1))
    if existing_with_hash:
        existing_page = existing_with_hash[0]
        row = db.t.pages.insert(
            site_id=site_id,
            url=url,
            html=existing_page["html"],
            content_hash=content_hash,
            last_scraped=now,
            last_changed=existing_page.get("last_changed", now),
        )
        print(f"{url} (duplicate HTML)")
    else:
        row = db.t.pages.insert(
            site_id=site_id,
            url=url,
            html=html,
            content_hash=content_hash,
            last_scraped=now,
            last_changed=now,
        )
        print(f"New: {url}")
    return row["id"]


def prepare_pipeline_db(db_path: str | None = None):
    return bootstrap_scraper_db(db_path, seed=True)


# %%
if __name__ == "__main__":
    test_db = bootstrap_scraper_db(":memory:")
    assert test_db.t.pages is not None
    assert scrape_discovered_pages(test_db, site_id=1, delay=0.0) == 0
    print("Check Passed")
