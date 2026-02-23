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


def _set_tab_steps_for_chunk(db, chunk_id: int, steps: int = 1) -> None:
    chunk = db.t.chunks[int(chunk_id)]
    extract = db.t.extracts[int(chunk["extract_id"])]
    page = db.t.pages[int(extract["page_id"])]
    links = "".join(f'<a href="#tab-step{idx}">Step {idx}</a>' for idx in range(1, int(steps) + 1))
    db.t.pages.update({"id": int(page["id"]), "html": links})


class _FakeResponses:
    def create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return [{"type": "response.output_text.delta", "delta": "stub-answer"}]
        return SimpleNamespace(output_text="stub-answer")


class _FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.responses = _FakeResponses()


class _SequencedFakeResponses:
    def __init__(self, stream_texts: list[str] | None = None):
        self._stream_texts = list(stream_texts or [])

    def create(self, *args, **kwargs):
        if kwargs.get("stream"):
            text = self._stream_texts.pop(0) if self._stream_texts else ""
            return [{"type": "response.output_text.delta", "delta": text}]
        return SimpleNamespace(output_text="stub-answer")


class _SequencedFakeOpenAI:
    stream_texts: list[str] = []

    def __init__(self, *args, **kwargs):
        self.responses = _SequencedFakeResponses(type(self).stream_texts)


class _CaptureResponses:
    last_user_text: str = ""

    def create(self, *args, **kwargs):
        if kwargs.get("stream"):
            return [{"type": "response.output_text.delta", "delta": "stub-answer"}]
        inputs = kwargs.get("input") or []
        if len(inputs) >= 2:
            user_content = (inputs[1].get("content") or [{}])[0]
            _CaptureResponses.last_user_text = str(user_content.get("text") or "")
        return SimpleNamespace(output_text="rewritten-question")


class _CaptureOpenAI:
    def __init__(self, *args, **kwargs):
        self.responses = _CaptureResponses()


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


