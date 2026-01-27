# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: crit
#     language: python
#     name: python3
# ---

# %%
import os
import time
import torch
import numpy as np
from sentence_transformers import SentenceTransformer
from fastlite import database
from openai import OpenAI
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# %%
MODEL_NAME = "BAAI/bge-small-en-v1.5"
LLM_MODEL = "gpt-5.2"

# %%
db = database("scraper.db")

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
def search_embeddings(db, query, top_k=5):
    """Search stored embeddings with a text query and return top matches."""
    t0 = time.perf_counter()
    embeddings = list(db.t.embeddings())
    if not embeddings:
        print("No embeddings found in database")
        return []

    query_embedding = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    scored = []
    for row in embeddings:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        score = float(np.dot(query_embedding, embedding))
        scored.append((score, row["chunk_id"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    print(f"timing: search_embeddings {time.perf_counter() - t0:.3f}s")
    return scored[:top_k]

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
def build_context(extracts):
    """Assemble extracts into a single context string."""
    parts = []
    for item in extracts:
        header = f"[extract_id={item['extract_id']} score={item['score']:.4f}]"
        parts.append(header + "\n" + item["text"])
    return "\n\n---\n\n".join(parts)

# %%
def build_source_links(db, scored_results, max_sources=3):
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

        sources.append(
            {
                "score": score,
                "chunk_id": chunk_id,
                "extract_id": extract_id,
                "url": url,
            }
        )

        if max_sources is not None and len(sources) >= max_sources:
            break

    return sources

# %%
def answer_query_with_context(query, top_k=5, max_extracts=3, verbose=True):
    """Search, gather context, and ask the LLM to answer."""
    t0 = time.perf_counter()
    scored = search_embeddings(db, query, top_k=top_k)
    extracts = get_parent_extracts(db, scored, max_extracts=max_extracts)
    context = build_context(extracts)

    if not context:
        if verbose:
            print("No context available to send to the LLM.")
        return None, []

    client = OpenAI()
    t_llm = time.perf_counter()
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
                        "text": (
                            "Answer the question using only the provided context. "
                            "If the answer is not in the context, say you don't know."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"Question: {query}\n\nContext:\n{context}",
                    }
                ],
            },
        ],
    )

    if verbose:
        print(f"timing: llm_response {time.perf_counter() - t_llm:.3f}s")
        print(f"timing: total {time.perf_counter() - t0:.3f}s")
        print(response.output_text)
    sources = build_source_links(db, scored, max_sources=max_extracts)
    if verbose and sources:
        print("\nSources:")
        for source in sources:
            print(f"- {source['url']}")
    return response.output_text, sources

# %%
if __name__ == "__main__":
    answer_query_with_context("how can i get an edp")

# %%
