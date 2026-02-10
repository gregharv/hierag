from __future__ import annotations

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from .fastlite_db import bootstrap_scraper_db

MODEL_NAME = "BAAI/bge-small-en-v1.5"
_MODEL: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _MODEL = SentenceTransformer(MODEL_NAME, device=device)
        _MODEL.max_seq_length = 512
    return _MODEL


def generate_embeddings_for_chunks(db, batch_size=64):
    chunks = list(db.t.chunks())
    chunks_without_embeddings = [
        chunk
        for chunk in chunks
        if not list(db.t.embeddings.rows_where("chunk_id=?", [chunk["id"]]))
    ]

    print(f"Total chunks: {len(chunks)}")
    print(f"Chunks without embeddings: {len(chunks_without_embeddings)}")
    if not chunks_without_embeddings:
        print("All chunks already have embeddings")
        return

    model = _get_model()
    texts = [chunk["text"] for chunk in chunks_without_embeddings]
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_chunks = chunks_without_embeddings[i : i + batch_size]

        embeddings = model.encode(
            batch_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        for chunk, embedding in zip(batch_chunks, embeddings):
            db.t.embeddings.insert(
                chunk_id=chunk["id"],
                embedding=embedding.tobytes(),
            )

        print(f"Processed {min(i + batch_size, len(texts))}/{len(texts)} chunks")


def show_sample_embedding(db, chunk_id=None):
    """Display a sample embedding in human-readable format."""
    if chunk_id is None:
        embeddings = list(db.t.embeddings(limit=1))
        if not embeddings:
            print("No embeddings found in database")
            return None
        embedding_row = embeddings[0]
        chunk_id = embedding_row["chunk_id"]
    else:
        rows = list(db.t.embeddings.rows_where("chunk_id=?", [chunk_id]))
        if not rows:
            print(f"No embedding found for chunk_id {chunk_id}")
            return None
        embedding_row = rows[0]

    chunk = db.t.chunks[embedding_row["chunk_id"]]
    embedding = np.frombuffer(embedding_row["embedding"], dtype=np.float32)

    print(f"Chunk ID: {chunk_id}")
    print(f"Chunk text (first 200 chars): {chunk['text'][:200]}...")
    print(f"Embedding shape: {embedding.shape}")
    print(f"Embedding dtype: {embedding.dtype}")
    print(f"Embedding size: {len(embedding_row['embedding'])} bytes (binary)")
    return embedding


def search_embeddings(db, query, top_k=5):
    """Search stored embeddings with a text query and return top matches."""
    embeddings = list(db.t.embeddings())
    if not embeddings:
        print("No embeddings found in database")
        return []

    model = _get_model()
    query_embedding = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    scored = []
    for row in embeddings:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        score = float(np.dot(query_embedding, embedding))
        chunk = db.t.chunks[row["chunk_id"]]
        scored.append((score, row["chunk_id"], chunk["text"]))

    scored.sort(key=lambda item: item[0], reverse=True)
    print(f'Query: "{query}"')
    for score, chunk_id, text in scored[:top_k]:
        preview = text.replace("\n", " ").strip()[:200]
        print(f"score={score:.4f} chunk_id={chunk_id} text={preview}...")

    return scored[:top_k]


def show_parent_extracts(db, scored_results, max_chars=None):
    """Display parent extracts for scored results, de-duplicated by extract."""
    if not scored_results:
        print("No results to display")
        return

    seen_extract_ids = set()
    for score, chunk_id, _ in scored_results:
        chunk = db.t.chunks[chunk_id]
        extract_id = chunk["extract_id"]
        if extract_id in seen_extract_ids:
            continue
        seen_extract_ids.add(extract_id)

        extract = db.t.extracts[extract_id]
        text = extract["text"].strip()
        if max_chars is not None:
            text = text[:max_chars]
        print(f"score={score:.4f} chunk_id={chunk_id} extract_id={extract_id}")
        print(text)


# %%
if __name__ == "__main__":
    test_db = bootstrap_scraper_db(":memory:")
    page = test_db.t.pages.insert(
        site_id=1,
        url="https://example.com/a",
        html="<div>hello</div>",
        content_hash="x",
        last_scraped="now",
        last_changed="now",
    )
    extract = test_db.t.extracts.insert(page_id=page["id"], extract_index=0, text="extract")
    chunk = test_db.t.chunks.insert(extract_id=extract["id"], chunk_index=0, text="chunk")
    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    test_db.t.embeddings.insert(chunk_id=chunk["id"], embedding=vector.tobytes())
    sample = show_sample_embedding(test_db, chunk["id"])
    assert sample is not None and sample.shape[0] == 3
    print("Check Passed")