def test_stream_idk_prefix_retries_retrieval_and_answers(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_ids = sorted(chunk_meta.keys())
    chunk_first, chunk_retry = chunk_ids[0], chunk_ids[1]
    search_calls = []

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        search_calls.append((query, int(top_k)))
        if len(search_calls) == 1:
            scored = [(0.81, chunk_first)]
        else:
            scored = [(0.96, chunk_retry)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    _SequencedFakeOpenAI.stream_texts = [
        "I don't know based on the provided context.",
        "Use the retry source details to complete the workflow.",
    ]
    monkeypatch.setattr(flow, "OpenAI", _SequencedFakeOpenAI)

    events = list(flow.stream_answer_with_context(db, "Need workflow steps", top_k=10, max_extracts=2))
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert final_text == "Use the retry source details to complete the workflow."
    assert len(search_calls) == 2
    assert search_calls[0][1] >= 40
    assert search_calls[1][1] >= 120
    assert debug["fallback"]["triggered"] is True
    assert debug["fallback"]["reason"] == "idk_prefix"
    assert debug["fallback"]["final_mode"] == "answer"
    assert debug["fallback"]["retry_config"] == {"top_k": 120, "max_extracts": 10}
    assert debug["fallback"]["first_pass_retrieval"] is not None
    assert debug["fallback"]["second_pass_retrieval"] is not None
    assert debug["sources"][0]["chunk_id"] == chunk_retry


def test_stream_idk_prefix_retries_then_clarifies(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_id = sorted(chunk_meta.keys())[0]
    search_calls = []

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        search_calls.append((query, int(top_k)))
        scored = [(0.82, chunk_id)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(
        flow,
        "_build_clarifying_question",
        lambda **_: "Can you clarify which program and customer type you mean?",
    )
    _SequencedFakeOpenAI.stream_texts = [
        "I don't know from the provided context.",
        "I still don't know from the context.",
    ]
    monkeypatch.setattr(flow, "OpenAI", _SequencedFakeOpenAI)

    events = list(flow.stream_answer_with_context(db, "Need workflow steps", top_k=10, max_extracts=2))
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert len(search_calls) == 2
    assert search_calls[1][1] >= 120
    assert final_text == "Can you clarify which program and customer type you mean?"
    assert "don't know" not in final_text.lower()
    assert debug["fallback"]["triggered"] is True
    assert debug["fallback"]["reason"] == "retry_still_insufficient"
    assert debug["fallback"]["final_mode"] == "clarify"
    assert debug["llm_request"] is None


def test_stream_no_context_retries_then_clarifies(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    search_calls = []

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        search_calls.append((query, int(top_k)))
        scored: list[tuple[float, int]] = []
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(
        flow,
        "_build_clarifying_question",
        lambda **_: "Can you clarify which exact policy and customer scenario you need?",
    )

    events = list(flow.stream_answer_with_context(db, "Need workflow steps", top_k=10, max_extracts=2))
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert len(search_calls) == 2
    assert search_calls[0][1] >= 40
    assert search_calls[1][1] >= 120
    assert final_text == "Can you clarify which exact policy and customer scenario you need?"
    assert debug["fallback"]["triggered"] is True
    assert debug["fallback"]["reason"] == "retry_still_insufficient"
    assert debug["fallback"]["final_mode"] == "clarify"
    assert debug["fallback"]["retry_config"] == {"top_k": 120, "max_extracts": 10}
    assert debug["fallback"]["first_pass_retrieval"]["ranked_chunks"] == []
    assert debug["fallback"]["second_pass_retrieval"]["ranked_chunks"] == []


def test_rewrite_excludes_prior_fallback_clarify_turns(monkeypatch):
    monkeypatch.setattr(flow, "OpenAI", _CaptureOpenAI)
    history = [
        {"role": "user", "content": "Customer is off for nonpayment."},
        {
            "role": "assistant",
            "content": "What is the specific customer program or plan?",
            "fallback_final_mode": "clarify",
        },
        {"role": "user", "content": "traditional"},
    ]
    rewritten, debug = flow.rewrite_query_with_history("traditional", history=history)

    assert debug["used"] is True
    assert rewritten == "rewritten-question"
    assert "specific customer program or plan" not in _CaptureResponses.last_user_text
    assert "Customer is off for nonpayment." in _CaptureResponses.last_user_text
    assert "traditional" in _CaptureResponses.last_user_text


def test_loop_guard_prevents_second_clarify_after_short_user_reply(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_id = sorted(chunk_meta.keys())[0]
    search_calls = []
    history = [
        {"role": "user", "content": "An active tradition customer is off for nonpayment."},
        {
            "role": "assistant",
            "content": "What is the specific customer program or plan?",
            "fallback_final_mode": "clarify",
            "message_id": 123,
        },
    ]

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        search_calls.append((query, int(top_k)))
        scored = [(0.84, chunk_id)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(
        flow,
        "_build_clarifying_question",
        lambda **_: (_ for _ in ()).throw(AssertionError("clarifying question should be suppressed by loop guard")),
    )
    _SequencedFakeOpenAI.stream_texts = [
        "I don't know based on the provided context.",
        "I still don't know from the context.",
    ]
    monkeypatch.setattr(flow, "OpenAI", _SequencedFakeOpenAI)

    events = list(
        flow.stream_answer_with_context(
            db,
            "traditional",
            top_k=10,
            max_extracts=2,
            history=history,
        )
    )
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert len(search_calls) == 2
    assert final_text == "from the context."
    assert debug["fallback"]["final_mode"] == "answer"
    assert debug["fallback"]["loop_guard_applied"] is True
    assert debug["fallback"]["clarify_turns_recent"] >= 1
    assert debug["fallback"]["answer_mode"] == "loop_guard_best_effort"


def test_loop_guard_uses_best_effort_when_second_pass_idk_has_no_salvage(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_id = sorted(chunk_meta.keys())[0]
    history = [
        {"role": "user", "content": "An active tradition customer is off for nonpayment."},
        {
            "role": "assistant",
            "content": "What is the specific customer program or plan?",
            "fallback_final_mode": "clarify",
            "message_id": 123,
        },
    ]

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_search(_db, query, top_k=5):
        scored = [(0.84, chunk_id)]
        return scored, _make_debug_payload(query, scored, chunk_meta)

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow, "search_embeddings_with_debug", fake_search)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", lambda _query: None)
    monkeypatch.setattr(
        flow,
        "_build_clarifying_question",
        lambda **_: (_ for _ in ()).throw(AssertionError("clarifying question should be suppressed by loop guard")),
    )
    monkeypatch.setattr(
        flow,
        "_build_best_effort_answer",
        lambda query, context, history=None: "Best effort answer from loop guard.",
    )
    _SequencedFakeOpenAI.stream_texts = [
        "I don't know.",
        "I don't know.",
    ]
    monkeypatch.setattr(flow, "OpenAI", _SequencedFakeOpenAI)

    events = list(
        flow.stream_answer_with_context(
            db,
            "i said traditional",
            top_k=10,
            max_extracts=2,
            history=history,
        )
    )
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert final_text == "Best effort answer from loop guard."
    assert debug["fallback"]["final_mode"] == "answer"
    assert debug["fallback"]["loop_guard_applied"] is True
    assert debug["fallback"]["answer_mode"] == "loop_guard_best_effort"


def test_build_procedure_link_markdown_limits_to_three_and_dedupes():
    sources = [
        {
            "url": "https://connections/?docs=residential%2Falpha",
            "url_canonical": "https://connections/?docs=residential/alpha",
            "has_tab_steps": True,
        },
        {
            "url": "https://connections/?docs=residential/alpha",
            "url_canonical": "https://connections/?docs=residential/alpha",
            "has_tab_steps": True,
        },
        {
            "url": "https://connections/?docs=residential/beta",
            "url_canonical": "https://connections/?docs=residential/beta",
            "has_tab_steps": True,
        },
        {
            "url": "https://connections/?docs=residential/gamma",
            "url_canonical": "https://connections/?docs=residential/gamma",
            "has_tab_steps": True,
        },
        {
            "url": "https://connections/?docs=residential/delta",
            "url_canonical": "https://connections/?docs=residential/delta",
            "has_tab_steps": True,
        },
    ]
    block = flow._build_procedure_link_markdown("Answer", sources)
    lines = [line for line in block.splitlines() if line.startswith("- [")]
    assert len(lines) == 3
    assert "#tab-step" not in block
    assert "docs=residential/delta" not in block


def test_build_procedure_link_markdown_recognizes_tab_step_fragment_without_metadata():
    sources = [
        {
            "url": "https://connections/?docs=residential/alpha#tab-step2",
            "url_canonical": "https://connections/?docs=residential/alpha#tab-step2",
            "has_tab_steps": False,
            "tab_step_count": 0,
        }
    ]
    block = flow._build_procedure_link_markdown("Answer", sources)
    assert "Procedure links:" in block
    assert "(https://connections/?docs=residential/alpha)" in block
    assert "#tab-step" not in block


def test_build_procedure_link_markdown_appends_even_if_url_in_answer():
    source_url = "https://connections/?docs=residential/alpha"
    sources = [
        {
            "url": source_url,
            "url_canonical": source_url,
            "has_tab_steps": True,
        }
    ]
    block = flow._build_procedure_link_markdown(f"See {source_url}", sources)
    assert "Procedure links:" in block
    assert f"({source_url})" in block


def test_stream_cache_hit_appends_procedure_links_even_if_answer_contains_same_url(monkeypatch):
    db, chunk_meta = _seed_flow_db()
    chunk_id = sorted(chunk_meta.keys())[0]
    _set_tab_steps_for_chunk(db, chunk_id, steps=2)

    def fake_rewrite(query, history=None):
        return query, {"used": False, "reason": "no_history"}

    def fake_cache(_query):
        return {
            "id": 55,
            "answer_text": "Cached answer https://connections/?docs=residential/alpha",
            "sources_json": '[{"url":"https://connections/?docs=residential/alpha"}]',
        }

    monkeypatch.setattr(flow, "rewrite_query_with_history", fake_rewrite)
    monkeypatch.setattr(flow.app_db, "get_cache_answer", fake_cache)

    events = list(flow.stream_answer_with_context(db, "Need workflow steps", top_k=10, max_extracts=2))
    debug = next(event["debug"] for event in events if event.get("type") == "debug")
    final_text = "".join(event.get("text", "") for event in events if event.get("type") == "delta")

    assert final_text.startswith("Cached answer")
    assert "Procedure links:" in final_text
    assert "(https://connections/?docs=residential/alpha)" in final_text
    assert "Procedure links:" in debug["llm_response_text"]
    assert debug["cached"] is True


# %%
if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
