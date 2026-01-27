# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: crit
#     language: python
#     name: python3
# ---

# %% time_run="2026-01-24T21:38:10.883922+00:00"
from fastlite import database

db = database('scraper.db')

# %% time_run="2026-01-24T21:38:10.919060+00:00"
db.t

# %% time_run="2026-01-24T21:38:10.948562+00:00"
db.q(f"select * from sites")

# %% time_run="2026-01-24T21:38:10.976401+00:00"
db.t.discovered_urls(limit=1)


# %% time_run="2026-01-24T21:38:11.012690+00:00"
def scrape_discovered_pages(db, site_id=None, url_filter=None, delay=0.5):
    """
    Scrape pages from discovered_urls table and store them in pages table.
    
    Args:
        db: Database connection
        site_id: Optional site_id to filter by. If None, scrapes all sites.
        url_filter: Optional callable that takes a URL and returns True if it should be scraped.
        delay: Delay between requests in seconds.
    
    Returns:
        Number of pages scraped.
    """
    import httpx
    import hashlib
    from datetime import datetime
    import time
    
    # Find URLs from discovered_urls that haven't been scraped yet (not in pages table)
    if site_id:
        query = """
            SELECT d.id, d.site_id, d.url 
            FROM discovered_urls d
            LEFT JOIN pages p ON d.url = p.url
            WHERE d.site_id=? AND p.id IS NULL
        """
        params = (site_id,)
    else:
        query = """
            SELECT d.id, d.site_id, d.url 
            FROM discovered_urls d
            LEFT JOIN pages p ON d.url = p.url
            WHERE p.id IS NULL
        """
        params = ()
    
    pages_to_scrape = db.execute(query, params).fetchall()
    
    if url_filter:
        pages_to_scrape = [(pid, sid, url) for pid, sid, url in pages_to_scrape if url_filter(url)]
    
    scraped = 0
    for discovered_id, site_id_val, url in pages_to_scrape:
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
            if resp.status_code != 200:
                print(f"✗ {url}: status {resp.status_code}")
                continue
            
            html = resp.text
            content_hash = hashlib.md5(html.encode()).hexdigest()
            now = datetime.utcnow().isoformat()
            
            # Check if we already have this content_hash (duplicate HTML)
            existing_with_hash = list(db.t.pages.rows_where('content_hash=?', [content_hash], limit=1))
            
            if existing_with_hash:
                # Duplicate HTML found - reuse the existing HTML to avoid storing duplicate
                existing_page = existing_with_hash[0]
                existing_html = existing_page['html']
                print(f"⊘ {url} (duplicate HTML, reusing from {existing_page['url']})")
                # Still insert a page record for this URL, but reuse the HTML
                db.t.pages.insert(site_id=site_id_val, url=url, html=existing_html, content_hash=content_hash,
                                 last_scraped=now, last_changed=existing_page.get('last_changed', now))
            else:
                # New unique HTML content
                db.t.pages.insert(site_id=site_id_val, url=url, html=html, content_hash=content_hash,
                                 last_scraped=now, last_changed=now)
                print(f"✓ {url} (scraped)")
                scraped += 1

            _collect_pdfs(db, site_id_val, url, html)
            _collect_pdfs_from_subpages(db, site_id_val, url, html)
            
            time.sleep(delay)
        except Exception as e:
            print(f"✗ {url}: {e}")
    
    return scraped


# %% time_run="2026-01-24T21:38:11.044204+00:00"
import httpx
from bs4 import BeautifulSoup
import hashlib
from datetime import datetime
from urllib.parse import urljoin, urlparse

PDF_GUARDRAILS = {"max_pdfs_per_page": 10}

def _links(html, base_url):
    return [urljoin(base_url, a["href"]) for a in BeautifulSoup(html, "lxml").find_all("a", href=True)]

def _pdf_like(url):
    u = url.lower()
    return ".pdf" in u or "/uploadedfiles/" in u

