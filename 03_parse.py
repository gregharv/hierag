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

# %%
from fastlite import database
from bs4 import BeautifulSoup
import html2text
import re
import importlib.util

db = database('scraper.db')

# Import utils
spec = importlib.util.spec_from_file_location("utils", "00_utils.py")
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)

# %%
db.t

# %%
db.t.pages

# %%
db.t.pages(limit=1)

# %%
db.q(f"select * from pages where site_id=2 limit 1")

# %%
print(db.t.pages(limit=1)[0]['html'])


# %%
def extract_breadcrumb_context(html, site_id=None):
    """
    Extract breadcrumb context from HTML.
    Returns formatted context string like 'Context: Home > Residential > ...' or empty string.
    
    Args:
        html: HTML content
        site_id: Site ID to determine which selector to use (from utils.SITES config)
    """
    if site_id is None:
        return ''
    
    site_config = utils.get_site_config(site_id)
    if not site_config or 'breadcrumb_selector' not in site_config:
        return ''
    
    soup = BeautifulSoup(html, 'lxml')
    breadcrumb_selector = site_config['breadcrumb_selector']
    breadcrumb_element = soup.select_one(breadcrumb_selector)
    
    if not breadcrumb_element:
        return ''
    
    # For JEA site (site_id=1), try to find list items first, otherwise extract text directly
    if site_id == 1:
        breadcrumb_items = breadcrumb_element.select('li')
        if breadcrumb_items:
            breadcrumb_texts = [li.get_text(strip=True) for li in breadcrumb_items if li.get_text(strip=True)]
        else:
            # Fallback: extract text and split by common separators
            breadcrumb_text = breadcrumb_element.get_text(strip=True)
            breadcrumb_texts = [t.strip() for t in breadcrumb_text.split('>') if t.strip()]
    else:
        # For other sites (like Connections), extract list items
        breadcrumb_items = breadcrumb_element.select('li')
        breadcrumb_texts = [li.get_text(strip=True) for li in breadcrumb_items if li.get_text(strip=True)]
    
    if breadcrumb_texts:
        return 'Context: ' + ' > '.join(breadcrumb_texts) + '\n\n'
    
    return ''

# %%
def split_md_sections(html, selector, converter=None, min_len=100, max_len=1000):
    """
    Split content by accordion items first, then by headers for remaining content.
    max_len=6000 characters ensures chunks stay well under 8k token context limit.
    """

    soup = BeautifulSoup(html, 'lxml')
    container = soup.select_one(selector)
    if not container:
        return []

    if converter is None:
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0

    chunks = []
    accordion_parts = []
    
    # Extract accordion items (looking for elements that might be accordions)
    # This pattern looks for elements with "Closed Title" text or accordion-like structure
    accordion_items = container.select('[class*="accordion"], [class*="collapse"], details')
    
    if accordion_items:
        for item in accordion_items:
            try:
                # Check if element has a valid name before converting
                if hasattr(item, 'name') and item.name:
                    content = converter.handle(str(item)).strip()
                    if content:
                        # Check if it matches the accordion pattern in markdown
                        if 'Accordion Item' in content or 'Closed Title' in content:
                            accordion_parts.append(content)
                            item.decompose()
            except (TypeError, AttributeError, ValueError) as e:
                # Skip malformed elements
                continue
    
    if accordion_parts:
        # Split accordion parts by the markdown pattern
        for part in accordion_parts:
            parts = re.split(r'\n\nAccordion Item\n\nClosed Title:', part)
            for p in parts:
                if p.strip(): chunks.append(p.strip())
    
    # Process remaining content
    remaining = converter.handle(str(container)).strip()
    if remaining:
        for c in re.split(r'(?=^#{1,3}\s)', remaining, flags=re.MULTILINE):
            if c.strip(): chunks.append(c.strip())
    
    res = []
    for c in chunks:
        if res and len(res[-1]) < min_len: res[-1] += '\n\n' + c
        elif len(c) > max_len:
            paras = c.split('\n\n')
            buf = ''
            for p in paras:
                if len(buf) + len(p) > max_len and buf:
                    res.append(buf.strip())
                    buf = p
                else: buf += '\n\n' + p if buf else p
            if buf: res.append(buf.strip())
        else: res.append(c)
    return res

page_html = db.q(f"select html from pages where url='https://www.jea.com/my_account/rates/'")[0]['html']
selector = db.q(f"select selector from sites where id=1")[0]['selector']
chunks = split_md_sections(page_html, selector)
[(i, len(c), c[:80]) for i,c in enumerate(chunks)]


