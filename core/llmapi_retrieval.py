from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Sequence
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import numpy as np

try:
    from .llmapi_shared import (
        BM25_B,
        BM25_CANDIDATE_K,
        BM25_K1,
        FUSION_ALPHA,
        MIN_BM25_SCORE_RAW,
        MIN_VECTOR_SCORE_RAW,
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
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
        MIN_BM25_SCORE_RAW,
        MIN_VECTOR_SCORE_RAW,
        SOURCE_MIN_BM25_SCORE_RAW,
        SOURCE_MIN_VECTOR_SCORE_RAW,
        RETRIEVAL_DEBUG,
        SYNONYM_GROUPS,
        VECTOR_CANDIDATE_K,
        get_model,
    )
    from core.fastlite_db import ensure_pipeline_schema, get_scraper_db

_RETRIEVAL_CACHE = None
_RETRIEVAL_CACHE_LOCK = threading.Lock()
_TAB_STEP_HREF_RE = re.compile(r"""href\s*=\s*["'](#tab-step(\d+))["']""", re.IGNORECASE)
_TAB_STEP_ID_RE = re.compile(r"""id\s*=\s*["'](tab-step(\d+))["']""", re.IGNORECASE)


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


def canonicalize_source_url(url: str) -> str:
    """Normalize source URLs so encoded/decoded and fragment variants dedupe."""
    value = str(url or "").strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
    except Exception:
        return value

    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = unquote(parsed.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"

    query_items = parse_qs(parsed.query, keep_blank_values=False)
    docs_values = query_items.get("docs") or []
    docs_value = next((unquote(str(v)).strip().strip("/") for v in docs_values if str(v).strip()), "")
    if docs_value:
        docs_norm = quote(docs_value, safe="/-._~")
        return f"{scheme}://{netloc}{path}?docs={docs_norm}"

    pairs: list[tuple[str, str]] = []
    for key in sorted(query_items.keys()):
        norm_key = unquote(str(key)).strip()
        if not norm_key:
            continue
        for val in query_items.get(key) or []:
            pairs.append((norm_key, unquote(str(val)).strip()))
    query = urlencode(pairs, doseq=True, safe="/-._~")
    if query:
        return f"{scheme}://{netloc}{path}?{query}"
    return f"{scheme}://{netloc}{path}"


def extract_tab_step_anchors(html: str) -> list[str]:
    """Return sorted unique tab-step anchors found in page HTML."""
    text = str(html or "")
    if not text:
        return []

    step_numbers: set[int] = set()
    for match in _TAB_STEP_HREF_RE.finditer(text):
        step_numbers.add(int(match.group(2)))
    for match in _TAB_STEP_ID_RE.finditer(text):
        step_numbers.add(int(match.group(2)))

    if not step_numbers:
        return []
    return [f"#tab-step{num}" for num in sorted(step_numbers)]


def _coerce_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return 0.0


def _source_passes_source_score_gate(source: dict) -> bool:
    vector_raw = _coerce_float(source.get("vector_score_raw"))
    bm25_raw = _coerce_float(source.get("bm25_score_raw"))
    return (vector_raw > SOURCE_MIN_VECTOR_SCORE_RAW) or (bm25_raw > SOURCE_MIN_BM25_SCORE_RAW)


def _is_missing_row_error(exc: Exception) -> bool:
    name = (exc.__class__.__name__ or "").lower()
    if name == "notfounderror":
        return True
    module = (exc.__class__.__module__ or "").lower()
    return "apswutils" in module and "notfound" in name


def _safe_get_row(table, row_id: int):
    try:
        return table[row_id]
    except Exception as exc:
        if _is_missing_row_error(exc):
            return None
        raise


def _resolve_chunk_lineage(db, chunk_id: int):
    chunk = _safe_get_row(db.t.chunks, int(chunk_id))
    if not chunk:
        return None
    extract = _safe_get_row(db.t.extracts, int(chunk.get("extract_id") or 0))
    if not extract:
        return None
    page = _safe_get_row(db.t.pages, int(extract.get("page_id") or 0))
    if not page:
        return None
    return chunk, extract, page


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

    stale_embedding_rows = 0
    for row in embeddings:
        chunk_id = row["chunk_id"]
        chunk = _safe_get_row(db.t.chunks, int(chunk_id))
        if not chunk:
            stale_embedding_rows += 1
            continue
        text = chunk["text"] or ""
        tokens = _tokenize_for_bm25(text)
        tf = Counter(tokens)
        posting_idx = len(chunk_ids)

        chunk_ids.append(chunk_id)
        chunk_texts.append(text)
        vectors.append(np.array(np.frombuffer(row["embedding"], dtype=np.float32), copy=True))
        doc_lens.append(len(tokens))

        for term, count in tf.items():
            doc_freq[term] += 1
            term_postings[term].append((posting_idx, count))

    if not vectors:
        if RETRIEVAL_DEBUG and stale_embedding_rows > 0:
            print(f"hybrid_debug: cache build skipped stale embedding rows={stale_embedding_rows}")
        return None

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


def _low_signal_gate(vector_scores: np.ndarray, bm25_scores: np.ndarray) -> dict:
    vector_score_raw_max = float(vector_scores.max()) if vector_scores.size else 0.0
    bm25_score_raw_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
    triggered = (vector_score_raw_max < MIN_VECTOR_SCORE_RAW) and (bm25_score_raw_max < MIN_BM25_SCORE_RAW)
    return {
        "triggered": bool(triggered),
        "vector_score_raw_max": vector_score_raw_max,
        "bm25_score_raw_max": bm25_score_raw_max,
        "vector_score_raw_min": float(MIN_VECTOR_SCORE_RAW),
        "bm25_score_raw_min": float(MIN_BM25_SCORE_RAW),
    }


def _search_embeddings_with_debug_once(db, query, top_k=5):
    """Hybrid retrieval pass with stale row skipping but no cache refresh retry."""
    t0 = time.perf_counter()
    cache = _get_retrieval_cache(db)
    if not cache:
        print("No embeddings found in database")
        return [], {"query": query, "error": "No embeddings found in database"}, 0

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
    low_signal_gate = _low_signal_gate(vector_scores, bm25_scores)

    fusion_t0 = time.perf_counter()
    if vector_idx.size == 0 and bm25_idx.size == 0:
        return (
            [],
            {
                "query": query,
                "query_variants": query_variants,
                "config": {
                    "vector_candidate_k": VECTOR_CANDIDATE_K,
                    "bm25_candidate_k": BM25_CANDIDATE_K,
                    "fusion_alpha": FUSION_ALPHA,
                    "bm25_k1": BM25_K1,
                    "bm25_b": BM25_B,
                    "min_vector_score_raw": MIN_VECTOR_SCORE_RAW,
                    "min_bm25_score_raw": MIN_BM25_SCORE_RAW,
                    "source_min_vector_score_raw": SOURCE_MIN_VECTOR_SCORE_RAW,
                    "source_min_bm25_score_raw": SOURCE_MIN_BM25_SCORE_RAW,
                },
                "candidate_counts": {"vector": 0, "bm25": 0, "merged": 0},
                "timings": {
                    "vector_s": vector_elapsed,
                    "bm25_s": bm25_elapsed,
                    "fusion_s": 0.0,
                    "total_s": time.perf_counter() - t0,
                },
                "low_signal_gate": low_signal_gate,
                "ranked_chunks": [],
                "by_chunk_id": {},
                "stale_candidates_skipped": 0,
            },
            0,
        )

    candidate_idx = np.unique(np.concatenate([vector_idx, bm25_idx]))
    if low_signal_gate["triggered"]:
        fusion_elapsed = time.perf_counter() - fusion_t0
        if RETRIEVAL_DEBUG:
            print(
                "hybrid_debug_low_signal: "
                f"vector_raw_max={low_signal_gate['vector_score_raw_max']:.4f} "
                f"bm25_raw_max={low_signal_gate['bm25_score_raw_max']:.4f}"
            )
        return (
            [],
            {
                "query": query,
                "query_variants": query_variants,
                "config": {
                    "vector_candidate_k": VECTOR_CANDIDATE_K,
                    "bm25_candidate_k": BM25_CANDIDATE_K,
                    "fusion_alpha": FUSION_ALPHA,
                    "bm25_k1": BM25_K1,
                    "bm25_b": BM25_B,
                    "min_vector_score_raw": MIN_VECTOR_SCORE_RAW,
                    "min_bm25_score_raw": MIN_BM25_SCORE_RAW,
                    "source_min_vector_score_raw": SOURCE_MIN_VECTOR_SCORE_RAW,
                    "source_min_bm25_score_raw": SOURCE_MIN_BM25_SCORE_RAW,
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
                    "total_s": time.perf_counter() - t0,
                },
                "low_signal_gate": low_signal_gate,
                "ranked_chunks": [],
                "by_chunk_id": {},
                "stale_candidates_skipped": 0,
            },
            0,
        )

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
    stale_candidates_skipped = 0
    for pos in order:
        if len(scored) >= int(top_k):
            break
        idx = int(candidate_idx[pos])
        chunk_id = int(chunk_ids[idx])
        fusion_score = float(fusion_scores[pos])
        lineage = _resolve_chunk_lineage(db, chunk_id)
        if not lineage:
            stale_candidates_skipped += 1
            continue
        chunk, extract, page = lineage
        scored.append((fusion_score, chunk_id))
        item = {
            "rank": len(ranked_chunks) + 1,
            "score": fusion_score,
            "chunk_id": chunk_id,
            "extract_id": int(extract["id"]),
            "url": page["url"],
            "url_canonical": canonicalize_source_url(page["url"]),
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
            "min_vector_score_raw": MIN_VECTOR_SCORE_RAW,
            "min_bm25_score_raw": MIN_BM25_SCORE_RAW,
            "source_min_vector_score_raw": SOURCE_MIN_VECTOR_SCORE_RAW,
            "source_min_bm25_score_raw": SOURCE_MIN_BM25_SCORE_RAW,
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
        "low_signal_gate": low_signal_gate,
        "ranked_chunks": ranked_chunks,
        "by_chunk_id": by_chunk_id,
        "stale_candidates_skipped": int(stale_candidates_skipped),
    }
    return scored[:top_k], debug, stale_candidates_skipped


def search_embeddings_with_debug(db, query, top_k=5):
    """Hybrid retrieval with debug metadata for candidates and fused ranking."""
    scored, debug, stale_candidates_skipped = _search_embeddings_with_debug_once(
        db,
        query,
        top_k=top_k,
    )
    if stale_candidates_skipped <= 0:
        return scored, debug

    try:
        refresh_retrieval_cache(db)
    except Exception as exc:
        debug["cache_refresh_retry"] = False
        debug["cache_refresh_retry_error"] = str(exc) or exc.__class__.__name__
        return scored, debug

    retried_scored, retried_debug, retried_stale = _search_embeddings_with_debug_once(
        db,
        query,
        top_k=top_k,
    )
    retried_debug["cache_refresh_retry"] = True
    retried_debug["stale_candidates_skipped_initial"] = int(stale_candidates_skipped)
    retried_debug["stale_candidates_skipped_after_refresh"] = int(retried_stale)
    return retried_scored, retried_debug


def search_embeddings(db, query, top_k=5):
    scored, _ = search_embeddings_with_debug(db, query, top_k=top_k)
    return scored


def get_parent_extracts(db, scored_results, max_extracts=None):
    t0 = time.perf_counter()
    extracts = []
    seen_extract_ids = set()
    seen_urls = set()

    for score, chunk_id in scored_results:
        lineage = _resolve_chunk_lineage(db, int(chunk_id))
        if not lineage:
            continue
        chunk, extract, page = lineage
        extract_id = chunk["extract_id"]
        if extract_id in seen_extract_ids:
            continue

        url_canonical = canonicalize_source_url(page["url"])
        if url_canonical and url_canonical in seen_urls:
            continue

        seen_extract_ids.add(extract_id)
        if url_canonical:
            seen_urls.add(url_canonical)
        extracts.append(
            {
                "score": score,
                "chunk_id": chunk_id,
                "extract_id": extract_id,
                "text": extract["text"].strip(),
                "url": page["url"],
                "url_canonical": url_canonical,
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
    seen_urls = set()

    for score, chunk_id in scored_results:
        lineage = _resolve_chunk_lineage(db, int(chunk_id))
        if not lineage:
            continue
        chunk, extract, page = lineage
        extract_id = chunk["extract_id"]
        if extract_id in seen_extract_ids:
            continue

        url = page["url"]
        url_canonical = canonicalize_source_url(url)
        if url_canonical and url_canonical in seen_urls:
            continue
        tab_step_anchors = extract_tab_step_anchors(page.get("html") or "")

        source = {
            "score": score,
            "chunk_id": chunk_id,
            "extract_id": extract_id,
            "url": url,
            "url_canonical": url_canonical,
            "last_scraped": page.get("last_scraped"),
            "has_tab_steps": bool(tab_step_anchors),
            "tab_step_count": len(tab_step_anchors),
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
        source["source_score_eligible"] = _source_passes_source_score_gate(source)
        source["procedure_link_eligible"] = bool(source["has_tab_steps"]) and bool(source["source_score_eligible"])
        if not source["source_score_eligible"]:
            continue

        seen_extract_ids.add(extract_id)
        if url_canonical:
            seen_urls.add(url_canonical)
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
    encoded_url = "https://connections/?docs=residential%2Fmyway%2Ftopic#tab"
    decoded_url = "https://connections/?docs=residential/myway/topic"
    assert canonicalize_source_url(encoded_url) == canonicalize_source_url(decoded_url)
    anchors = extract_tab_step_anchors('<a href="#tab-step2"></a><div id="tab-step1"></div>')
    assert anchors == ["#tab-step1", "#tab-step2"]
    sources = build_source_links(
        test_db,
        [(0.9, chunk["id"])],
        max_sources=1,
        score_details={
            int(chunk["id"]): {
                "from_vector": True,
                "from_bm25": False,
                "vector_score_raw": 0.9,
                "bm25_score_raw": 0.0,
            }
        },
    )
    assert len(sources) == 1 and sources[0]["url"] == "https://example.com/doc"
    assert sources[0]["url_canonical"] == "https://example.com/doc"
    assert sources[0]["last_scraped"] == "now"
    assert sources[0]["has_tab_steps"] is False
    assert sources[0]["tab_step_count"] == 0
    print("Check Passed")
