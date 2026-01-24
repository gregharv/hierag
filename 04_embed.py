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
import time
import statistics
import torch
from sentence_transformers import SentenceTransformer


# %%
MODELS = {
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
    "e5_small": "intfloat/e5-small-v2",
    "bge_small": "BAAI/bge-small-en-v1.5",
}


# %%
def prep_texts(model_key, texts, as_query=False):
    if model_key.startswith("e5"):
        prefix = "query: " if as_query else "passage: "
        return [prefix + t for t in texts]
    return texts



# %%
def bench(
    model_name,
    texts,
    device="cpu",
    batch_size=64,
    max_seq_length=256,
    runs=10,
    warmup=2,
):
    model = SentenceTransformer(model_name, device=device)
    model.max_seq_length = max_seq_length

    # Warmup
    for _ in range(warmup):
        _ = model.encode(
            texts[:batch_size],
            batch_size=batch_size,
            normalize_embeddings=False,
            show_progress_bar=False,
        )

    times = []
    for _ in range(runs):
        if device.startswith("cuda"):
            torch.cuda.synchronize()

        t0 = time.perf_counter()

        _ = model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=False,
            show_progress_bar=False,
        )

        if device.startswith("cuda"):
            torch.cuda.synchronize()

        t1 = time.perf_counter()
        times.append(t1 - t0)

    total = len(texts)
    median = statistics.median(times)
    p90 = statistics.quantiles(times, n=10)[8]

    return {
        "model": model_name,
        "device": device,
        "batch_size": batch_size,
        "max_seq_length": max_seq_length,
        "texts": total,
        "median_s": round(median, 4),
        "p90_s": round(p90, 4),
        "throughput_texts_per_s": round(total / median, 2),
        "latency_ms_per_text": round((median / total) * 1000, 3),
    }



# %%
base_text = "This is a sample paragraph used for embedding benchmarks. " * 60
texts = [f"{i}. {base_text}" for i in range(2000)]

len(texts), len(texts[0])


# %%
device = "cuda" if torch.cuda.is_available() else "cpu"
device


# %%
results = []

for key, model_name in MODELS.items():
    prepared_texts = prep_texts(key, texts, as_query=False)
    res = bench(
        model_name,
        prepared_texts,
        device=device,
        batch_size=64,
        max_seq_length=256,
    )
    results.append(res)

results


# %%
import pandas as pd

df = pd.DataFrame(results)
df


# %%

# %%
