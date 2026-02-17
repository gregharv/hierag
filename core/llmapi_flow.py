from __future__ import annotations

import json
import re
import time
from typing import Dict, Generator

import httpx
from openai import OpenAI

try:
    from . import service as app_db
    from .fastlite_db import ensure_pipeline_schema, get_scraper_db
    from .llmapi_retrieval import (
        build_context,
        build_source_links,
        get_parent_extracts,
        search_embeddings_with_debug,
    )
    from .llmapi_shared import GLOSSARY_SNIPPETS, LLM_MODEL
except ImportError:
    from core import service as app_db
    from core.fastlite_db import ensure_pipeline_schema, get_scraper_db
    from core.llmapi_retrieval import (
        build_context,
        build_source_links,
        get_parent_extracts,
        search_embeddings_with_debug,
    )
    from core.llmapi_shared import GLOSSARY_SNIPPETS, LLM_MODEL

QUERY_REWRITE_MODEL = "gpt-5-nano"
QUERY_REWRITE_HISTORY_LIMIT = 20
QUERY_GLOSSARY_TERMS = ("myway", "prepay", "prepaid")
RETRIEVAL_ORIGINAL_WEIGHT = 0.65
RETRIEVAL_REWRITTEN_WEIGHT = 0.35
RETRIEVAL_EXPANDED_MIN_TOP_K = 40
RETRIEVAL_EXPANDED_MULTIPLIER = 4


def _hydrate_sources_with_last_scraped(db, sources: list[dict] | None) -> list[dict]:
    if not sources:
        return []

    hydrated: list[dict] = []
    cache: dict[str, str | None] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue

        item = dict(source)
        if item.get("last_scraped"):
            hydrated.append(item)
            continue

        url = str(item.get("url") or "").strip()
        if not url:
            hydrated.append(item)
            continue

        if url not in cache:
            row = list(db.t.pages.rows_where("url=?", [url], limit=1))
            cache[url] = row[0].get("last_scraped") if row else None
        if cache[url]:
            item["last_scraped"] = cache[url]
        hydrated.append(item)

    return hydrated


def _build_llm_prompt(query: str, context: str) -> tuple[str, str]:
    system_text = (
        "Answer the question using only the provided context. "
        "If the answer is not in the context, say you don't know."
    )
    user_text = f"Question: {query}\n\nContext:\n{context}"
    return system_text, user_text


def _normalize_query_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _query_changed(original_query: str, effective_query: str) -> bool:
    return _normalize_query_text(original_query) != _normalize_query_text(effective_query)


def _expanded_retrieval_top_k(top_k: int, max_extracts: int) -> int:
    top_k = int(top_k or 0)
    max_extracts = int(max_extracts or 0)
    return max(top_k, max_extracts * RETRIEVAL_EXPANDED_MULTIPLIER, RETRIEVAL_EXPANDED_MIN_TOP_K)


def _glossary_debug(original_query: str, effective_query: str) -> dict:
    query_text = f"{original_query} {effective_query}".strip().lower()
    trigger_terms = sorted(
        {
            term
            for term in QUERY_GLOSSARY_TERMS
            if re.search(rf"\b{re.escape(term)}\b", query_text)
        }
    )
    included = bool(trigger_terms)
    return {
        "included": included,
        "trigger_terms": trigger_terms,
        "reason": "query_term_match" if included else "no_match",
    }


def _summarize_retrieval_debug(debug_payload: dict | None) -> dict:
    if not isinstance(debug_payload, dict):
        return {}
    return {k: v for k, v in debug_payload.items() if k != "by_chunk_id"}


def _single_retrieval_payload(original_query: str, original_debug: dict) -> dict:
    payload = dict(original_debug or {})
    payload["strategy"] = "single_original"
    payload["merge_weights"] = {"original": 1.0, "rewritten": 0.0}
    payload["query"] = original_query
    payload["original"] = _summarize_retrieval_debug(original_debug)
    payload["rewritten"] = None
    return payload


