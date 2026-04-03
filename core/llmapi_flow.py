from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Generator
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import httpx
from openai import OpenAI

try:
    from . import service as app_db
    from .fastlite_db import ensure_pipeline_schema, get_scraper_db
    from .llmapi_retrieval import (
        build_context,
        build_source_links,
        canonicalize_source_url,
        extract_tab_step_anchors,
        get_parent_extracts,
        search_embeddings_with_debug,
    )
    from .llmapi_shared import (
        GLOSSARY_SNIPPETS,
        LLM_MODEL,
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
    )
except ImportError:
    from core import service as app_db
    from core.fastlite_db import ensure_pipeline_schema, get_scraper_db
    from core.llmapi_retrieval import (
        build_context,
        build_source_links,
        canonicalize_source_url,
        extract_tab_step_anchors,
        get_parent_extracts,
        search_embeddings_with_debug,
    )
    from core.llmapi_shared import (
        GLOSSARY_SNIPPETS,
        LLM_MODEL,
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
    )

QUERY_REWRITE_MODEL = "gpt-5-nano"
QUERY_REWRITE_HISTORY_LIMIT = 20
QUERY_GLOSSARY_TERMS = ("myway", "prepay", "prepaid")
RETRIEVAL_ORIGINAL_WEIGHT = 0.65
RETRIEVAL_REWRITTEN_WEIGHT = 0.35
RETRIEVAL_EXPANDED_MIN_TOP_K = 40
RETRIEVAL_EXPANDED_MULTIPLIER = 4
FALLBACK_RETRY_MIN_TOP_K = 120
FALLBACK_RETRY_MIN_MAX_EXTRACTS = 10
FALLBACK_PREFIX_DECISION_CHARS = 48
FALLBACK_CLARIFY_HISTORY_LIMIT = 8
FALLBACK_LOOP_GUARD_HISTORY_WINDOW = 8
PROMPT_ALLOWED_SOURCE_LINKS_MAX = 6
OFF_TOPIC_NO_SOURCES_REPLY = (
    "I could not find relevant Connections sources for that question. "
    "Ask about Connections policies, programs, or customer-service workflows."
)
BEST_EFFORT_FALLBACK_REPLY = (
    "I could not determine a reliable answer from the retrieved Connections sources. "
    "Please restate the question with the specific policy, program, customer type, or workflow step."
)
_IDK_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"i(?:\s+[a-z]+){0,3}\s+(?:do\s*not|don[\W_]*t)\s+know"
    r"|i\s+(?:can\s*not|cannot|could\s+not)\s+(?:find|answer)"
    r"|the\s+provided\s+context\s+(?:does\s+not|doesn[\W_]*t)\s+(?:describe|include|contain)"
    r"|based\s+on\s+the\s+provided\s+context,\s*i\s+(?:do\s*not|don[\W_]*t)\s+know"
    r")\b",
    re.IGNORECASE,
)
_IDK_LEADING_SENTENCE_RE = re.compile(
    r"^\s*(?:"
    r"i(?:\s+[a-z]+){0,3}\s+(?:do\s*not|don[\W_]*t)\s+know"
    r"|based\s+on\s+the\s+provided\s+context(?:,\s*)?(?:i(?:\s+[a-z]+){0,3}\s+)?(?:do\s*not|don[\W_]*t)\s+know"
    r"|the\s+provided\s+context\s+(?:does\s+not|doesn[\W_]*t)\s+(?:describe|include|contain)"
    r")\s*[:\-\u2014,]*\s*",
    re.IGNORECASE,
)
_SHORT_CLARIFY_REPLY_PREFIX_RE = re.compile(r"^\s*i\s+said\b", re.IGNORECASE)
_TAB_STEP_FRAGMENT_RE = re.compile(r"#tab-step(\d+)\b", re.IGNORECASE)


def _fallback_debug_payload() -> dict:
    return {
        "triggered": False,
        "reason": None,
        "final_mode": "answer",
        "first_pass_retrieval": None,
        "second_pass_retrieval": None,
        "retry_config": None,
        "loop_guard_applied": False,
        "clarify_turns_recent": 0,
        "answer_mode": None,
    }


