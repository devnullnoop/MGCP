"""Centralized embedding model for MGCP.

Uses BAAI/bge-base-en-v1.5 for improved retrieval quality over all-MiniLM-L6-v2.
- Dimensions: 768 (vs 384)
- MTEB benchmark: ~7% better NDCG
- Size: ~415MB (downloaded on first use)

API Reference:
- sentence-transformers: https://www.sbert.net/
- BGE models: https://huggingface.co/BAAI/bge-base-en-v1.5
"""

import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

logger = logging.getLogger("mgcp.embedding")

# Model configuration
MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIMENSION = 768


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Get the shared embedding model instance.

    Uses lru_cache to ensure single instance across all stores.
    First call downloads the model (~415MB) if not cached.
    """
    logger.info(f"Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    logger.info(f"Embedding model loaded (dimension={EMBEDDING_DIMENSION})")
    return model


def embed(text: str) -> list[float]:
    """Embed a single text string.

    Args:
        text: Text to embed

    Returns:
        List of floats representing the embedding vector (768 dimensions)
    """
    model = get_embedding_model()
    # encode() returns numpy array, convert to list for Qdrant
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts efficiently.

    Args:
        texts: List of texts to embed

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []

    model = get_embedding_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]