# %%
def split_with_tabs(html, selector, converter=None, min_len=100, max_len=1000):
    """
    Split content by tabs first, then by headers for remaining content.
    Used for extracting raw content before creating extracts.
    """

    soup = BeautifulSoup(html, 'lxml')
    container = soup.select_one(selector)
    if not container:
        return []

    if converter is None:
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0

    tabs_wrap = container.select_one('.kt-tabs-content-wrap')
    tabs_list = container.select_one('.kt-tabs-title-list')
    intro = container.select_one('.doc-content-wrap > p')
    
    chunks = []
    step_parts = []
    
    if intro:
        step_parts.append(intro.get_text().strip())
        intro.decompose()
    
    if tabs_wrap:
        for tab in tabs_wrap.select('[class*="kt-inner-tab-"]'):
            # Extract step number from class name (e.g., "kt-inner-tab-1" -> "1")
            classes = tab.get('class', [])
            step_num = ''
            for cls in classes:
                if cls.startswith('kt-inner-tab-'):
                    step_num = cls.replace('kt-inner-tab-', '')
                    break
            # Fallback: try to extract from class string if class list doesn't work
            if not step_num:
                class_str = ' '.join(classes) if classes else str(tab.get('class', ''))
                match = re.search(r'kt-inner-tab-(\d+)', class_str)
                if match:
                    step_num = match.group(1)
            content = converter.handle(str(tab)).strip()
            if step_num and content: step_parts.append(f"Step {step_num}: {content}")
        tabs_wrap.decompose()
    
    if tabs_list: tabs_list.decompose()
    if step_parts: chunks.append('\n\n'.join(step_parts))
    
    remaining = converter.handle(str(container)).strip()
    if remaining:
        for c in re.split(r'(?=^#{1,3}\s)', remaining, flags=re.MULTILINE):
            if c.strip(): chunks.append(c.strip())
    
    res = []
    for c in chunks:
        if res and len(res[-1]) < min_len: res[-1] += '\n\n' + c
        elif len(c) > max_len:
            paras = c.split('\n\n')
            buf = ''
            for p in paras:
                if len(buf) + len(p) > max_len and buf:
                    res.append(buf.strip())
                    buf = p
                else: buf += '\n\n' + p if buf else p
            if buf: res.append(buf.strip())
        else: res.append(c)
    
    return res

page = db.q(f"select * from pages where site_id=2 limit 1")[0]
site = db.q(f"select * from sites where id=2")[0]
chunks2 = split_with_tabs(page['html'], site['selector'])
[(i, len(c), c[:80]) for i,c in enumerate(chunks2)]

# %%
print(chunks2[0])

# %%
def create_extracts_from_page(html, selector, site_id, max_extract_len=100_000):
    """
    Create extracts from a page HTML.
    Extracts are 10,000 character chunks with breadcrumb context prepended.
    
    Returns:
        List of extract text strings with context
    """
    # Extract breadcrumb context
    context_prefix = extract_breadcrumb_context(html, site_id)
    
    # Get raw content based on site_id configuration
    site_config = utils.get_site_config(site_id)
    if site_config and 'split_function' in site_config:
        split_func_name = site_config['split_function']
        if split_func_name == 'split_with_tabs':
            raw_chunks = split_with_tabs(html, selector, max_len=100_000)
        elif split_func_name == 'split_md_sections':
            raw_chunks = split_md_sections(html, selector, max_len=100_000)
        else:
            # Fallback: simple HTML to text conversion
            soup = BeautifulSoup(html, 'lxml')
            container = soup.select_one(selector)
            if not container:
                return []
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.body_width = 0
            raw_chunks = [converter.handle(str(container)).strip()]
    else:
        # Fallback: simple HTML to text conversion
        soup = BeautifulSoup(html, 'lxml')
        container = soup.select_one(selector)
        if not container:
            return []
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.body_width = 0
        raw_chunks = [converter.handle(str(container)).strip()]
    
    # Combine chunks and split into extracts of max_extract_len
    full_text = '\n\n'.join(raw_chunks)
    
    extracts = []
    if full_text:
        # Split into extracts of max_extract_len, trying to break at paragraph boundaries
        while len(full_text) > max_extract_len:
            # Try to find a good break point (double newline)
            break_point = max_extract_len
            last_break = full_text.rfind('\n\n', 0, max_extract_len)
            if last_break > max_extract_len * 0.5:  # Only use if it's not too early
                break_point = last_break + 2
            
            extract_text = full_text[:break_point].strip()
            if extract_text:
                extracts.append(extract_text)
            full_text = full_text[break_point:].strip()
        
        # Add remaining text
        if full_text:
            extracts.append(full_text)
    
    # Prepend context to all extracts
    if context_prefix:
        extracts = [context_prefix + extract for extract in extracts]
    
    return extracts