def _retry_retrieval_config(retrieval_top_k: int, max_extracts: int) -> dict[str, int]:
    return {
        "top_k": max(int(retrieval_top_k or 0) * 2, FALLBACK_RETRY_MIN_TOP_K),
        "max_extracts": max(int(max_extracts or 0) * 2, FALLBACK_RETRY_MIN_MAX_EXTRACTS),
    }


def _is_idk_like_response(text: str) -> bool:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return False
    return bool(_IDK_PREFIX_RE.match(cleaned))


def _deterministic_clarifying_question(original_query: str) -> str:
    cleaned = " ".join(str(original_query or "").split()).replace('"', "'")
    if cleaned:
        if len(cleaned) > 120:
            cleaned = cleaned[:120].rstrip()
        return (
            f'Could you clarify which specific policy or workflow you mean for "{cleaned}", '
            "including customer type, program, and current service status?"
        )
    return (
        "Could you clarify which specific policy or workflow you need, including customer type, "
        "program, and current service status?"
    )


def _latest_user_reply_is_short_clarification(original_query: str) -> bool:
    value = " ".join(str(original_query or "").strip().split())
    if not value:
        return False
    if _SHORT_CLARIFY_REPLY_PREFIX_RE.match(value):
        return True
    return len(value.split()) <= 4


def _strip_idk_prefix(text: str) -> str:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return ""
    stripped = _IDK_LEADING_SENTENCE_RE.sub("", value, count=1).strip()
    if not stripped:
        return ""
    stripped = stripped.lstrip(" .,:;-\u2014")
    return stripped.strip()


def _is_low_signal_retrieval(retrieval_summary: dict | None) -> bool:
    if not isinstance(retrieval_summary, dict):
        return False
    gate = retrieval_summary.get("low_signal_gate")
    return isinstance(gate, dict) and bool(gate.get("triggered"))


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return float(default)