def _scores_by_chunk(scored_results: list[tuple[float, int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for score, chunk_id in scored_results or []:
        cid = int(chunk_id)
        value = float(score)
        if cid not in scores or value > scores[cid]:
            scores[cid] = value
    return scores


def _merge_retrieval_payload(
    original_query: str,
    effective_query: str,
    original_scored: list[tuple[float, int]],
    original_debug: dict,
    rewritten_scored: list[tuple[float, int]],
    rewritten_debug: dict,
    top_k: int,
) -> tuple[list[tuple[float, int]], dict]:
    original_scores = _scores_by_chunk(original_scored)
    rewritten_scores = _scores_by_chunk(rewritten_scored)
    original_details = (original_debug or {}).get("by_chunk_id", {}) or {}
    rewritten_details = (rewritten_debug or {}).get("by_chunk_id", {}) or {}

    merged_scores: list[tuple[float, int]] = []
    merged_details: dict[int, dict] = {}
    chunk_ids = sorted(set(original_scores.keys()) | set(rewritten_scores.keys()))
    for chunk_id in chunk_ids:
        original_score = float(original_scores.get(chunk_id, 0.0))
        rewritten_score = float(rewritten_scores.get(chunk_id, 0.0))
        merged_score = (RETRIEVAL_ORIGINAL_WEIGHT * original_score) + (
            RETRIEVAL_REWRITTEN_WEIGHT * rewritten_score
        )
        merged_scores.append((merged_score, chunk_id))

        original_component = RETRIEVAL_ORIGINAL_WEIGHT * original_score
        rewritten_component = RETRIEVAL_REWRITTEN_WEIGHT * rewritten_score
        base_detail = original_details.get(chunk_id) if original_component >= rewritten_component else rewritten_details.get(chunk_id)
        if not base_detail:
            base_detail = original_details.get(chunk_id) or rewritten_details.get(chunk_id) or {}

        detail = dict(base_detail)
        detail["score_original"] = original_score if chunk_id in original_scores else None
        detail["score_rewritten"] = rewritten_score if chunk_id in rewritten_scores else None
        detail["score_merged"] = merged_score
        detail["from_original_query"] = chunk_id in original_scores
        detail["from_rewritten_query"] = chunk_id in rewritten_scores
        merged_details[int(chunk_id)] = detail

    merged_scores.sort(key=lambda item: item[0], reverse=True)
    merged_scores = merged_scores[: int(top_k)]

    ranked_chunks = []
    for rank, (score, chunk_id) in enumerate(merged_scores, start=1):
        detail = merged_details.get(int(chunk_id), {})
        ranked_chunks.append(
            {
                "rank": rank,
                "score": float(score),
                "chunk_id": int(chunk_id),
                "extract_id": detail.get("extract_id"),
                "url": detail.get("url"),
                "url_canonical": detail.get("url_canonical"),
                "from_vector": bool(detail.get("from_vector")),
                "from_bm25": bool(detail.get("from_bm25")),
                "vector_score_raw": detail.get("vector_score_raw"),
                "bm25_score_raw": detail.get("bm25_score_raw"),
                "vector_score_norm": detail.get("vector_score_norm"),
                "bm25_score_norm": detail.get("bm25_score_norm"),
                "chunk_preview": detail.get("chunk_preview"),
                "score_original": detail.get("score_original"),
                "score_rewritten": detail.get("score_rewritten"),
                "from_original_query": bool(detail.get("from_original_query")),
                "from_rewritten_query": bool(detail.get("from_rewritten_query")),
            }
        )

    original_summary = _summarize_retrieval_debug(original_debug)
    rewritten_summary = _summarize_retrieval_debug(rewritten_debug)
    original_counts = (original_summary.get("candidate_counts") or {}) if original_summary else {}
    rewritten_counts = (rewritten_summary.get("candidate_counts") or {}) if rewritten_summary else {}
    original_timings = (original_summary.get("timings") or {}) if original_summary else {}
    rewritten_timings = (rewritten_summary.get("timings") or {}) if rewritten_summary else {}

    query_variants = []
    seen_variants = set()
    for summary in (original_summary, rewritten_summary):
        for variant in summary.get("query_variants") or []:
            key = str(variant).strip()
            if not key or key in seen_variants:
                continue
            seen_variants.add(key)
            query_variants.append(key)

    merged_payload = {
        "query": effective_query,
        "query_variants": query_variants,
        "config": dict(original_summary.get("config") or rewritten_summary.get("config") or {}),
        "candidate_counts": {
            "vector": max(
                int(original_counts.get("vector") or 0),
                int(rewritten_counts.get("vector") or 0),
            ),
            "bm25": max(
                int(original_counts.get("bm25") or 0),
                int(rewritten_counts.get("bm25") or 0),
            ),
            "merged": len(chunk_ids),
        },
        "timings": {
            "vector_s": float(original_timings.get("vector_s") or 0.0)
            + float(rewritten_timings.get("vector_s") or 0.0),
            "bm25_s": float(original_timings.get("bm25_s") or 0.0)
            + float(rewritten_timings.get("bm25_s") or 0.0),
            "fusion_s": float(original_timings.get("fusion_s") or 0.0)
            + float(rewritten_timings.get("fusion_s") or 0.0),
            "total_s": float(original_timings.get("total_s") or 0.0)
            + float(rewritten_timings.get("total_s") or 0.0),
        },
        "ranked_chunks": ranked_chunks,
        "by_chunk_id": merged_details,
        "strategy": "dual_merge",
        "merge_weights": {
            "original": RETRIEVAL_ORIGINAL_WEIGHT,
            "rewritten": RETRIEVAL_REWRITTEN_WEIGHT,
        },
        "original": original_summary,
        "rewritten": rewritten_summary,
        "query_original": original_query,
        "query_rewritten": effective_query,
    }
    return merged_scores, merged_payload


def _run_retrieval_strategy(
    db,
    original_query: str,
    effective_query: str,
    retrieval_top_k: int,
) -> tuple[list[tuple[float, int]], dict]:
    original_scored, original_debug = search_embeddings_with_debug(
        db,
        original_query,
        top_k=retrieval_top_k,
    )
    rewrite_changed = _query_changed(original_query, effective_query)
    if not rewrite_changed:
        return original_scored, _single_retrieval_payload(original_query, original_debug)

    try:
        rewritten_scored, rewritten_debug = search_embeddings_with_debug(
            db,
            effective_query,
            top_k=retrieval_top_k,
        )
    except Exception as exc:
        fallback_payload = _single_retrieval_payload(original_query, original_debug)
        fallback_payload["rewrite_retrieval_error"] = str(exc)
        fallback_payload["query_rewritten"] = effective_query
        return original_scored, fallback_payload

    return _merge_retrieval_payload(
        original_query=original_query,
        effective_query=effective_query,
        original_scored=original_scored,
        original_debug=original_debug,
        rewritten_scored=rewritten_scored,
        rewritten_debug=rewritten_debug,
        top_k=retrieval_top_k,
    )


def _clean_history(history: list[dict] | None, limit: int = QUERY_REWRITE_HISTORY_LIMIT) -> list[dict]:
    if not history:
        return []

    cleaned: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})

    if limit <= 0:
        return cleaned
    return cleaned[-limit:]


def rewrite_query_with_history(query: str, history: list[dict] | None = None) -> tuple[str, dict]:
    original = str(query or "").strip()
    if not original:
        return original, {"used": False, "reason": "empty_query"}

    cleaned_history = _clean_history(history)
    if not cleaned_history:
        return original, {"used": False, "reason": "no_history"}

    history_lines = []
    for idx, turn in enumerate(cleaned_history, start=1):
        history_lines.append(f"{idx}. {turn['role']}: {turn['content']}")

    system_text = (
        "Rewrite the latest user question into a standalone question using the conversation history. "
        "Preserve intent and constraints. Do not answer the question. "
        "Return only the rewritten question."
    )
    user_text = (
        "Conversation history:\n"
        + "\n".join(history_lines)
        + f"\n\nLatest user question:\n{original}\n\nRewritten standalone question:"
    )

    client = OpenAI(http_client=httpx.Client(verify=False))
    try:
        response = client.responses.create(
            model=QUERY_REWRITE_MODEL,
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
            ],
        )
    except Exception as exc:
        return original, {
            "used": False,
            "reason": "rewrite_error",
            "error": str(exc),
            "model": QUERY_REWRITE_MODEL,
            "history_turns": len(cleaned_history),
        }

    rewritten = (response.output_text or "").strip()
    if not rewritten:
        return original, {
            "used": False,
            "reason": "empty_rewrite",
            "model": QUERY_REWRITE_MODEL,
            "history_turns": len(cleaned_history),
        }

    return rewritten, {
        "used": True,
        "model": QUERY_REWRITE_MODEL,
        "history_turns": len(cleaned_history),
        "original_query": original,
        "rewritten_query": rewritten,
    }


