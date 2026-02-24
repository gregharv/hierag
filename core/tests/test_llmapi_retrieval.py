from __future__ import annotations

import numpy as np

import core.llmapi_retrieval as retrieval
from core.fastlite_db import ensure_pipeline_schema, get_scraper_db
from core.llmapi_retrieval import (
    build_source_links,
    canonicalize_source_url,
    extract_tab_step_anchors,
    get_parent_extracts,
)


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
        html='<ul><li id="tab-step1"><a href="#tab-step2">next</a></li></ul>',
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


def _seed_db_with_single_chunk():
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
    page = db.t.pages.insert(
        site_id=1,
        url="https://connections/?docs=single/topic",
        html="<div>single chunk</div>",
        content_hash="hash-single",
        last_scraped="now",
        last_changed="now",
    )
    extract = db.t.extracts.insert(page_id=page["id"], extract_index=0, text="single extract")
    chunk = db.t.chunks.insert(extract_id=extract["id"], chunk_index=0, text="single chunk")
    return db, int(chunk["id"])


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
    assert sources[0]["has_tab_steps"] is True
    assert sources[0]["tab_step_count"] == 2
    assert sources[1]["has_tab_steps"] is False
    assert sources[1]["tab_step_count"] == 0


def test_extract_tab_step_anchors_sorts_and_dedupes():
    html = """
    <div id="tab-step2"></div>
    <a href="#tab-step10">Step 10</a>
    <a href="#tab-step2">Step 2</a>
    <a href="#tab-step1">Step 1</a>
    """
    anchors = extract_tab_step_anchors(html)
    assert anchors == ["#tab-step1", "#tab-step2", "#tab-step10"]


def test_search_embeddings_refreshes_cache_when_stale_candidates_detected(monkeypatch):
    db, valid_chunk_id = _seed_db_with_single_chunk()
    stale_chunk_id = 999_001
    dim = 4

    stale_cache = {
        "chunk_ids": np.array([stale_chunk_id, valid_chunk_id], dtype=np.int64),
        "chunk_texts": ["stale", "valid"],
        "emb_matrix": np.array(
            [[1.0, 1.0, 1.0, 1.0], [0.4, 0.4, 0.4, 0.4]],
            dtype=np.float32,
        ),
        "doc_lens": np.array([1.0, 1.0], dtype=np.float32),
        "avg_doc_len": 1.0,
        "doc_freq": {},
        "term_postings": {},
        "num_docs": 2,
    }
    fresh_cache = {
        "chunk_ids": np.array([valid_chunk_id], dtype=np.int64),
        "chunk_texts": ["valid"],
        "emb_matrix": np.array([[0.4, 0.4, 0.4, 0.4]], dtype=np.float32),
        "doc_lens": np.array([1.0], dtype=np.float32),
        "avg_doc_len": 1.0,
        "doc_freq": {},
        "term_postings": {},
        "num_docs": 1,
    }

    state = {"cache": stale_cache}
    refresh_calls = {"count": 0}

    monkeypatch.setattr(retrieval, "_get_retrieval_cache", lambda _db: state["cache"])

    def _fake_refresh(_db):
        refresh_calls["count"] += 1
        state["cache"] = fresh_cache
        return fresh_cache

    monkeypatch.setattr(retrieval, "refresh_retrieval_cache", _fake_refresh)
    monkeypatch.setattr(
        retrieval,
        "_query_embeddings",
        lambda queries: np.ones((len(queries), dim), dtype=np.float32),
    )
    monkeypatch.setattr(
        retrieval,
        "_bm25_scores",
        lambda cache, query_terms: np.zeros(int(cache["num_docs"]), dtype=np.float32),
    )
    monkeypatch.setattr(retrieval, "_expand_query_variants", lambda query: [query])

    scored, debug = retrieval.search_embeddings_with_debug(db, "elephant", top_k=2)
    assert refresh_calls["count"] == 1
    assert scored and scored[0][1] == valid_chunk_id
    assert debug.get("cache_refresh_retry") is True
    assert int(debug.get("stale_candidates_skipped_initial") or 0) >= 1


def test_search_embeddings_skips_persistent_stale_candidates_after_retry(monkeypatch):
    db, _ = _seed_db_with_single_chunk()
    stale_chunk_id = 999_777
    dim = 4
    stale_only_cache = {
        "chunk_ids": np.array([stale_chunk_id], dtype=np.int64),
        "chunk_texts": ["stale"],
        "emb_matrix": np.array([[1.0, 1.0, 1.0, 1.0]], dtype=np.float32),
        "doc_lens": np.array([1.0], dtype=np.float32),
        "avg_doc_len": 1.0,
        "doc_freq": {},
        "term_postings": {},
        "num_docs": 1,
    }

    refresh_calls = {"count": 0}
    monkeypatch.setattr(retrieval, "_get_retrieval_cache", lambda _db: stale_only_cache)

    def _fake_refresh(_db):
        refresh_calls["count"] += 1
        return stale_only_cache

    monkeypatch.setattr(retrieval, "refresh_retrieval_cache", _fake_refresh)
    monkeypatch.setattr(
        retrieval,
        "_query_embeddings",
        lambda queries: np.ones((len(queries), dim), dtype=np.float32),
    )
    monkeypatch.setattr(
        retrieval,
        "_bm25_scores",
        lambda cache, query_terms: np.zeros(int(cache["num_docs"]), dtype=np.float32),
    )
    monkeypatch.setattr(retrieval, "_expand_query_variants", lambda query: [query])

    scored, debug = retrieval.search_embeddings_with_debug(db, "elephant", top_k=1)
    assert refresh_calls["count"] == 1
    assert scored == []
    assert debug.get("cache_refresh_retry") is True
    assert int(debug.get("stale_candidates_skipped_after_refresh") or 0) >= 1


def test_parent_extracts_and_source_links_skip_missing_chunk_ids():
    db, chunk_encoded, _chunk_decoded, _chunk_other = _seed_db_with_duplicate_urls()
    missing_chunk_id = 999_999
    scored = [
        (0.98, missing_chunk_id),
        (0.95, int(chunk_encoded["id"])),
    ]

    extracts = get_parent_extracts(db, scored, max_extracts=3)
    sources = build_source_links(db, scored, max_sources=3)

    assert len(extracts) == 1
    assert extracts[0]["chunk_id"] == int(chunk_encoded["id"])
    assert len(sources) == 1
    assert sources[0]["chunk_id"] == int(chunk_encoded["id"])


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