def _coerce_non_negative_int(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return parsed if parsed >= 0 else default


def _source_passes_source_score_gate(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    vector_raw = _coerce_float(source.get("vector_score_raw"))
    bm25_raw = _coerce_float(source.get("bm25_score_raw"))
    return (vector_raw > SOURCE_MIN_VECTOR_SCORE_RAW) or (bm25_raw > SOURCE_MIN_BM25_SCORE_RAW)


def _source_has_tab_step_fragment(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    for key in ("url", "url_canonical"):
        raw = str(source.get(key) or "").strip()
        if raw and _TAB_STEP_FRAGMENT_RE.search(raw):
            return True
    return False


def _source_is_procedure(source: dict | None) -> bool:
    if not isinstance(source, dict):
        return False
    return bool(source.get("has_tab_steps")) or _source_has_tab_step_fragment(source)


def _source_is_procedure_link_eligible(source: dict | None) -> bool:
    if not _source_is_procedure(source):
        return False
    if isinstance(source, dict) and "procedure_link_eligible" in source:
        return bool(source.get("procedure_link_eligible"))
    return _source_passes_source_score_gate(source)


def _hydrate_sources_with_last_scraped(db, sources: list[dict] | None) -> list[dict]:
    if not sources:
        return []

    hydrated: list[dict] = []
    cache: dict[str, dict | None] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue

        item = dict(source)
        item["source_score_eligible"] = bool(_source_passes_source_score_gate(item))
        if not item["source_score_eligible"]:
            continue
        url = str(item.get("url") or item.get("url_canonical") or "").strip()
        if not url:
            hydrated.append(item)
            continue

        if url not in cache:
            row = list(db.t.pages.rows_where("url=?", [url], limit=1))
            cache[url] = row[0] if row else None

        page_row = cache[url]
        has_tab_steps = bool(item.get("has_tab_steps")) or _source_has_tab_step_fragment(item)
        tab_step_count = _coerce_non_negative_int(item.get("tab_step_count"), default=0)

        if page_row:
            if not item.get("last_scraped") and page_row.get("last_scraped"):
                item["last_scraped"] = page_row.get("last_scraped")
            tab_step_anchors = extract_tab_step_anchors(page_row.get("html") or "")
            if tab_step_anchors:
                has_tab_steps = True
                tab_step_count = max(tab_step_count, len(tab_step_anchors))

        if has_tab_steps and tab_step_count <= 0:
            tab_step_count = 1
        item["has_tab_steps"] = bool(has_tab_steps)
        item["tab_step_count"] = int(tab_step_count)
        item["procedure_link_eligible"] = bool(_source_is_procedure(item)) and bool(item["source_score_eligible"])
        hydrated.append(item)

    return hydrated


def _source_label_from_url(source: dict) -> str:
    raw_url = str(source.get("url") or source.get("url_canonical") or "").strip()
    if not raw_url:
        return "procedure"
    try:
        parsed = urlsplit(raw_url)
    except Exception:
        return "procedure"

    docs_values = parse_qs(parsed.query).get("docs") or []
    docs_path = ""
    if docs_values:
        docs_path = unquote(str(docs_values[0] or "")).strip().strip("/")
    if docs_path:
        parts = [part for part in docs_path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[-2]} / {parts[-1]}"
        return parts[0]
    path = unquote(parsed.path or "").strip().strip("/")
    if path:
        parts = [part for part in path.split("/") if part]
        return " / ".join(parts[-2:]) if len(parts) >= 2 else parts[0]
    return (parsed.netloc or "procedure").strip() or "procedure"


def _strip_url_fragment(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except Exception:
        return value
    return urlunsplit(parsed._replace(fragment="")) or value


def _build_allowed_source_links_for_prompt(sources: list[dict] | None) -> list[dict[str, str]]:
    seen_urls: set[str] = set()
    links: list[dict[str, str]] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        if "source_score_eligible" in source and not bool(source.get("source_score_eligible")):
            continue
        if "source_score_eligible" not in source and not _source_passes_source_score_gate(source):
            continue

        base_url = str(source.get("url") or source.get("url_canonical") or "").strip()
        if not base_url:
            continue

        canonical = canonicalize_source_url(str(source.get("url_canonical") or base_url))
        canonical_key = canonical or base_url
        if canonical_key in seen_urls:
            continue
        seen_urls.add(canonical_key)

        link_url = _strip_url_fragment(canonical or base_url)
        if not link_url:
            continue
        label = _source_label_from_url(source)
        links.append({"label": label, "url": link_url})
        if len(links) >= PROMPT_ALLOWED_SOURCE_LINKS_MAX:
            break
    return links


def _build_llm_prompt(
    query: str,
    context: str,
    *,
    procedure_mode: bool = False,
    sources: list[dict] | None = None,
) -> tuple[str, str]:
    _ = procedure_mode
    system_text = (
        "Answer the question using only the provided context. "
        "If helpful, you may include markdown hyperlinks in the form [text](url), "
        "but only with URLs listed under Allowed source links. "
        "Do not include a separate links section. "
        "If the answer is not in the context, say you don't know."
    )
    user_lines = [f"Question: {query}", "", "Context:", context]
    allowed_links = _build_allowed_source_links_for_prompt(sources)
    if allowed_links:
        user_lines.extend(["", "Allowed source links:"])
        for item in allowed_links:
            user_lines.append(f"- {item['label']}: {item['url']}")
    else:
        user_lines.extend(["", "Allowed source links: none"])
    user_lines.extend(
        [
            "",
            "If a link is useful, use markdown [text](url) and only one of the allowed source URLs above.",
            "Do not add a separate links section.",
        ]
    )
    user_text = "\n".join(user_lines)
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


def _run_retrieval_pass(
    db,
    *,
    original_query: str,
    effective_query: str,
    retrieval_top_k: int,
    max_extracts: int,
    glossary_included: bool,
) -> dict:
    scored, retrieval_debug = _run_retrieval_strategy(
        db,
        original_query=original_query,
        effective_query=effective_query,
        retrieval_top_k=retrieval_top_k,
    )
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS if glossary_included else None)
    score_details = retrieval_debug.get("by_chunk_id", {}) if isinstance(retrieval_debug, dict) else {}
    sources = build_source_links(
        db,
        scored,
        max_sources=max_extracts,
        score_details=score_details,
    )
    sources = _hydrate_sources_with_last_scraped(db, sources)
    return {
        "scored": scored,
        "retrieval_debug": retrieval_debug,
        "retrieval_summary": _summarize_retrieval_debug(retrieval_debug),
        "context": context,
        "sources": sources,
    }


def _clean_history(
    history: list[dict] | None,
    limit: int = QUERY_REWRITE_HISTORY_LIMIT,
    *,
    exclude_fallback_clarify_assistant: bool = False,
) -> list[dict]:
    if not history:
        return []

    cleaned: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        fallback_final_mode = str(item.get("fallback_final_mode") or "").strip().lower()
        if exclude_fallback_clarify_assistant and role == "assistant" and fallback_final_mode == "clarify":
            continue
        cleaned_item: dict[str, Any] = {"role": role, "content": content}
        if "fallback_final_mode" in item:
            cleaned_item["fallback_final_mode"] = item.get("fallback_final_mode")
        if "message_id" in item:
            cleaned_item["message_id"] = item.get("message_id")
        cleaned.append(cleaned_item)

    if limit <= 0:
        return cleaned
    return cleaned[-limit:]


def _recent_clarify_turns(history: list[dict] | None, window: int = FALLBACK_LOOP_GUARD_HISTORY_WINDOW) -> int:
    cleaned = _clean_history(history, limit=max(int(window), 1))
    clarify_count = 0
    for item in cleaned:
        if str(item.get("role", "")).lower() != "assistant":
            continue
        if str(item.get("fallback_final_mode") or "").strip().lower() == "clarify":
            clarify_count += 1
    return clarify_count


def _build_best_effort_answer(query: str, context: str, history: list[dict] | None = None) -> str:
    fallback = BEST_EFFORT_FALLBACK_REPLY
    context_text = str(context or "").strip()
    if not context_text:
        return fallback

    cleaned_history = _clean_history(
        history,
        limit=FALLBACK_CLARIFY_HISTORY_LIMIT,
        exclude_fallback_clarify_assistant=True,
    )
    history_lines = [f"{idx}. {turn['role']}: {turn['content']}" for idx, turn in enumerate(cleaned_history, start=1)]
    system_text = (
        "You are a customer-support assistant. Answer using only the provided context. "
        "Do not ask follow-up questions. Do not begin with 'I don't know'. "
        "If context is incomplete, state brief assumptions and provide the most actionable steps."
    )
    user_lines = [f"Question: {query}", "Context:", context_text]
    if history_lines:
        user_lines.extend(["Recent history:"] + history_lines)
    user_text = "\n".join(user_lines)

    client = OpenAI(http_client=httpx.Client(verify=False))
    try:
        response = client.responses.create(
            model=LLM_MODEL,
            reasoning={"effort": "minimal"},
            text={"verbosity": "low"},
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_text}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
            ],
        )
    except Exception:
        return fallback

    answer = " ".join(str(response.output_text or "").split()).strip()
    if not answer:
        return fallback
    sanitized = _strip_idk_prefix(answer)
    if sanitized:
        answer = sanitized
    if answer.endswith("?"):
        return fallback
    return answer


def _build_clarifying_question(
    original_query: str,
    effective_query: str,
    history: list[dict] | None = None,
    sources: list[dict] | None = None,
) -> str:
    fallback = _deterministic_clarifying_question(original_query)
    cleaned_history = _clean_history(
        history,
        limit=FALLBACK_CLARIFY_HISTORY_LIMIT,
        exclude_fallback_clarify_assistant=True,
    )
    history_lines = [f"{idx}. {turn['role']}: {turn['content']}" for idx, turn in enumerate(cleaned_history, start=1)]

    source_urls = []
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("url_canonical") or "").strip()
        if url:
            source_urls.append(url)
        if len(source_urls) >= 3:
            break

    system_text = (
        "You generate one concise clarifying question to disambiguate a retrieval request. "
        "Return exactly one question and do not answer it."
    )
    user_lines = [
        f"Original question: {original_query}",
        f"Effective retrieval question: {effective_query}",
    ]
    if history_lines:
        user_lines.append("Recent history:")
        user_lines.extend(history_lines)
    if source_urls:
        user_lines.append("Top retrieved sources:")
        user_lines.extend(f"- {url}" for url in source_urls)
    user_lines.append(
        "Ask one short follow-up question that will most improve retrieval specificity "
        "(scope, customer type, program, or workflow step)."
    )
    user_text = "\n".join(user_lines)

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
    except Exception:
        return fallback

    candidate = " ".join(str(response.output_text or "").split())
    if not candidate:
        return fallback
    question = candidate.splitlines()[0].strip()
    if "?" in question:
        question = question[: question.find("?") + 1]
    else:
        question = question.rstrip(" .!") + "?"
    if len(question) > 220:
        question = question[:220].rstrip(" ,;:.!?") + "?"
    return question or fallback


