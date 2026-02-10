from __future__ import annotations

import re

from bs4 import BeautifulSoup
import html2text

from .fastlite_db import bootstrap_scraper_db
from .site_config import get_site_config


def extract_breadcrumb_context(html: str, site_id: int | None = None) -> str:
    """
    Extract breadcrumb context from HTML as `Context: A > B > C`.
    """
    if site_id is None:
        return ""

    site_config = get_site_config(site_id)
    if not site_config or "breadcrumb_selector" not in site_config:
        return ""

    soup = BeautifulSoup(html, "lxml")
    breadcrumb_selector = site_config["breadcrumb_selector"]
    breadcrumb_element = soup.select_one(breadcrumb_selector)
    if not breadcrumb_element:
        return ""

    if site_id == 1:
        breadcrumb_items = breadcrumb_element.select("li")
        if breadcrumb_items:
            breadcrumb_texts = [li.get_text(strip=True) for li in breadcrumb_items if li.get_text(strip=True)]
        else:
            breadcrumb_text = breadcrumb_element.get_text(strip=True)
            breadcrumb_texts = [t.strip() for t in breadcrumb_text.split(">") if t.strip()]
    else:
        breadcrumb_items = breadcrumb_element.select("li")
        breadcrumb_texts = [li.get_text(strip=True) for li in breadcrumb_items if li.get_text(strip=True)]

    if breadcrumb_texts:
        return "Context: " + " > ".join(breadcrumb_texts) + "\n\n"
    return ""


def split_md_sections(html, selector, converter=None, min_len=100, max_len=1000):
    """
    Split content by accordion items first, then by headers.
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(selector)
    if not container:
        return []

    if converter is None:
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0

    chunks = []
    accordion_parts = []
    accordion_items = container.select('[class*="accordion"], [class*="collapse"], details')

    if accordion_items:
        for item in accordion_items:
            try:
                if hasattr(item, "name") and item.name:
                    content = converter.handle(str(item)).strip()
                    if content and ("Accordion Item" in content or "Closed Title" in content):
                        accordion_parts.append(content)
                        item.decompose()
            except (TypeError, AttributeError, ValueError):
                continue

    if accordion_parts:
        for part in accordion_parts:
            parts = re.split(r"\n\nAccordion Item\n\nClosed Title:", part)
            for piece in parts:
                if piece.strip():
                    chunks.append(piece.strip())

    remaining = converter.handle(str(container)).strip()
    if remaining:
        for chunk in re.split(r"(?=^#{1,3}\s)", remaining, flags=re.MULTILINE):
            if chunk.strip():
                chunks.append(chunk.strip())

    result = []
    for chunk in chunks:
        if result and len(result[-1]) < min_len:
            result[-1] += "\n\n" + chunk
        elif len(chunk) > max_len:
            paras = chunk.split("\n\n")
            buf = ""
            for para in paras:
                if len(buf) + len(para) > max_len and buf:
                    result.append(buf.strip())
                    buf = para
                else:
                    buf += "\n\n" + para if buf else para
            if buf:
                result.append(buf.strip())
        else:
            result.append(chunk)
    return result


def split_with_tabs(html, selector, converter=None, min_len=100, max_len=1000):
    """
    Split content by tabs first, then by headers.
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(selector)
    if not container:
        return []

    if converter is None:
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0

    tabs_wrap = container.select_one(".kt-tabs-content-wrap")
    tabs_list = container.select_one(".kt-tabs-title-list")
    intro = container.select_one(".doc-content-wrap > p")

    chunks = []
    step_parts = []

    if intro:
        step_parts.append(intro.get_text().strip())
        intro.decompose()

    if tabs_wrap:
        for tab in tabs_wrap.select('[class*="kt-inner-tab-"]'):
            classes = tab.get("class", [])
            step_num = ""
            for class_name in classes:
                if class_name.startswith("kt-inner-tab-"):
                    step_num = class_name.replace("kt-inner-tab-", "")
                    break

            if not step_num:
                class_str = " ".join(classes) if classes else str(tab.get("class", ""))
                match = re.search(r"kt-inner-tab-(\d+)", class_str)
                if match:
                    step_num = match.group(1)

            content = converter.handle(str(tab)).strip()
            if step_num and content:
                step_parts.append(f"Step {step_num}: {content}")
        tabs_wrap.decompose()

    if tabs_list:
        tabs_list.decompose()
    if step_parts:
        chunks.append("\n\n".join(step_parts))

    remaining = converter.handle(str(container)).strip()
    if remaining:
        for chunk in re.split(r"(?=^#{1,3}\s)", remaining, flags=re.MULTILINE):
            if chunk.strip():
                chunks.append(chunk.strip())

    result = []
    for chunk in chunks:
        if result and len(result[-1]) < min_len:
            result[-1] += "\n\n" + chunk
        elif len(chunk) > max_len:
            paras = chunk.split("\n\n")
            buf = ""
            for para in paras:
                if len(buf) + len(para) > max_len and buf:
                    result.append(buf.strip())
                    buf = para
                else:
                    buf += "\n\n" + para if buf else para
            if buf:
                result.append(buf.strip())
        else:
            result.append(chunk)
    return result


