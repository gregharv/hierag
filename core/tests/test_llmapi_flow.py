from __future__ import annotations

from types import SimpleNamespace

import core.llmapi_flow as flow
from core.fastlite_db import ensure_pipeline_schema, get_scraper_db


def _seed_flow_db():
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

    pages = []
    for idx, slug in enumerate(("alpha", "beta", "gamma"), start=1):
        pages.append(
            db.t.pages.insert(
                site_id=1,
                url=f"https://connections/?docs=residential/{slug}",
                html=f"<div>{slug}</div>",
                content_hash=f"h-{slug}",
                last_scraped="now",
                last_changed="now",
            )
        )

    extracts = []
    for idx, page in enumerate(pages, start=1):
        extracts.append(
            db.t.extracts.insert(
                page_id=page["id"],
                extract_index=0,
                text=f"extract {idx}",
            )
        )

    chunks = []
    for idx, extract in enumerate(extracts, start=1):
        chunks.append(
            db.t.chunks.insert(
                extract_id=extract["id"],
                chunk_index=0,
                text=f"chunk {idx}",
            )
        )

    chunk_meta = {}
    for chunk in chunks:
        extract = db.t.extracts[chunk["extract_id"]]
        page = db.t.pages[extract["page_id"]]
        chunk_meta[int(chunk["id"])] = {
            "extract_id": int(extract["id"]),
            "url": page["url"],
            "text": chunk["text"],
        }
    return db, chunk_meta


def _make_debug_payload(query: str, scored: list[tuple[float, int]], chunk_meta: dict[int, dict]) -> dict:
    ranked_chunks = []
    by_chunk_id = {}
    for rank, (score, chunk_id) in enumerate(scored, start=1):
        meta = chunk_meta[int(chunk_id)]
        item = {
            "rank": rank,
            "score": float(score),
            "chunk_id": int(chunk_id),
            "extract_id": int(meta["extract_id"]),
            "url": meta["url"],
            "url_canonical": meta["url"],
            "from_vector": True,
            "from_bm25": True,
            "vector_score_raw": float(score),
            "bm25_score_raw": float(score),
            "vector_score_norm": float(score),
            "bm25_score_norm": float(score),
            "chunk_preview": meta["text"],
        }
        ranked_chunks.append(item)
        by_chunk_id[int(chunk_id)] = item
    return {
        "query": query,
        "query_variants": [query],
        "candidate_counts": {"vector": len(scored), "bm25": len(scored), "merged": len(scored)},
        "timings": {"vector_s": 0.01, "bm25_s": 0.01, "fusion_s": 0.01, "total_s": 0.03},
        "ranked_chunks": ranked_chunks,
        "by_chunk_id": by_chunk_id,
    }


class _FakeResponses:
    def create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return [{"type": "response.output_text.delta", "delta": "stub-answer"}]
        return SimpleNamespace(output_text="stub-answer")


class _FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.responses = _FakeResponses()


def test_stream_dual_merge_prefers_original_signal_and_disables_glossary(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_ids = sorted(chunk_meta.keys())
    chunk_a, chunk_b, chunk_c = chunk_ids[0], chunk_ids[1], chunk_ids[2]
    search_calls = []

    def fake_rewrite(query, history=None):
        rewritten = "Debt recovery for an active Traditional customer who is disconnected for nonpayment"
        return rewritten, {"used": True, "model": "gpt-5-nano", "original_query": query, "rewritten_query": rewritten}

    def fake_search(_db, query, top_k=5):
        search_calls.append((query, top_k))
        if query == "Debt recovery":
            scored = [(0.90, chunk_a), (0.80, chunk_b)]
        else:
            scored = [(0.95, chunk_c)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(flow, "OpenAI", _FakeOpenAI)

    events = list(
        flow.stream_answer_with_context(
            db,
            "Debt recovery",
            top_k=10,
            max_extracts=2,
            history=[{"role": "user", "content": "prior turn"}],
        )
    )
    debug_event = next(event for event in events if event.get("type") == "debug")
    debug = debug_event["debug"]

    assert len(search_calls) == 2
    assert search_calls[0][0] == "Debt recovery"
    assert search_calls[1][0].startswith("Debt recovery for an active Traditional")
    assert search_calls[0][1] >= 40
    assert debug["retrieval"]["strategy"] == "dual_merge"
    assert debug["retrieval"]["merge_weights"] == {"original": 0.65, "rewritten": 0.35}
    assert debug["retrieval"]["ranked_chunks"][0]["chunk_id"] == chunk_a
    assert debug["glossary"]["included"] is False
    assert "[glossary]" not in debug["llm_request"]["user_text"].lower()
    assert debug["cache"]["lookup_order"][0] == "Debt recovery"


def test_stream_includes_glossary_for_myway_queries(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_id = sorted(chunk_meta.keys())[0]

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        scored = [(0.88, chunk_id)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(flow, "OpenAI", _FakeOpenAI)

    events = list(flow.stream_answer_with_context(db, "How do I help a MyWay customer?", top_k=10, max_extracts=2))
    debug_event = next(event for event in events if event.get("type") == "debug")
    debug = debug_event["debug"]

    assert debug["retrieval"]["strategy"] == "single_original"
    assert debug["glossary"]["included"] is True
    assert "myway" in debug["glossary"]["trigger_terms"]
    assert "Glossary: 'Prepay' and 'MyWay'" in debug["llm_request"]["user_text"]


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
