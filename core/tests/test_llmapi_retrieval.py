from __future__ import annotations

from core.fastlite_db import ensure_pipeline_schema, get_scraper_db
from core.llmapi_retrieval import build_source_links, canonicalize_source_url, get_parent_extracts


def _seed_db_with_duplicate_urls():
    db = get_scraper_db(":memory:")
    ensure_pipeline_schema(db)
    db.t.sites.insert(
        id=1,
        root_url="https://connections",
        selector="body",
        breadcrumb_selector="body",
        split_function="split_md_sections",
        name="connections",
    )

    page_encoded = db.t.pages.insert(
        site_id=1,
        url="https://connections/?docs=residential%2Fmyway%2Ftopic#tab-a",
        html="<div>encoded</div>",
        content_hash="h1",
        last_scraped="now",
        last_changed="now",
    )
    page_decoded = db.t.pages.insert(
        site_id=1,
        url="https://connections/?docs=residential/myway/topic",
        html="<div>decoded</div>",
        content_hash="h2",
        last_scraped="now",
        last_changed="now",
    )
    page_other = db.t.pages.insert(
        site_id=1,
        url="https://connections/?docs=residential/traditional/topic",
        html="<div>other</div>",
        content_hash="h3",
        last_scraped="now",
        last_changed="now",
    )

    extract_encoded = db.t.extracts.insert(page_id=page_encoded["id"], extract_index=0, text="Encoded extract")
    extract_decoded = db.t.extracts.insert(page_id=page_decoded["id"], extract_index=0, text="Decoded extract")
    extract_other = db.t.extracts.insert(page_id=page_other["id"], extract_index=0, text="Other extract")

    chunk_encoded = db.t.chunks.insert(
        extract_id=extract_encoded["id"],
        chunk_index=0,
        text="encoded chunk",
    )
    chunk_decoded = db.t.chunks.insert(
        extract_id=extract_decoded["id"],
        chunk_index=0,
        text="decoded chunk",
    )
    chunk_other = db.t.chunks.insert(
        extract_id=extract_other["id"],
        chunk_index=0,
        text="other chunk",
    )
    return db, chunk_encoded, chunk_decoded, chunk_other


def test_canonicalize_source_url_normalizes_docs_variants():
    encoded = "https://connections/?docs=residential%2Fmyway%2Ftopic#tab-a"
    decoded = "https://connections/?docs=residential/myway/topic"
    assert canonicalize_source_url(encoded) == canonicalize_source_url(decoded)


def test_get_parent_extracts_dedupes_by_canonical_url():
    db, chunk_encoded, chunk_decoded, chunk_other = _seed_db_with_duplicate_urls()
    scored = [
        (0.95, int(chunk_encoded["id"])),
        (0.90, int(chunk_decoded["id"])),
        (0.85, int(chunk_other["id"])),
    ]

    extracts = get_parent_extracts(db, scored, max_extracts=6)
    assert len(extracts) == 2
    assert extracts[0]["url_canonical"] == "https://connections/?docs=residential/myway/topic"
    assert extracts[1]["url_canonical"] == "https://connections/?docs=residential/traditional/topic"


def test_build_source_links_dedupes_by_canonical_url_and_sets_url_canonical():
    db, chunk_encoded, chunk_decoded, chunk_other = _seed_db_with_duplicate_urls()
    scored = [
        (0.95, int(chunk_encoded["id"])),
        (0.90, int(chunk_decoded["id"])),
        (0.85, int(chunk_other["id"])),
    ]
    score_details = {
        int(chunk_encoded["id"]): {"from_vector": True, "from_bm25": False, "vector_score_raw": 0.95},
        int(chunk_decoded["id"]): {"from_vector": True, "from_bm25": False, "vector_score_raw": 0.90},
        int(chunk_other["id"]): {"from_vector": False, "from_bm25": True, "bm25_score_raw": 0.85},
    }

    sources = build_source_links(db, scored, max_sources=6, score_details=score_details)
    assert len(sources) == 2
    assert sources[0]["url_canonical"] == "https://connections/?docs=residential/myway/topic"
    assert sources[1]["url_canonical"] == "https://connections/?docs=residential/traditional/topic"


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
