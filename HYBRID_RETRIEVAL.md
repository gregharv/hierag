# Hybrid Retrieval: Vector + BM25 + Fusion

This project uses a **hybrid retriever** in `05_llmapi.py` to rank chunks.

At a high level:
1. Vector search finds semantically similar chunks.
2. BM25 finds keyword/term matches.
3. Fusion combines both into one final score.

---

## 1) Vector Search (semantic similarity)

### What it does
Vector search answers:  
"Which chunks *mean* something similar to the query?"

### How it works here
1. The query is expanded into variants (for synonyms, if configured).
2. Each query variant is converted into an embedding vector.
3. Every stored chunk already has an embedding vector.
4. For each chunk, we compute similarity using dot product.
5. If there are multiple query variants, we keep the **max** similarity for that chunk.
6. We take the top vector candidates (`VECTOR_CANDIDATE_K`, default `50`).

### Output
- A raw vector score per chunk (`vector_score_raw`).
- A list of top vector candidates.

---

## 2) BM25 Search (lexical keyword matching)

### What it does
BM25 answers:  
"Which chunks contain the query terms in useful frequency/positions (keyword relevance)?"

### How it works here
1. Query text is tokenized (lowercased word-like tokens).
2. Chunk text is tokenized in advance and cached.
3. For each query term and chunk, BM25 uses:
   - term frequency in that chunk,
   - document frequency across corpus,
   - chunk length normalization.
4. IDF is computed as:

`idf = log(1 + (N - df + 0.5) / (df + 0.5))`

5. BM25 parameters used:
   - `BM25_K1 = 1.5`
   - `BM25_B = 0.75`
6. We take top BM25 candidates (`BM25_CANDIDATE_K`, default `50`).

### Output
- A raw BM25 score per chunk (`bm25_score_raw`).
- A list of top BM25 candidates.

---

## 3) Fusion (combine vector + BM25)

### Why fusion is needed
- Vector is strong for meaning/paraphrases.
- BM25 is strong for exact term hits.
- Either one alone can miss good results.

Fusion blends both strengths.

### How it works here
1. Candidate set = union of:
   - top vector candidates
   - top BM25 candidates
2. For each candidate chunk, collect:
   - raw vector score
   - raw BM25 score
3. Normalize each score type independently using min-max on candidate set:

`norm = (x - min) / (max - min)`

If all values are equal, normalized values become `0`.

4. Compute final fusion score:

`fusion = FUSION_ALPHA * vector_norm + (1 - FUSION_ALPHA) * bm25_norm`

Current default:
- `FUSION_ALPHA = 0.70`  
  (70% semantic signal, 30% lexical signal)

5. Sort by `fusion` descending and return top `top_k`.

---

## Example (simple)

Assume one candidate chunk has:
- `vector_norm = 0.80`
- `bm25_norm = 0.50`
- `FUSION_ALPHA = 0.70`

Then:

`fusion = 0.70 * 0.80 + 0.30 * 0.50 = 0.56 + 0.15 = 0.71`

So final rank uses `0.71`.

---

## What you see in debug

In debug output/page, each ranked chunk can show:
- `from_vector` (was it in vector top candidates?)
- `from_bm25` (was it in BM25 top candidates?)
- `vector_score_raw`
- `bm25_score_raw`
- `vector_score_norm`
- `bm25_score_norm`
- final `score` (fusion score)

So you can inspect exactly why a chunk ranked where it did.
