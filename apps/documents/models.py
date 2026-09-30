from django.contrib.auth.models import User
from django.db import models
from pgvector.django import HnswIndex, VectorField

EMBEDDING_DIM = 384   # BAAI/bge-small-en-v1.5. A different model needs a new migration.


class Document(models.Model):
    title = models.CharField(max_length=200)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, unique=True)   # stops the same PDF being ingested twice
    pages = models.PositiveIntegerField()
    chunk_count = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class Chunk(models.Model):
    document = models.ForeignKey(Document, related_name="chunks", on_delete=models.CASCADE)
    position = models.PositiveIntegerField()
    text = models.TextField()
    embedding = VectorField(dimensions=EMBEDDING_DIM)

    class Meta:
        ordering = ["document_id", "position"]
        indexes = [
            HnswIndex(name="chunk_embedding_hnsw", fields=["embedding"], m=16,
                      ef_construction=64, opclasses=["vector_cosine_ops"]),
        ]
