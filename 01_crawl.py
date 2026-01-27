# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
#   kernelspec:
#     display_name: py313
#     language: python
#     name: python3
# ---

# %% time_run="2026-01-24T21:36:42.872358+00:00"
from fastlite import database

# %%
db = database('scraper.db')

sites = db.t.sites
if sites not in db.t:
    sites.create(id=int, root_url=str, selector=str, breadcrumb_selector=str, split_function=str, name=str, pk='id')
    sites.create_index(['root_url'], unique=True)

discovered_urls = db.t.discovered_urls
if discovered_urls not in db.t:
    discovered_urls.create(id=int, site_id=int, url=str, kind=str, discovered_at=str, pk='id', foreign_keys=[('site_id', 'sites')])
    discovered_urls.create_index(['url'], unique=True)

pages = db.t.pages
if pages not in db.t:
    pages.create(id=int, site_id=int, url=str, html=str, content_hash=str, last_scraped=str, last_changed=str, pk='id', foreign_keys=[('site_id', 'sites')])
    pages.create_index(['url'], unique=True)

extracts = db.t.extracts
if extracts not in db.t:
    extracts.create(id=int, page_id=int, pdf_id=int, extract_index=int, text=str, pk='id', foreign_keys=[('page_id', 'pages')])

chunks = db.t.chunks
if chunks not in db.t:
    chunks.create(id=int, extract_id=int, chunk_index=int, text=str, pk='id', foreign_keys=[('extract_id', 'extracts')])

embeddings = db.t.embeddings
if embeddings not in db.t:
    embeddings.create(id=int, chunk_id=int, embedding=bytes, pk='id', foreign_keys=[('chunk_id', 'chunks')])

pdfs = db.t.pdfs
if pdfs not in db.t:
    pdfs.create(id=int, site_id=int, url=str, source_url=str, content_type=str, content_hash=str, bytes=bytes, pages=int, last_scraped=str, last_changed=str, pk='id', foreign_keys=[('site_id', 'sites')])
    pdfs.create_index(['url'], unique=True)
    pdfs.create_index(['content_hash'])

def _ensure_extracts_pdf_column(db):
    cols = {row['name'] for row in db.q("PRAGMA table_info(extracts)")}
    if 'pdf_id' not in cols:
        db.q("ALTER TABLE extracts ADD COLUMN pdf_id int")

_ensure_extracts_pdf_column(db)

db.t

# %% time_run="2026-01-24T21:36:42.882153+00:00"
import importlib.util
spec = importlib.util.spec_from_file_location("utils", "00_utils.py")
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)

for site in utils.SITES:
    db.t.sites.upsert(**site)

# %% time_run="2026-01-24T21:36:42.889042+00:00"
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time

def _link_kind(url):
    if urlparse(url).path.lower().endswith(".pdf"): return "pdf"
    try:
        r = httpx.head(url, timeout=6, follow_redirects=True, verify=False)
        if "application/pdf" in r.headers.get("content-type", "").lower(): return "pdf"
    except Exception:
        pass
    return "html"

def get_internal_links(soup, base_url, root_url):
    """Extract all internal links from a page that belong to the same domain as root_url."""
    root_parsed = urlparse(root_url)
    root_netloc, root_scheme = root_parsed.netloc, root_parsed.scheme or 'https'
    netloc_variants = {root_netloc, '', root_netloc[4:] if root_netloc.startswith('www.') else f'www.{root_netloc}'}
    
    links = set()
    for a in soup.find_all('a', href=True):
        parsed = urlparse(urljoin(base_url, a['href']))
        if parsed.netloc in netloc_variants:
            path = parsed.path or '/'
            query = f"?{parsed.query}" if parsed.query else ''
            fragment = f"#{parsed.fragment}" if parsed.fragment else ''
            clean_url = f"{root_scheme}://{root_netloc}{path}{query}{fragment}"
            if not path.lower().endswith(('.jpg', '.png', '.gif', '.zip')):
                links.add(clean_url)
    return links


# %% time_run="2026-01-24T21:36:42.895512+00:00"
from datetime import datetime

def crawl_site(db, site_id, max_pages=10, delay=0.5):
    """Crawl a site to discover URLs. Only stores URLs, not HTML content."""
    site = db.t.sites[site_id]
    if not site: raise ValueError(f"No site with id {site_id}")
    root_url, domain = site['root_url'], urlparse(site['root_url']).netloc
    
    visited, queue = set(), [root_url]
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True, verify=False)
            if resp.status_code != 200: 
                print(f"✗ {url}: status {resp.status_code}")
                continue
            
            existing = list(db.t.discovered_urls.rows_where('url=?', [url], limit=1))
            if not existing:
                db.t.discovered_urls.insert(site_id=site_id, url=url, kind=_link_kind(url), discovered_at=datetime.utcnow().isoformat())
                print(f"✓ {url} (discovered)")
            else:
                kind = _link_kind(url)
                if existing[0].get('kind') != kind:
                    db.t.discovered_urls.update({'id': existing[0]['id'], 'kind': kind})
                print(f"  {url} (already discovered)")
            
            soup = BeautifulSoup(resp.text, 'lxml')
            for link in get_internal_links(soup, url, root_url):
                if urlparse(link).netloc in (domain, f"www.{domain}", domain.replace("www.", "")) and link not in visited:
                    queue.append(link)
                existing_link = list(db.t.discovered_urls.rows_where('url=?', [link], limit=1))
                kind = _link_kind(link)
                if not existing_link:
                    db.t.discovered_urls.insert(site_id=site_id, url=link, kind=kind, discovered_at=datetime.utcnow().isoformat())
                elif existing_link[0].get('kind') != kind:
                    db.t.discovered_urls.update({'id': existing_link[0]['id'], 'kind': kind})
            
            time.sleep(delay)
        except Exception as e:
            print(f"✗ {url}: {e}")
    
    return len(visited)


# %% time_run="2026-01-24T21:36:42.900011+00:00"
crawl_site(db, 1)

# %%
# Test crawl starting at Bid Results page
_orig_root = db.t.sites[1]["root_url"]
db.t.sites.update({"id": 1, "root_url": "https://www.jea.com/About/Procurement/Bid_Results/"})
crawl_site(db, 1, max_pages=10)
db.t.sites.update({"id": 1, "root_url": _orig_root})

# %%

# %%
crawl_site(db, 2)

# %%