def _fallback_retry_failure_reply(
    *,
    original_query: str,
    effective_query: str,
    history: list[dict] | None,
    sources: list[dict] | None,
    retrieval_summary: dict | None,
    best_effort_context: str,
    fallback_debug: dict,
) -> str:
    has_sources = any(
        isinstance(item, dict) and str(item.get("url") or item.get("url_canonical") or "").strip()
        for item in (sources or [])
    )
    if not has_sources:
        fallback_debug["final_mode"] = "out_of_scope"
        fallback_debug["answer_mode"] = "off_topic_no_sources"
        return OFF_TOPIC_NO_SOURCES_REPLY
    if _is_low_signal_retrieval(retrieval_summary):
        fallback_debug["final_mode"] = "clarify"
        fallback_debug["answer_mode"] = "clarify_low_signal"
        return _build_clarifying_question(
            original_query=original_query,
            effective_query=effective_query,
            history=history,
            sources=sources,
        )
    fallback_debug["final_mode"] = "answer"
    fallback_debug["answer_mode"] = "best_effort_high_signal"
    return _build_best_effort_answer(
        query=effective_query,
        context=best_effort_context,
        history=history,
    )


def rewrite_query_with_history(query: str, history: list[dict] | None = None) -> tuple[str, dict]:
    original = str(query or "").strip()
    if not original:
        return original, {"used": False, "reason": "empty_query"}

    cleaned_history = _clean_history(
        history,
        exclude_fallback_clarify_assistant=True,
    )
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
    """Compatibility wrapper that collects text and sources from the streaming path."""
    text_parts: list[str] = []
    sources: list[dict] = []
    for event in stream_answer_with_context(
        db,
        query,
        top_k=top_k,
        max_extracts=max_extracts,
        history=history,
    ):
        etype = event.get("type")
        if etype == "delta":
            text_parts.append(str(event.get("text", "")))
        elif etype == "sources":
            sources = list(event.get("sources", []))
        elif etype == "error":
            print(str(event.get("error", "Unknown error")))

    text = "".join(text_parts).strip()
    if not text:
        return None
    print(text)
    if sources:
        print("\nSources:")
        for source in sources:
            print(f"- {source.get('url')}")
    return text, sources


