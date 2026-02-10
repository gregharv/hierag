from __future__ import annotations

import os
import threading

import torch
from sentence_transformers import SentenceTransformer

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from .fastlite_db import ensure_pipeline_schema, get_scraper_db
except ImportError:
    from core.fastlite_db import ensure_pipeline_schema, get_scraper_db

MODEL_NAME = "BAAI/bge-small-en-v1.5"
LLM_MODEL = "gpt-5.2"
VECTOR_CANDIDATE_K = 50
BM25_CANDIDATE_K = 50
FUSION_ALPHA = 0.70
BM25_K1 = 1.5
BM25_B = 0.75
RETRIEVAL_DEBUG = os.getenv("HYBRID_RETRIEVAL_DEBUG", "").lower() in {"1", "true", "yes"}

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

db = get_scraper_db()
_MODEL: SentenceTransformer | None = None
_MODEL_LOCK = threading.Lock()

if load_dotenv:
    load_dotenv()
else:
    print("python-dotenv not installed; set OPENAI_API_KEY in the environment.")


def get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            _MODEL = SentenceTransformer(MODEL_NAME, device=device)
            _MODEL.max_seq_length = 512
    return _MODEL


# %%
if __name__ == "__main__":
    test_db = get_scraper_db(":memory:")
    ensure_pipeline_schema(test_db)
    assert test_db.t.pages is not None
    print("Check Passed")
