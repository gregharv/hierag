from __future__ import annotations

from .llmapi_flow import (
    _build_llm_prompt,
    _event_get,
    answer_query_with_context as _answer_query_with_context,
    stream_answer_with_context as _stream_answer_with_context,
)
from .llmapi_retrieval import (
    _bm25_scores,
    _expand_query_variants,
    _min_max_normalize,
    _query_embeddings,
    _tokenize_for_bm25,
    _top_indices,
    build_context,
    build_source_links,
    get_parent_extracts,
    refresh_retrieval_cache as _refresh_retrieval_cache,
    search_embeddings,
    search_embeddings_with_debug,
)
from .llmapi_shared import (
    BM25_B,
    BM25_CANDIDATE_K,
    BM25_K1,
    FUSION_ALPHA,
    GLOSSARY_SNIPPETS,
    LLM_MODEL,
    MODEL_NAME,
    RETRIEVAL_DEBUG,
    SYNONYM_GROUPS,
    VECTOR_CANDIDATE_K,
    db,
)


def refresh_retrieval_cache():
    """Compatibility wrapper for retrieval cache refresh."""
    return _refresh_retrieval_cache(db)


def answer_query_with_context(query, top_k=10, max_extracts=6):
    """Compatibility wrapper preserving previous call signature."""
    return _answer_query_with_context(db, query, top_k=top_k, max_extracts=max_extracts)


def stream_answer_with_context(query, top_k=10, max_extracts=6):
    """Compatibility wrapper preserving previous call signature."""
    return _stream_answer_with_context(db, query, top_k=top_k, max_extracts=max_extracts)


__all__ = [
    "BM25_B",
    "BM25_CANDIDATE_K",
    "BM25_K1",
    "FUSION_ALPHA",
    "GLOSSARY_SNIPPETS",
    "LLM_MODEL",
    "MODEL_NAME",
    "RETRIEVAL_DEBUG",
    "SYNONYM_GROUPS",
    "VECTOR_CANDIDATE_K",
    "_bm25_scores",
    "_build_llm_prompt",
    "_event_get",
    "_expand_query_variants",
    "_min_max_normalize",
    "_query_embeddings",
    "_tokenize_for_bm25",
    "_top_indices",
    "answer_query_with_context",
    "build_context",
    "build_source_links",
    "db",
    "get_parent_extracts",
    "refresh_retrieval_cache",
    "search_embeddings",
    "search_embeddings_with_debug",
    "stream_answer_with_context",
]


# %%
if __name__ == "__main__":
    import numpy as np

    assert _tokenize_for_bm25("Hello, World 123!") == ["hello", "world", "123"]
    normalized = _min_max_normalize(np.array([1.0, 3.0], dtype=np.float32))
    assert normalized.tolist() == [0.0, 1.0]
    assert _build_llm_prompt("q", "ctx")[1].startswith("Question:")
    print("Check Passed")
