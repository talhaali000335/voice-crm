"""Local embeddings (fastembed / ONNX). The model is baked into the Docker image and loaded on first use."""
import threading

from django.conf import settings

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding(model_name=settings.EMBEDDING_MODEL,
                                       cache_dir=settings.EMBEDDING_CACHE_DIR or None)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [v.tolist() for v in _get_model().embed(texts, batch_size=32)]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
