from django.conf import settings
from pgvector.django import CosineDistance

from apps.documents.models import Chunk

from .embeddings import embed_one


def retrieve(question: str) -> list[dict]:
    """Top-k most similar chunks. 'sim' is cosine similarity (1.0 = identical meaning)."""
    qvec = embed_one(question)
    rows = (Chunk.objects.select_related("document")
            .annotate(distance=CosineDistance("embedding", qvec))
            .order_by("distance")[:settings.RETRIEVAL_TOP_K])
    return [{"text": c.text, "title": c.document.title, "sim": 1.0 - float(c.distance),
             "vec": list(c.embedding)} for c in rows]
