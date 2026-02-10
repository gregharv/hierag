from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Sequence

import numpy as np

try:
    from .llmapi_shared import (
        BM25_B,
        BM25_CANDIDATE_K,
        BM25_K1,
        FUSION_ALPHA,
        RETRIEVAL_DEBUG,
        SYNONYM_GROUPS,
        VECTOR_CANDIDATE_K,
        get_model,
    )
    from .fastlite_db import ensure_pipeline_schema, get_scraper_db
except ImportError:
    from core.llmapi_shared import (
        BM25_B,
        BM25_CANDIDATE_K,
        BM25_K1,
        FUSION_ALPHA,
        RETRIEVAL_DEBUG,
        SYNONYM_GROUPS,
        VECTOR_CANDIDATE_K,
        get_model,
    )
    from core.fastlite_db import ensure_pipeline_schema, get_scraper_db

_RETRIEVAL_CACHE = None
_RETRIEVAL_CACHE_LOCK = threading.Lock()


def _expand_query_variants(query: str) -> List[str]:
    """Return query variants by swapping known synonym aliases."""
    variants = {query}
    for group in SYNONYM_GROUPS:
        aliases = group.get("aliases", [])
        if not aliases:
            continue
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, aliases)) + r")\b", re.IGNORECASE)
        next_variants = set()
        for value in variants:
            if pattern.search(value):
                for alias in aliases:
                    next_variants.add(pattern.sub(alias, value))
            else:
                next_variants.add(value)
        variants = next_variants
    return list(variants)