def _event_get(event, key, default=None):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def _stream_llm_with_prefix_guard(client: OpenAI, system_text: str, user_text: str) -> Generator[Dict, None, dict]:
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

    response_parts: list[str] = []
    buffered: list[str] = []
    buffered_text = ""
    decided = False
    idk_prefix = False

    try:
        for event in stream:
            if _event_get(event, "type") != "response.output_text.delta":
                continue
            delta = _event_get(event, "delta")
            if not delta:
                continue

            response_parts.append(delta)
            if decided:
                yield {"type": "delta", "text": delta}
                continue

            buffered.append(delta)
            buffered_text += delta
            prefix = buffered_text.lstrip()
            if prefix and _is_idk_like_response(prefix):
                idk_prefix = True
                decided = True
                break
            if len(prefix) >= FALLBACK_PREFIX_DECISION_CHARS:
                decided = True
                for part in buffered:
                    yield {"type": "delta", "text": part}
                buffered = []

        if not decided and not idk_prefix:
            prefix = buffered_text.lstrip()
            if prefix and _is_idk_like_response(prefix):
                idk_prefix = True
            else:
                for part in buffered:
                    yield {"type": "delta", "text": part}
                buffered = []
    finally:
        closer = getattr(stream, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    return {"text": "".join(response_parts), "idk_prefix": idk_prefix}


def stream_answer_with_context(db, query, top_k=10, max_extracts=6, history=None) -> Generator[Dict, None, None]:
    """Stream LLM response as deltas plus sources/debug events."""
    t0 = time.perf_counter()
    original_query = str(query or "").strip()
    effective_query, rewrite_debug = rewrite_query_with_history(original_query, history=history)
    retrieval_top_k = _expanded_retrieval_top_k(top_k=top_k, max_extracts=max_extracts)
    glossary = _glossary_debug(original_query=original_query, effective_query=effective_query)
    fallback_debug = _fallback_debug_payload()
    clarify_turns_recent = _recent_clarify_turns(history, window=FALLBACK_LOOP_GUARD_HISTORY_WINDOW)
    fallback_debug["clarify_turns_recent"] = clarify_turns_recent
    loop_guard_candidate = clarify_turns_recent >= 1 and _latest_user_reply_is_short_clarification(original_query)

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
        text = str(cached.get("answer_text", "") or "")
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
                "fallback": fallback_debug,
            },
        }
        yield {"type": "sources", "sources": sources}
        yield {"type": "done"}
        return

    first_pass = _run_retrieval_pass(
        db,
        original_query=original_query,
        effective_query=effective_query,
        retrieval_top_k=retrieval_top_k,
        max_extracts=max_extracts,
        glossary_included=bool(glossary["included"]),
    )
    fallback_debug["first_pass_retrieval"] = first_pass["retrieval_summary"]

    active_pass = first_pass
    llm_request = None
    llm_response_text = ""
    llm_guard_result = {"idk_prefix": False, "text": ""}
    first_pass_low_signal = _is_low_signal_retrieval(first_pass["retrieval_summary"])

    client = None

    def _run_stream_attempt(context_text: str, *, sources: list[dict] | None) -> tuple[dict, dict]:
        nonlocal client
        if client is None:
            client = OpenAI(http_client=httpx.Client(verify=False))
        system_text_local, user_text_local = _build_llm_prompt(
            effective_query,
            context_text,
            sources=sources,
        )
        request_payload = {
            "model": LLM_MODEL,
            "system_text": system_text_local,
            "user_text": user_text_local,
        }
        attempt = _stream_llm_with_prefix_guard(
            client,
            system_text=system_text_local,
            user_text=user_text_local,
        )
        t_llm = time.perf_counter()
        result = {"idk_prefix": False, "text": ""}
        while True:
            try:
                event = next(attempt)
            except StopIteration as stop:
                result = stop.value or {"idk_prefix": False, "text": ""}
                break
            else:
                yield event
        print(f"timing: llm_stream_attempt {time.perf_counter() - t_llm:.3f}s")
        return request_payload, result

    if not first_pass["context"] and first_pass_low_signal:
        fallback_debug["triggered"] = True
        fallback_debug["reason"] = "low_signal_off_topic"
        fallback_debug["final_mode"] = "out_of_scope"
        fallback_debug["answer_mode"] = "off_topic_no_sources"
        llm_response_text = OFF_TOPIC_NO_SOURCES_REPLY
        yield {"type": "delta", "text": llm_response_text}
    elif not first_pass["context"]:
        fallback_debug["triggered"] = True
        fallback_debug["reason"] = "no_context"
        retry_config = _retry_retrieval_config(retrieval_top_k, max_extracts)
        fallback_debug["retry_config"] = retry_config
        second_pass = _run_retrieval_pass(
            db,
            original_query=original_query,
            effective_query=effective_query,
            retrieval_top_k=retry_config["top_k"],
            max_extracts=retry_config["max_extracts"],
            glossary_included=bool(glossary["included"]),
        )
        fallback_debug["second_pass_retrieval"] = second_pass["retrieval_summary"]
        active_pass = second_pass
        if second_pass["context"]:
            stream_attempt = _run_stream_attempt(
                second_pass["context"],
                sources=second_pass["sources"],
            )
            while True:
                try:
                    event = next(stream_attempt)
                except StopIteration as stop:
                    llm_request, llm_guard_result = stop.value or (None, {"idk_prefix": False, "text": ""})
                    break
                else:
                    yield event
            llm_response_text = str(llm_guard_result.get("text", ""))
            if llm_guard_result.get("idk_prefix"):
                fallback_debug["reason"] = "retry_still_insufficient"
                if loop_guard_candidate:
                    fallback_debug["loop_guard_applied"] = True
                    fallback_debug["final_mode"] = "answer"
                    fallback_debug["answer_mode"] = "loop_guard_best_effort"
                    stripped = _strip_idk_prefix(llm_response_text)
                    if stripped and not stripped.endswith("?"):
                        llm_response_text = stripped
                    else:
                        llm_request = None
                        llm_response_text = _build_best_effort_answer(
                            query=effective_query,
                            context=second_pass["context"],
                            history=history,
                        )
                else:
                    llm_request = None
                    llm_response_text = _fallback_retry_failure_reply(
                        original_query=original_query,
                        effective_query=effective_query,
                        history=history,
                        sources=second_pass["sources"],
                        retrieval_summary=second_pass["retrieval_summary"],
                        best_effort_context=second_pass["context"],
                        fallback_debug=fallback_debug,
                    )
                yield {"type": "delta", "text": llm_response_text}
            else:
                fallback_debug["answer_mode"] = "normal"
        else:
            fallback_debug["reason"] = "retry_still_insufficient"
            if loop_guard_candidate:
                fallback_debug["loop_guard_applied"] = True
                fallback_debug["final_mode"] = "answer"
                fallback_debug["answer_mode"] = "loop_guard_best_effort"
                llm_response_text = _build_best_effort_answer(
                    query=effective_query,
                    context=first_pass["context"] or "",
                    history=history,
                )
            else:
                llm_response_text = _fallback_retry_failure_reply(
                    original_query=original_query,
                    effective_query=effective_query,
                    history=history,
                    sources=second_pass["sources"],
                    retrieval_summary=second_pass["retrieval_summary"],
                    best_effort_context=first_pass["context"] or "",
                    fallback_debug=fallback_debug,
                )
            yield {"type": "delta", "text": llm_response_text}
    else:
        stream_attempt = _run_stream_attempt(
            first_pass["context"],
            sources=first_pass["sources"],
        )
        while True:
            try:
                event = next(stream_attempt)
            except StopIteration as stop:
                llm_request, llm_guard_result = stop.value or (None, {"idk_prefix": False, "text": ""})
                break
            else:
                yield event
        llm_response_text = str(llm_guard_result.get("text", ""))
        if llm_guard_result.get("idk_prefix"):
            fallback_debug["triggered"] = True
            fallback_debug["reason"] = "idk_prefix"
            retry_config = _retry_retrieval_config(retrieval_top_k, max_extracts)
            fallback_debug["retry_config"] = retry_config
            second_pass = _run_retrieval_pass(
                db,
                original_query=original_query,
                effective_query=effective_query,
                retrieval_top_k=retry_config["top_k"],
                max_extracts=retry_config["max_extracts"],
                glossary_included=bool(glossary["included"]),
            )
            fallback_debug["second_pass_retrieval"] = second_pass["retrieval_summary"]
            active_pass = second_pass
            if second_pass["context"]:
                stream_attempt_retry = _run_stream_attempt(
                    second_pass["context"],
                    sources=second_pass["sources"],
                )
                while True:
                    try:
                        event = next(stream_attempt_retry)
                    except StopIteration as stop:
                        llm_request, llm_guard_result = stop.value or (None, {"idk_prefix": False, "text": ""})
                        break
                    else:
                        yield event
                llm_response_text = str(llm_guard_result.get("text", ""))
                if llm_guard_result.get("idk_prefix"):
                    fallback_debug["reason"] = "retry_still_insufficient"
                    if loop_guard_candidate:
                        fallback_debug["loop_guard_applied"] = True
                        fallback_debug["final_mode"] = "answer"
                        fallback_debug["answer_mode"] = "loop_guard_best_effort"
                        stripped = _strip_idk_prefix(llm_response_text)
                        if stripped and not stripped.endswith("?"):
                            llm_response_text = stripped
                        else:
                            llm_request = None
                            llm_response_text = _build_best_effort_answer(
                                query=effective_query,
                                context=second_pass["context"],
                                history=history,
                            )
                    else:
                        llm_request = None
                        llm_response_text = _fallback_retry_failure_reply(
                            original_query=original_query,
                            effective_query=effective_query,
                            history=history,
                            sources=second_pass["sources"],
                            retrieval_summary=second_pass["retrieval_summary"],
                            best_effort_context=second_pass["context"],
                            fallback_debug=fallback_debug,
                        )
                    yield {"type": "delta", "text": llm_response_text}
                else:
                    fallback_debug["answer_mode"] = "normal"
            else:
                fallback_debug["reason"] = "retry_still_insufficient"
                if loop_guard_candidate:
                    fallback_debug["loop_guard_applied"] = True
                    fallback_debug["final_mode"] = "answer"
                    fallback_debug["answer_mode"] = "loop_guard_best_effort"
                    llm_request = None
                    llm_response_text = _build_best_effort_answer(
                        query=effective_query,
                        context=first_pass["context"] or "",
                        history=history,
                    )
                else:
                    llm_request = None
                    llm_response_text = _fallback_retry_failure_reply(
                        original_query=original_query,
                        effective_query=effective_query,
                        history=history,
                        sources=second_pass["sources"],
                        retrieval_summary=second_pass["retrieval_summary"],
                        best_effort_context=first_pass["context"] or "",
                        fallback_debug=fallback_debug,
                    )
                yield {"type": "delta", "text": llm_response_text}
        elif fallback_debug["triggered"]:
            fallback_debug["answer_mode"] = "normal"

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
            "retrieval": active_pass["retrieval_summary"],
            "sources": active_pass["sources"],
            "llm_request": llm_request,
            "llm_response_text": llm_response_text,
            "fallback": fallback_debug,
        },
    }
    yield {"type": "sources", "sources": active_pass["sources"]}
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
    prompt_system, prompt_user = _build_llm_prompt(
        "q",
        "ctx",
        procedure_mode=True,
        sources=[{"url": "https://example.com/doc", "vector_score_raw": 0.9}],
    )
    assert prompt_system.startswith("Answer the question")
    assert "Keep it extremely brief" not in prompt_system
    assert "Allowed source links:" in prompt_user
    assert "https://example.com/doc" in prompt_user
    assert _event_get({"type": "x"}, "type") == "x"
    assert _is_idk_like_response("I don't know based on the provided context.")
    assert not _is_idk_like_response("Here is what I found in the context.")
    retry_cfg = _retry_retrieval_config(40, 6)
    assert retry_cfg == {"top_k": 120, "max_extracts": 12}
    test_db.t.pages.update({"id": page["id"], "html": '<a href="#tab-step1">Step 1</a>'})
    hydrated = _hydrate_sources_with_last_scraped(
        test_db,
        [{"url": "https://example.com/doc", "vector_score_raw": 0.9}],
    )
    assert hydrated and hydrated[0]["last_scraped"] == "now"
    assert hydrated[0]["has_tab_steps"] is True
    allowed_links = _build_allowed_source_links_for_prompt(hydrated)
    assert allowed_links and allowed_links[0]["url"] == "https://example.com/doc"
    fallback_debug = _fallback_debug_payload()
    no_sources_reply = _fallback_retry_failure_reply(
        original_query="q",
        effective_query="q",
        history=None,
        sources=[],
        retrieval_summary=None,
        best_effort_context="",
        fallback_debug=fallback_debug,
    )
    assert no_sources_reply == OFF_TOPIC_NO_SOURCES_REPLY
    assert fallback_debug["final_mode"] == "out_of_scope"
    assert page["id"] and extract["id"] and chunk["id"]
    print("Check Passed")