def answer_query_with_context(db, query, top_k=10, max_extracts=6, history=None):
    """Search, gather context, and ask the LLM to answer."""
    t0 = time.perf_counter()
    original_query = str(query or "").strip()
    effective_query, _ = rewrite_query_with_history(original_query, history=history)
    retrieval_top_k = _expanded_retrieval_top_k(top_k=top_k, max_extracts=max_extracts)
    scored, retrieval_debug = _run_retrieval_strategy(
        db,
        original_query=original_query,
        effective_query=effective_query,
        retrieval_top_k=retrieval_top_k,
    )
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    glossary = _glossary_debug(original_query=original_query, effective_query=effective_query)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS if glossary["included"] else None)
    if not context:
        print("No context available to send to the LLM.")
        return None

    client = OpenAI(http_client=httpx.Client(verify=False))
    t_llm = time.perf_counter()
    system_text, user_text = _build_llm_prompt(effective_query, context)
    response = client.responses.create(
        model=LLM_MODEL,
        reasoning={"effort": "none"},
        text={"verbosity": "low"},
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
    )

    print(f"timing: llm_response {time.perf_counter() - t_llm:.3f}s")
    print(f"timing: total {time.perf_counter() - t0:.3f}s")
    print(response.output_text)
    score_details = retrieval_debug.get("by_chunk_id", {})
    sources = build_source_links(
        db,
        scored,
        max_sources=max_extracts,
        score_details=score_details,
    )
    if sources:
        print("\nSources:")
        for source in sources:
            print(f"- {source['url']}")
    return response.output_text, sources


