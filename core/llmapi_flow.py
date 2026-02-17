from __future__ import annotations

import json
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
    effective_query, _ = rewrite_query_with_history(query, history=history)
    scored, retrieval_debug = search_embeddings_with_debug(db, effective_query, top_k=top_k)
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS)
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

    cached = app_db.get_cache_answer(effective_query)
    if not cached and effective_query != original_query:
        cached = app_db.get_cache_answer(original_query)

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
                "retrieval": None,
                "sources": sources,
                "llm_request": None,
                "llm_response_text": text,
            },
        }
        yield {"type": "sources", "sources": sources}
        yield {"type": "done"}
        return

    scored, retrieval_debug = search_embeddings_with_debug(db, effective_query, top_k=top_k)
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS)
    score_details = retrieval_debug.get("by_chunk_id", {})
    sources = build_source_links(
        db,
        scored,
        max_sources=max_extracts,
        score_details=score_details,
    )
    sources = _hydrate_sources_with_last_scraped(db, sources)

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
                "retrieval": {k: v for k, v in retrieval_debug.items() if k != "by_chunk_id"},
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
            "retrieval": {k: v for k, v in retrieval_debug.items() if k != "by_chunk_id"},
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
