# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
#   kernelspec:
#     display_name: py313
#     language: python
#     name: python3
# ---

# %%
import os
import time
import re
import math
import threading
import torch
import numpy as np
from collections import Counter, defaultdict
from typing import Dict, Generator, Iterable, List, Sequence
from sentence_transformers import SentenceTransformer
from fastlite import database
from openai import OpenAI
import httpx
import json
import app_db
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# %%
MODEL_NAME = "BAAI/bge-small-en-v1.5"
LLM_MODEL = "gpt-5.2"
VECTOR_CANDIDATE_K = 50
BM25_CANDIDATE_K = 50
FUSION_ALPHA = 0.70
BM25_K1 = 1.5
BM25_B = 0.75
RETRIEVAL_DEBUG = os.getenv("HYBRID_RETRIEVAL_DEBUG", "").lower() in {"1", "true", "yes"}

# Domain glossary + synonym handling for query expansion.
SYNONYM_GROUPS = [
    {
        "canonical": "myway",
        "aliases": ["myway", "prepay", "prepaid"],
        "note": "Prepay and MyWay refer to the same program.",
    },
]

GLOSSARY_SNIPPETS = [
    "Glossary: 'Prepay' and 'MyWay' refer to the same program; treat them as identical terms.",
]

# %%
db = database("scraper.db")
_RETRIEVAL_CACHE = None
_RETRIEVAL_CACHE_LOCK = threading.Lock()

# %%
device = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(MODEL_NAME, device=device)
model.max_seq_length = 512

# %%
if load_dotenv:
    load_dotenv()
else:
    print("python-dotenv not installed; set OPENAI_API_KEY in the environment.")

# %%
def _expand_query_variants(query: str) -> List[str]:
    """Return query variants by swapping known synonym aliases."""
    variants = {query}
    for group in SYNONYM_GROUPS:
        aliases = group.get("aliases", [])
        if not aliases:
            continue
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, aliases)) + r")\b", re.IGNORECASE)
        next_variants = set()
        for v in variants:
            if pattern.search(v):
                for alias in aliases:
                    next_variants.add(pattern.sub(alias, v))
            else:
                next_variants.add(v)
        variants = next_variants
    return list(variants)


def _query_embeddings(queries: Iterable[str]) -> np.ndarray:
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


def refresh_retrieval_cache():
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
        for item in ranked_chunks[:5]:
            print(
                "hybrid_top"
                f"{item['rank']}: score={item['score']:.4f} "
                f"chunk_id={item['chunk_id']} "
                f"from_vector={item['from_vector']} "
                f"from_bm25={item['from_bm25']} "
                f"text={item['chunk_preview'][:120]}"
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
    """Hybrid retrieval: vector + BM25 candidate generation with score-fusion rerank."""
    scored, _ = search_embeddings_with_debug(db, query, top_k=top_k)
    return scored

# %%
def get_parent_extracts(db, scored_results, max_extracts=None):
    """Return de-duplicated parent extracts for scored chunk results."""
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

# %%
def build_context(extracts, glossary: Iterable[str] | None = None):
    """Assemble extracts into a single context string."""
    parts = []
    if glossary:
        for note in glossary:
            parts.append("[glossary]\n" + note)
    for item in extracts:
        header = f"[extract_id={item['extract_id']} score={item['score']:.4f}]"
        parts.append(header + "\n" + item["text"])
    return "\n\n---\n\n".join(parts)

# %%
def build_source_links(db, scored_results, max_sources=3, score_details: Dict[int, Dict] | None = None):
    """Return de-duplicated source links for scored chunk results."""
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
def _build_llm_prompt(query: str, context: str) -> tuple[str, str]:
    system_text = (
        "Answer the question using only the provided context. "
        "If the answer is not in the context, say you don't know."
    )
    user_text = f"Question: {query}\n\nContext:\n{context}"
    return system_text, user_text


# %%
def answer_query_with_context(query, top_k=10, max_extracts=6):
    """Search, gather context, and ask the LLM to answer."""
    t0 = time.perf_counter()
    scored, retrieval_debug = search_embeddings_with_debug(db, query, top_k=top_k)
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS)

    if not context:
        print("No context available to send to the LLM.")
        return None

    client = OpenAI(http_client=httpx.Client(verify=False))
    t_llm = time.perf_counter()
    system_text, user_text = _build_llm_prompt(query, context)
    response = client.responses.create(
        model=LLM_MODEL,
        reasoning={"effort": "none"},
        text={"verbosity": "low"},
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_text,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": user_text,
                    }
                ],
            },
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


def stream_answer_with_context(
    query, top_k=10, max_extracts=6
) -> Generator[Dict, None, None]:
    """Stream the LLM response as text deltas plus a final sources event."""
    t0 = time.perf_counter()
    cached = app_db.get_cache_answer(query)
    if cached:
        sources = []
        if cached.get("sources_json"):
            try:
                sources = json.loads(cached["sources_json"])
            except Exception:
                sources = []
        yield {"type": "cache", "cache_id": cached.get("id")}
        text = cached.get("answer_text", "")
        for i in range(0, len(text), 80):
            yield {"type": "delta", "text": text[i : i + 80]}
        yield {
            "type": "debug",
            "debug": {
                "query": query,
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
    scored, retrieval_debug = search_embeddings_with_debug(db, query, top_k=top_k)
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts, glossary=GLOSSARY_SNIPPETS)
    score_details = retrieval_debug.get("by_chunk_id", {})
    sources = build_source_links(
        db,
        scored,
        max_sources=max_extracts,
        score_details=score_details,
    )

    if not context:
        yield {
            "type": "delta",
            "text": "I couldn't find relevant context in the embeddings database.",
        }
        yield {
            "type": "debug",
            "debug": {
                "query": query,
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
    system_text, user_text = _build_llm_prompt(query, context)
    stream = client.responses.create(
        model=LLM_MODEL,
        reasoning={"effort": "none"},
        text={"verbosity": "low"},
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_text,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": user_text,
                    }
                ],
            },
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
            "query": query,
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
# answer_query_with_context("transfer service")

# %%