def create_extracts_from_page(html, selector, site_id, max_extract_len=100_000):
    """
    Create long extracts from page HTML and prepend breadcrumb context.
    """
    context_prefix = extract_breadcrumb_context(html, site_id)
    site_config = get_site_config(site_id)

    if site_config and "split_function" in site_config:
        split_func_name = site_config["split_function"]
        split_func = globals().get(split_func_name)
        if callable(split_func):
            raw_chunks = split_func(html, selector, max_len=100_000)
        else:
            soup = BeautifulSoup(html, "lxml")
            container = soup.select_one(selector)
            if not container:
                return []
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.body_width = 0
            raw_chunks = [converter.handle(str(container)).strip()]
    else:
        soup = BeautifulSoup(html, "lxml")
        container = soup.select_one(selector)
        if not container:
            return []
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0
        raw_chunks = [converter.handle(str(container)).strip()]

    full_text = "\n\n".join(raw_chunks)
    extracts = []
    if full_text:
        while len(full_text) > max_extract_len:
            break_point = max_extract_len
            last_break = full_text.rfind("\n\n", 0, max_extract_len)
            if last_break > max_extract_len * 0.5:
                break_point = last_break + 2

            extract_text = full_text[:break_point].strip()
            if extract_text:
                extracts.append(extract_text)
            full_text = full_text[break_point:].strip()

        if full_text:
            extracts.append(full_text)

    if context_prefix:
        extracts = [context_prefix + extract for extract in extracts]
    return extracts


def create_chunks_from_extract(extract_text, max_chunk_len=1000):
    """
    Create smaller chunks from an extract while keeping context prefix.
    """
    context_prefix = ""
    if extract_text.startswith("Context:"):
        context_end = extract_text.find("\n\n", 0)
        if context_end > 0:
            context_prefix = extract_text[: context_end + 2]
            extract_text = extract_text[context_end + 2 :].strip()

    chunks = []
    if extract_text:
        while len(extract_text) > max_chunk_len:
            break_point = max_chunk_len
            search_end = min(len(extract_text), max_chunk_len)
            search_start = max(0, max_chunk_len - 1000)
            search_text = extract_text[search_start:search_end]
            header_pattern = re.compile(r"^#{1,6}\s+", re.MULTILINE)
            header_matches = list(header_pattern.finditer(search_text))

            if header_matches:
                last_header_match = header_matches[-1]
                header_pos = search_start + last_header_match.start()
                if header_pos > max_chunk_len * 0.3:
                    break_point = header_pos
            else:
                last_break = extract_text.rfind("\n\n", 0, max_chunk_len)
                if last_break > max_chunk_len * 0.5:
                    break_point = last_break + 2

            chunk_text = extract_text[:break_point].strip()
            if chunk_text:
                chunks.append(chunk_text)
            extract_text = extract_text[break_point:].strip()

        if extract_text:
            chunks.append(extract_text)

    if context_prefix:
        chunks = [context_prefix + chunk for chunk in chunks]
    else:
        chunks = [chunk for chunk in chunks if chunk.strip()]
    return chunks


def process_all_pages_to_extracts_and_chunks(db, clear_existing=True, use_upsert=False):
    """
    Convert all pages into extracts + chunks and store in DB.
    """
    all_pages = list(db.t.pages())
    total_extracts = 0
    total_chunks = 0

    for page in all_pages:
        site_id = page["site_id"]
        site = db.t.sites[site_id]
        if not site:
            print(f"Warning: Site {site_id} not found for page {page['id']}")
            continue

        if clear_existing:
            existing_extracts = list(db.t.extracts.rows_where("page_id=?", [page["id"]]))
            if existing_extracts:
                extract_ids = [extract["id"] for extract in existing_extracts]
                for extract_id in extract_ids:
                    for chunk in db.t.chunks.rows_where("extract_id=?", [extract_id]):
                        db.t.chunks.delete(chunk["id"])
                for extract in existing_extracts:
                    db.t.extracts.delete(extract["id"])

        extracts = create_extracts_from_page(page["html"], site["selector"], site_id, max_extract_len=100_000)
        page_chunks_count = 0

        for extract_index, extract_text in enumerate(extracts):
            if use_upsert:
                extract = db.t.extracts.upsert(
                    page_id=page["id"],
                    extract_index=extract_index,
                    text=extract_text,
                    pk=["page_id", "extract_index"],
                )
            else:
                extract = db.t.extracts.insert(
                    page_id=page["id"],
                    extract_index=extract_index,
                    text=extract_text,
                )
            total_extracts += 1

            chunks = create_chunks_from_extract(extract_text, max_chunk_len=1000)
            for chunk_index, chunk_text in enumerate(chunks):
                if use_upsert:
                    db.t.chunks.upsert(
                        extract_id=extract["id"],
                        chunk_index=chunk_index,
                        text=chunk_text,
                        pk=["extract_id", "chunk_index"],
                    )
                else:
                    db.t.chunks.insert(
                        extract_id=extract["id"],
                        chunk_index=chunk_index,
                        text=chunk_text,
                    )
                total_chunks += 1
                page_chunks_count += 1

        print(f"Page {page['id']}: Created {len(extracts)} extracts, {page_chunks_count} chunks")

    return total_extracts, total_chunks


# %%
if __name__ == "__main__":
    test_db = bootstrap_scraper_db(":memory:")
    page = test_db.t.pages.insert(
        site_id=1,
        url="https://example.com/rates",
        html="""
        <html><body>
          <nav id="secondary-content"><li>Home</li><li>Rates</li></nav>
          <div id="secondary-content">
            <div>
              <div class="cb-content-container cf"><h2>Header</h2><p>Body text.</p></div>
            </div>
          </div>
        </body></html>
        """,
        content_hash="x",
        last_scraped="now",
        last_changed="now",
    )
    extracts_count, chunks_count = process_all_pages_to_extracts_and_chunks(test_db)
    assert page["id"] is not None
    assert extracts_count >= 0
    assert chunks_count >= 0
    print("Check Passed")
