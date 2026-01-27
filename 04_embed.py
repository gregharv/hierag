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
import torch
import numpy as np
from sentence_transformers import SentenceTransformer
from fastlite import database

# %%
MODEL_NAME = "BAAI/bge-small-en-v1.5"

# %%
db = database('scraper.db')

# %%
device = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(MODEL_NAME, device=device)
model.max_seq_length = 512

# %%
def generate_embeddings_for_chunks(db, batch_size=64):
    chunks = list(db.t.chunks())
    chunks_without_embeddings = [
        chunk for chunk in chunks
        if not list(db.t.embeddings.rows_where('chunk_id=?', [chunk['id']]))
    ]
    
    print(f"Total chunks: {len(chunks)}")
    print(f"Chunks without embeddings: {len(chunks_without_embeddings)}")
    
    if not chunks_without_embeddings:
        print("All chunks already have embeddings")
        return
    
    texts = [chunk['text'] for chunk in chunks_without_embeddings]
    
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i+batch_size]
        batch_chunks = chunks_without_embeddings[i:i+batch_size]
        
        embeddings = model.encode(
            batch_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        
        for chunk, embedding in zip(batch_chunks, embeddings):
            # Store as binary (bytes) for efficiency:
            # - BGE-small produces 384-dimensional float32 vectors
            # - Binary: 384 * 4 bytes = 1,536 bytes per embedding
            # - Text: would be ~10-15 bytes per number = ~4,000-6,000 bytes
            db.t.embeddings.insert(
                chunk_id=chunk['id'],
                embedding=embedding.tobytes(),
            )
        
        print(f"Processed {min(i+batch_size, len(texts))}/{len(texts)} chunks")

# %%
generate_embeddings_for_chunks(db)

# %%
def show_sample_embedding(db, chunk_id=None):
    """Display a sample embedding in human-readable format"""
    if chunk_id is None:
        # Get first chunk with an embedding
        embeddings = list(db.t.embeddings(limit=1))
        if not embeddings:
            print("No embeddings found in database")
            return
        embedding_row = embeddings[0]
        chunk_id = embedding_row['chunk_id']
    else:
        embedding_row = list(db.t.embeddings.rows_where('chunk_id=?', [chunk_id]))
        if not embedding_row:
            print(f"No embedding found for chunk_id {chunk_id}")
            return
        embedding_row = embedding_row[0]
    
    # Get the chunk text
    chunk = db.t.chunks[embedding_row['chunk_id']]
    
    # Convert binary back to numpy array
    embedding = np.frombuffer(embedding_row['embedding'], dtype=np.float32)
    
    print(f"Chunk ID: {chunk_id}")
    print(f"Chunk text (first 200 chars): {chunk['text'][:200]}...")
    print(f"\nEmbedding shape: {embedding.shape}")
    print(f"Embedding dtype: {embedding.dtype}")
    print(f"Embedding size: {len(embedding_row['embedding'])} bytes (binary)")
    print(f"\nFirst 20 values:")
    print(embedding[:20])
    print(f"\nLast 20 values:")
    print(embedding[-20:])
    print(f"\nStats:")
    print(f"  Min: {embedding.min():.6f}")
    print(f"  Max: {embedding.max():.6f}")
    print(f"  Mean: {embedding.mean():.6f}")
    print(f"  Std: {embedding.std():.6f}")
    print(f"\nFull embedding (all {len(embedding)} values):")
    # print(embedding.tolist())
    
    return embedding

# %%
show_sample_embedding(db)

# %%
def search_embeddings(db, query, top_k=5):
    """Search stored embeddings with a text query and return top matches."""
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
        chunk = db.t.chunks[row["chunk_id"]]
        scored.append((score, row["chunk_id"], chunk["text"]))

    scored.sort(key=lambda x: x[0], reverse=True)

    print(f'Query: "{query}"')
    for score, chunk_id, text in scored[:top_k]:
        preview = text.replace("\n", " ").strip()[:200]
        print(f"score={score:.4f} chunk_id={chunk_id} text={preview}...")

    return scored[:top_k]

# %%
def show_parent_extracts(db, scored_results, max_chars=None):
    """Display parent extracts for scored chunk results, de-duplicated by extract."""
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
        print(f"\nscore={score:.4f} chunk_id={chunk_id} extract_id={extract_id}")
        print(text)

# %%
results = search_embeddings(db, "residential electric rate", top_k=5)
show_parent_extracts(db, results)

# %%