# %%
def create_chunks_from_extract(extract_text, max_chunk_len=1000):
    """
    Create chunks from an extract text.
    Chunks are 1,000 character pieces with the same context as the extract.
    
    Args:
        extract_text: The extract text (already includes context prefix)
        max_chunk_len: Maximum length for each chunk
    
    Returns:
        List of chunk text strings
    """
    # Extract context prefix if present
    context_prefix = ''
    if extract_text.startswith('Context:'):
        # Find where context ends (after the first double newline)
        context_end = extract_text.find('\n\n', 0)
        if context_end > 0:
            context_prefix = extract_text[:context_end + 2]
            extract_text = extract_text[context_end + 2:].strip()
    
    chunks = []
    if extract_text:
        # Split into chunks of max_chunk_len, prioritizing markdown header boundaries
        while len(extract_text) > max_chunk_len:
            break_point = max_chunk_len
            
            # First, try to find a markdown header (line starting with #) before max_chunk_len
            # Search backwards from max_chunk_len to find the nearest header
            search_end = min(len(extract_text), max_chunk_len)
            search_start = max(0, max_chunk_len - 1000)  # Search back up to 1000 chars
            search_text = extract_text[search_start:search_end]
            
            # Find all markdown headers in the search area (headers start at beginning of line)
            # Pattern matches: start of line, then # followed by 1-6 more #, then space
            header_pattern = re.compile(r'^#{1,6}\s+', re.MULTILINE)
            header_matches = list(header_pattern.finditer(search_text))
            
            if header_matches:
                # Use the last (closest to max_chunk_len) header as break point
                # Split BEFORE the header so header stays with its content
                last_header_match = header_matches[-1]
                header_pos = search_start + last_header_match.start()
                if header_pos > max_chunk_len * 0.3:  # Only use if not too early
                    break_point = header_pos
            else:
                # Fallback: try to find a paragraph boundary (double newline)
                last_break = extract_text.rfind('\n\n', 0, max_chunk_len)
                if last_break > max_chunk_len * 0.5:  # Only use if it's not too early
                    break_point = last_break + 2
            
            chunk_text = extract_text[:break_point].strip()
            if chunk_text:
                chunks.append(chunk_text)
            extract_text = extract_text[break_point:].strip()
        
        # Add remaining text
        if extract_text:
            chunks.append(extract_text)
    
    # Prepend context to all chunks
    if context_prefix:
        chunks = [context_prefix + chunk for chunk in chunks]
    else:
        chunks = [chunk for chunk in chunks if chunk.strip()]
    
    return chunks

# %%
def process_all_pages_to_extracts_and_chunks(db):
    """
    Process all pages to create extracts (10,000 chars) and chunks (1,000 chars).
    Both extracts and chunks include breadcrumb context.
    
    Returns:
        Tuple of (num_extracts_created, num_chunks_created)
    """
    all_pages = list(db.t.pages())
    total_extracts = 0
    total_chunks = 0
    
    for page in all_pages:
        site_id = page['site_id']
        site = db.t.sites[site_id]
        
        if not site:
            print(f"Warning: Site {site_id} not found for page {page['id']}")
            continue
        
        # Create extracts from page
        extracts = create_extracts_from_page(page['html'], site['selector'], site_id, max_extract_len=100_000)
        
        # Store extracts in database
        page_chunks_count = 0
        for i, extract_text in enumerate(extracts):
            extract = db.t.extracts.insert(
                page_id=page['id'],
                extract_index=i,
                text=extract_text,
            )
            total_extracts += 1
            
            # Create chunks from extract
            chunks = create_chunks_from_extract(extract_text, max_chunk_len=1000)
            
            # Store chunks in database
            for j, chunk_text in enumerate(chunks):
                db.t.chunks.insert(
                    extract_id=extract['id'],
                    chunk_index=j,
                    text=chunk_text,
                )
                total_chunks += 1
                page_chunks_count += 1
        
        print(f"Page {page['id']}: Created {len(extracts)} extracts, {page_chunks_count} chunks")
    
    return total_extracts, total_chunks

# %%
# Process all pages to create extracts and chunks
extracts_count, chunks_count = process_all_pages_to_extracts_and_chunks(db)
print(f"\nTotal: Created {extracts_count} extracts and {chunks_count} chunks")

# %%
# Verify the structure
if db.t.extracts():
    sample_extract = list(db.t.extracts(limit=1))[0]
    print(f"\nSample extract (ID: {sample_extract['id']}):")
    print(f"Length: {len(sample_extract['text'])} chars")
    print(f"Preview: {sample_extract['text'][:200]}...")
    
    # Show chunks for this extract
    extract_chunks = list(db.t.chunks.rows_where('extract_id=?', [sample_extract['id']]))
    print(f"\nChunks for this extract: {len(extract_chunks)}")
    if extract_chunks:
        print(f"First chunk length: {len(extract_chunks[0]['text'])} chars")
        print(f"First chunk preview: {extract_chunks[0]['text'][:200]}...")

# %%
print(db.t.chunks()[0]['text'])

# %%
print(db.t.chunks()[1]['text'])

# %%
print(db.t.chunks()[2]['text'])

# %%

# %%
# Clear existing extracts and chunks
# for x in db.t.chunks():
#     db.t.chunks.delete(x['id'])

# for x in db.t.extracts():
#     db.t.extracts.delete(x['id'])

# %%