def _save_pdf(db, site_id, pdf_url, source_url, resp=None):
    if list(db.t.pdfs.rows_where("url=?", [pdf_url], limit=1)): return
    resp = resp or httpx.get(pdf_url, timeout=20, follow_redirects=True, verify=False)
    if resp.status_code != 200: return
    ct = resp.headers.get("content-type", "").lower()
    if "application/pdf" not in ct and ".pdf" not in pdf_url.lower(): return
    pdf_bytes = resp.content
    now = datetime.utcnow().isoformat()
    db.t.pdfs.insert(site_id=site_id, url=pdf_url, source_url=source_url, content_type=ct.split(";")[0], content_hash=hashlib.md5(pdf_bytes).hexdigest(), bytes=pdf_bytes, pages=None, last_scraped=now, last_changed=now)

def _collect_pdfs(db, site_id, page_url, html):
    cap = PDF_GUARDRAILS["max_pdfs_per_page"]
    for u in [x for x in _links(html, page_url) if _pdf_like(x)][:cap]:
        _save_pdf(db, site_id, u, page_url)

def _collect_pdfs_from_subpages(db, site_id, page_url, html):
    root = urlparse(page_url).netloc
    base = urlparse(page_url).path.rstrip("/") + "/"
    for link in _links(html, page_url):
        p = urlparse(link)
        if p.netloc not in {root, f"www.{root}", root.replace("www.", "")} or not p.path.startswith(base): continue
        resp = httpx.get(link, timeout=10, follow_redirects=True, verify=False)
        if resp.status_code != 200: continue
        if "application/pdf" in resp.headers.get("content-type", "").lower(): _save_pdf(db, site_id, link, page_url, resp)
        else: _collect_pdfs(db, site_id, link, resp.text)

def fetch_page(db, site_id, url):
    """Fetch a single page and store it"""
    site = db.t.sites[site_id]
    if not site: raise ValueError(f"No site with id {site_id}")
    
    resp = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
    html = resp.text
    content_hash = hashlib.md5(html.encode()).hexdigest()
    now = datetime.utcnow().isoformat()
    
    # Check if URL already exists
    existing_by_url = list(db.t.pages.rows_where('url=?', [url], limit=1))
    
    if existing_by_url:
        page = existing_by_url[0]
        # Check if content changed
        if page['content_hash'] != content_hash:
            db.t.pages.update({'id': page['id'], 'html': html, 'content_hash': content_hash,
                              'last_scraped': now, 'last_changed': now})
            print(f"↻ Updated: {url}")
        else:
            db.t.pages.update({'id': page['id'], 'last_scraped': now})
            print(f"  {url} (unchanged)")
        _collect_pdfs(db, site_id, url, html)
        _collect_pdfs_from_subpages(db, site_id, url, html)
        return page['id']
    else:
        # Check if we already have this content_hash (duplicate HTML)
        existing_with_hash = list(db.t.pages.rows_where('content_hash=?', [content_hash], limit=1))
        
        if existing_with_hash:
            # Duplicate HTML found - reuse the existing HTML
            existing_page = existing_with_hash[0]
            existing_html = existing_page['html']
            print(f"⊘ {url} (duplicate HTML, reusing from {existing_page['url']})")
            row = db.t.pages.insert(site_id=site_id, url=url, html=existing_html, content_hash=content_hash,
                                    last_scraped=now, last_changed=existing_page.get('last_changed', now))
        else:
            # New unique HTML content
            row = db.t.pages.insert(site_id=site_id, url=url, html=html, content_hash=content_hash,
                                    last_scraped=now, last_changed=now)
            print(f"✓ New: {url}")
        _collect_pdfs(db, site_id, url, html)
        _collect_pdfs_from_subpages(db, site_id, url, html)
        return row['id']


# %% time_run="2026-01-24T21:38:11.067147+00:00"
scrape_discovered_pages(db, 1)

# %% time_run="2026-01-24T21:38:29.138686+00:00"
# JEA page (site_id=1)
fetch_page(db, 1, 'https://www.jea.com/my_account/rates/')

# %%
fetch_page(db, 1, 'https://www.jea.com/About/Procurement/Bid_Results/')

# %% time_run="2026-01-24T21:39:02.969231+00:00"
# Connections page (site_id=2) 
# fetch_page(db, 2, 'https://connections/?docs=residential/start-stop-transfer-traditional-service/transfer-service/transferring-service')

# %%