def _event_get(event, key, default=None):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def stream_answer_with_context(db, query, top_k=10, max_extracts=6, history=None) -> Generator[Dict, None, None]:
    """Stream LLM response as deltas plus sources/debug events."""
    t0 = time.perf_counter()
    original_query = str(query or "").strip()
    effective_query, rewrite_debug = rewrite_query_with_history(original_query, history=history)
    retrieval_top_k = _expanded_retrieval_top_k(top_k=top_k, max_extracts=max_extracts)
    glossary = _glossary_debug(original_query=original_query, effective_query=effective_query)

    cache_lookup_order = [original_query]
    cache_hit_query = None
    cached = app_db.get_cache_answer(original_query)
    if cached:
        cache_hit_query = original_query
    elif _query_changed(original_query, effective_query):
        cache_lookup_order.append(effective_query)
        cached = app_db.get_cache_answer(effective_query)
        if cached:
            cache_hit_query = effective_query

    if cached:
        sources = []
        if cached.get("sources_json"):
            try:
                sources = json.loads(cached["sources_json"])
            except Exception:
                sources = []
        sources = _hydrate_sources_with_last_scraped(db, sources)
        yield {"type": "cache", "cache_id": cached.get("id")}
        text = cached.get("answer_text", "")
        for i in range(0, len(text), 80):
            yield {"type": "delta", "text": text[i : i + 80]}
        yield {
            "type": "debug",
            "debug": {
                "query": original_query,
                "query_effective": effective_query,
                "query_rewritten": (
                    effective_query
                    if rewrite_debug.get("used") and effective_query != original_query
                    else None
                ),
                "query_rewrite": rewrite_debug,
                "cached": True,
                "cache_id": cached.get("id"),
                "cache": {
                    "hit": True,
                    "cache_id": cached.get("id"),
                    "lookup_order": cache_lookup_order,
                    "hit_query": cache_hit_query,
                },
                "glossary": glossary,
                "retrieval": None,
                "sources": sources,
                "llm_request": None,
                "llm_response_text": text,
            },
        }
        yield {"type": "sources", "sources": sources}
        yield {"type": "done"}
        return

    scored, retrieval_debug = _run_retrieval_strategy(
        db,
        original_query=original_query,
        effective_query=effective_query,
        retrieval_top_k=retrieval_top_k,
    )
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS if glossary["included"] else None)
    score_details = retrieval_debug.get("by_chunk_id", {})
    sources = build_source_links(
        db,
        scored,
        max_sources=max_extracts,
        score_details=score_details,
    )
    sources = _hydrate_sources_with_last_scraped(db, sources)
    retrieval_debug_summary = _summarize_retrieval_debug(retrieval_debug)

    if not context:
        yield {
            "type": "delta",
            "text": "I couldn't find relevant context in the embeddings database.",
        }
        yield {
            "type": "debug",
            "debug": {
                "query": original_query,
                "query_effective": effective_query,
                "query_rewritten": (
                    effective_query
                    if rewrite_debug.get("used") and effective_query != original_query
                    else None
                ),
                "query_rewrite": rewrite_debug,
                "cached": False,
                "cache": {
                    "hit": False,
                    "cache_id": None,
                    "lookup_order": cache_lookup_order,
                    "hit_query": None,
                },
                "glossary": glossary,
                "retrieval": retrieval_debug_summary,
                "sources": sources,
                "llm_request": None,
                "llm_response_text": "",
                "error": "No context available",
            },
        }
        yield {"type": "sources", "sources": sources}
        yield {"type": "done"}
        return

    client = OpenAI(http_client=httpx.Client(verify=False))
    t_llm = time.perf_counter()
    system_text, user_text = _build_llm_prompt(effective_query, context)
    stream = client.responses.create(
        model=LLM_MODEL,
        reasoning={"effort": "none"},
        text={"verbosity": "low"},
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
        stream=True,
    )

    response_parts = []
    for event in stream:
        if _event_get(event, "type") == "response.output_text.delta":
            delta = _event_get(event, "delta")
            if delta:
                response_parts.append(delta)
                yield {"type": "delta", "text": delta}

    llm_response_text = "".join(response_parts)
    print(f"timing: llm_stream {time.perf_counter() - t_llm:.3f}s")
    print(f"timing: total {time.perf_counter() - t0:.3f}s")
    yield {
        "type": "debug",
        "debug": {
            "query": original_query,
            "query_effective": effective_query,
            "query_rewritten": (
                effective_query
                if rewrite_debug.get("used") and effective_query != original_query
                else None
            ),
            "query_rewrite": rewrite_debug,
            "cached": False,
            "cache": {
                "hit": False,
                "cache_id": None,
                "lookup_order": cache_lookup_order,
                "hit_query": None,
            },
            "glossary": glossary,
            "retrieval": retrieval_debug_summary,
            "sources": sources,
            "llm_request": {
                "model": LLM_MODEL,
                "system_text": system_text,
                "user_text": user_text,
            },
            "llm_response_text": llm_response_text,
        },
    }
    yield {"type": "sources", "sources": sources}
    yield {"type": "done"}


# %%
if __name__ == "__main__":
    test_db = get_scraper_db(":memory:")
    ensure_pipeline_schema(test_db)
    test_db.t.sites.insert(
        id=1,
        root_url="https://example.com",
        selector="body",
        breadcrumb_selector="body",
        split_function="split_md_sections",
        name="example",
    )
    page = test_db.t.pages.insert(
        site_id=1,
        url="https://example.com/doc",
        html="<div>hello world</div>",
        content_hash="hash",
        last_scraped="now",
        last_changed="now",
    )
    extract = test_db.t.extracts.insert(page_id=page["id"], extract_index=0, text="extract text")
    chunk = test_db.t.chunks.insert(extract_id=extract["id"], chunk_index=0, text="hello world")
    assert _build_llm_prompt("q", "ctx")[0].startswith("Answer the question")
    assert _event_get({"type": "x"}, "type") == "x"
    hydrated = _hydrate_sources_with_last_scraped(test_db, [{"url": "https://example.com/doc"}])
    assert hydrated and hydrated[0]["last_scraped"] == "now"
    assert page["id"] and extract["id"] and chunk["id"]
    print("Check Passed")