def _query_embeddings(queries: Iterable[str]) -> np.ndarray:
    model = get_model()
    return model.encode(
        list(queries),
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def _tokenize_for_bm25(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _build_retrieval_cache(db):
    embeddings = list(db.t.embeddings())
    if not embeddings:
        return None

    chunk_ids = []
    chunk_texts = []
    vectors = []
    doc_lens = []
    doc_freq = defaultdict(int)
    term_postings = defaultdict(list)

    for idx, row in enumerate(embeddings):
        chunk_id = row["chunk_id"]
        chunk = db.t.chunks[chunk_id]
        text = chunk["text"] or ""
        tokens = _tokenize_for_bm25(text)
        tf = Counter(tokens)

        chunk_ids.append(chunk_id)
        chunk_texts.append(text)
        vectors.append(np.array(np.frombuffer(row["embedding"], dtype=np.float32), copy=True))
        doc_lens.append(len(tokens))

        for term, count in tf.items():
            doc_freq[term] += 1
            term_postings[term].append((idx, count))

    emb_matrix = np.vstack(vectors).astype(np.float32)
    doc_lens_arr = np.array(doc_lens, dtype=np.float32)
    avg_doc_len = float(doc_lens_arr.mean()) if len(doc_lens_arr) else 0.0
    return {
        "chunk_ids": np.array(chunk_ids, dtype=np.int64),
        "chunk_texts": chunk_texts,
        "emb_matrix": emb_matrix,
        "doc_lens": doc_lens_arr,
        "avg_doc_len": avg_doc_len,
        "doc_freq": dict(doc_freq),
        "term_postings": {k: v for k, v in term_postings.items()},
        "num_docs": len(chunk_ids),
    }


def _get_retrieval_cache(db):
    global _RETRIEVAL_CACHE
    if _RETRIEVAL_CACHE is not None:
        return _RETRIEVAL_CACHE
    with _RETRIEVAL_CACHE_LOCK:
        if _RETRIEVAL_CACHE is None:
            _RETRIEVAL_CACHE = _build_retrieval_cache(db)
    return _RETRIEVAL_CACHE


def refresh_retrieval_cache(db):
    """Rebuild the in-memory hybrid retrieval cache."""
    global _RETRIEVAL_CACHE
    with _RETRIEVAL_CACHE_LOCK:
        _RETRIEVAL_CACHE = _build_retrieval_cache(db)
    return _RETRIEVAL_CACHE


def _top_indices(scores: np.ndarray, k: int) -> np.ndarray:
    if k <= 0 or scores.size == 0:
        return np.array([], dtype=np.int64)
    k = min(k, int(scores.size))
    idx = np.argpartition(scores, -k)[-k:]
    return idx[np.argsort(scores[idx])[::-1]]


def _min_max_normalize(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values.astype(np.float32)
    lo = float(values.min())
    hi = float(values.max())
    if hi - lo <= 1e-12:
        return np.zeros_like(values, dtype=np.float32)
    return ((values - lo) / (hi - lo)).astype(np.float32)


def _bm25_scores(cache: Dict, query_terms: Sequence[str]) -> np.ndarray:
    num_docs = int(cache["num_docs"])
    scores = np.zeros(num_docs, dtype=np.float32)
    if num_docs == 0 or not query_terms:
        return scores

    avg_doc_len = float(cache["avg_doc_len"]) if float(cache["avg_doc_len"]) > 0 else 1.0
    doc_lens = cache["doc_lens"]
    doc_freq = cache["doc_freq"]
    term_postings = cache["term_postings"]

    for term in set(query_terms):
        df = int(doc_freq.get(term, 0))
        if df <= 0:
            continue
        idf = math.log(1.0 + ((num_docs - df + 0.5) / (df + 0.5)))
        for doc_idx, tf in term_postings.get(term, []):
            tf = float(tf)
            denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * (float(doc_lens[doc_idx]) / avg_doc_len))
            if denom <= 0:
                continue
            scores[doc_idx] += float(idf * ((tf * (BM25_K1 + 1.0)) / denom))
    return scores


def search_embeddings_with_debug(db, query, top_k=5):
    """Hybrid retrieval with debug metadata for candidates and fused ranking."""
    t0 = time.perf_counter()
    cache = _get_retrieval_cache(db)
    if not cache:
        print("No embeddings found in database")
        return [], {"query": query, "error": "No embeddings found in database"}

    query_variants = _expand_query_variants(query)
    vector_t0 = time.perf_counter()
    query_embeddings = _query_embeddings(query_variants).astype(np.float32)
    emb_matrix = cache["emb_matrix"]
    vector_scores = np.max(np.dot(query_embeddings, emb_matrix.T), axis=0).astype(np.float32)
    vector_k = max(int(top_k), VECTOR_CANDIDATE_K)
    vector_idx = _top_indices(vector_scores, vector_k)
    vector_elapsed = time.perf_counter() - vector_t0

    bm25_t0 = time.perf_counter()
    bm25_terms = []
    for q in [query] + query_variants:
        bm25_terms.extend(_tokenize_for_bm25(q))
    bm25_scores = _bm25_scores(cache, bm25_terms)
    bm25_k = max(int(top_k), BM25_CANDIDATE_K)
    bm25_idx = _top_indices(bm25_scores, bm25_k)
    bm25_elapsed = time.perf_counter() - bm25_t0

    fusion_t0 = time.perf_counter()
    if vector_idx.size == 0 and bm25_idx.size == 0:
        return [], {
            "query": query,
            "query_variants": query_variants,
            "candidate_counts": {"vector": 0, "bm25": 0, "merged": 0},
            "timings": {
                "vector_s": vector_elapsed,
                "bm25_s": bm25_elapsed,
                "fusion_s": 0.0,
                "total_s": time.perf_counter() - t0,
            },
            "ranked_chunks": [],
            "by_chunk_id": {},
        }

    candidate_idx = np.unique(np.concatenate([vector_idx, bm25_idx]))
    candidate_vector = vector_scores[candidate_idx]
    candidate_bm25 = bm25_scores[candidate_idx]
    vector_norm = _min_max_normalize(candidate_vector)
    bm25_norm = _min_max_normalize(candidate_bm25)
    fusion_scores = (FUSION_ALPHA * vector_norm) + ((1.0 - FUSION_ALPHA) * bm25_norm)
    order = np.argsort(fusion_scores)[::-1]
    fusion_elapsed = time.perf_counter() - fusion_t0

    scored = []
    ranked_chunks = []
    by_chunk_id = {}
    chunk_ids = cache["chunk_ids"]
    vector_set = set(vector_idx.tolist())
    bm25_set = set(bm25_idx.tolist())
    for pos in order[: int(top_k)]:
        idx = int(candidate_idx[pos])
        chunk_id = int(chunk_ids[idx])
        fusion_score = float(fusion_scores[pos])
        scored.append((fusion_score, chunk_id))

        chunk = db.t.chunks[chunk_id]
        extract = db.t.extracts[chunk["extract_id"]]
        page = db.t.pages[extract["page_id"]]
        item = {
            "rank": len(ranked_chunks) + 1,
            "score": fusion_score,
            "chunk_id": chunk_id,
            "extract_id": int(extract["id"]),
            "url": page["url"],
            "from_vector": idx in vector_set,
            "from_bm25": idx in bm25_set,
            "vector_score_raw": float(vector_scores[idx]),
            "bm25_score_raw": float(bm25_scores[idx]),
            "vector_score_norm": float(vector_norm[pos]),
            "bm25_score_norm": float(bm25_norm[pos]),
            "chunk_preview": " ".join((chunk["text"] or "").split())[:220],
        }
        ranked_chunks.append(item)
        by_chunk_id[chunk_id] = item

    if RETRIEVAL_DEBUG:
        print(
            "hybrid_debug: "
            f"vector_candidates={vector_idx.size} "
            f"bm25_candidates={bm25_idx.size} "
            f"merged_candidates={candidate_idx.size}"
        )
        print(
            "hybrid_debug_timing: "
            f"vector={vector_elapsed:.3f}s "
            f"bm25={bm25_elapsed:.3f}s "
            f"fusion={fusion_elapsed:.3f}s"
        )

    total_elapsed = time.perf_counter() - t0
    print(f"timing: search_embeddings {total_elapsed:.3f}s")
    debug = {
        "query": query,
        "query_variants": query_variants,
        "config": {
            "vector_candidate_k": VECTOR_CANDIDATE_K,
            "bm25_candidate_k": BM25_CANDIDATE_K,
            "fusion_alpha": FUSION_ALPHA,
            "bm25_k1": BM25_K1,
            "bm25_b": BM25_B,
        },
        "candidate_counts": {
            "vector": int(vector_idx.size),
            "bm25": int(bm25_idx.size),
            "merged": int(candidate_idx.size),
        },
        "timings": {
            "vector_s": vector_elapsed,
            "bm25_s": bm25_elapsed,
            "fusion_s": fusion_elapsed,
            "total_s": total_elapsed,
        },
        "ranked_chunks": ranked_chunks,
        "by_chunk_id": by_chunk_id,
    }
    return scored[:top_k], debug


def search_embeddings(db, query, top_k=5):
    scored, _ = search_embeddings_with_debug(db, query, top_k=top_k)
    return scored


def get_parent_extracts(db, scored_results, max_extracts=None):
    t0 = time.perf_counter()
    extracts = []
    seen_extract_ids = set()

    for score, chunk_id in scored_results:
        chunk = db.t.chunks[chunk_id]
        extract_id = chunk["extract_id"]
        if extract_id in seen_extract_ids:
            continue
        seen_extract_ids.add(extract_id)

        extract = db.t.extracts[extract_id]
        extracts.append(
            {
                "score": score,
                "chunk_id": chunk_id,
                "extract_id": extract_id,
                "text": extract["text"].strip(),
            }
        )

        if max_extracts is not None and len(extracts) >= max_extracts:
            break

    print(f"timing: get_parent_extracts {time.perf_counter() - t0:.3f}s")
    return extracts


def build_context(extracts, glossary: Iterable[str] | None = None):
    parts = []
    if glossary:
        for note in glossary:
            parts.append("[glossary]\n" + note)
    for item in extracts:
        header = f"[extract_id={item['extract_id']} score={item['score']:.4f}]"
        parts.append(header + "\n" + item["text"])
    return "\n\n---\n\n".join(parts)


def build_source_links(db, scored_results, max_sources=3, score_details: Dict[int, Dict] | None = None):
    sources = []
    seen_extract_ids = set()

    for score, chunk_id in scored_results:
        chunk = db.t.chunks[chunk_id]
        extract_id = chunk["extract_id"]
        if extract_id in seen_extract_ids:
            continue
        seen_extract_ids.add(extract_id)

        extract = db.t.extracts[extract_id]
        page = db.t.pages[extract["page_id"]]
        url = page["url"]
        source = {
            "score": score,
            "chunk_id": chunk_id,
            "extract_id": extract_id,
            "url": url,
        }
        if score_details:
            detail = score_details.get(int(chunk_id))
            if detail:
                source["from_vector"] = bool(detail.get("from_vector"))
                source["from_bm25"] = bool(detail.get("from_bm25"))
                source["vector_score_raw"] = detail.get("vector_score_raw")
                source["bm25_score_raw"] = detail.get("bm25_score_raw")
                source["vector_score_norm"] = detail.get("vector_score_norm")
                source["bm25_score_norm"] = detail.get("bm25_score_norm")
        sources.append(source)

        if max_sources is not None and len(sources) >= max_sources:
            break

    return sources


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
    vector = np.array([0.5, 0.1, -0.2], dtype=np.float32)
    test_db.t.embeddings.insert(chunk_id=chunk["id"], embedding=vector.tobytes())

    assert _tokenize_for_bm25("Hello, World 123!") == ["hello", "world", "123"]
    normalized = _min_max_normalize(np.array([1.0, 3.0], dtype=np.float32))
    assert normalized.tolist() == [0.0, 1.0]
    sources = build_source_links(test_db, [(0.9, chunk["id"])], max_sources=1)
    assert len(sources) == 1 and sources[0]["url"] == "https://example.com/doc"
    print("Check Passed")
