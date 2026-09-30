"""PDF validation, text extraction, chunking and embedding."""
import hashlib
import re
from io import BytesIO

from django.conf import settings
from django.db import transaction
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from rag_core.embeddings import embed

from .models import Chunk, Document


class IngestError(Exception):
    """A problem with the uploaded file that the uploader should be told about."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Split text into ~size-character chunks, preferring sentence/space boundaries, with overlap."""
    text = re.sub(r"[ \t]+", " ", text.replace("\x00", ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[max(start, end - 200):end]
            cut = max(window.rfind(". "), window.rfind("\n"), window.rfind(" "))
            if cut > 0:
                end = end - len(window) + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def read_pdf(data: bytes) -> tuple[str, int]:
    """Validate a PDF and return (text, page_count). Raises IngestError with a clear message."""
    if not data.startswith(b"%PDF-"):
        raise IngestError("That file is not a PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise IngestError("Password-protected PDFs are not supported.")
        pages = len(reader.pages)
        if pages == 0:
            raise IngestError("The PDF has no pages.")
        if pages > settings.MAX_PDF_PAGES:
            raise IngestError(f"PDF is too long (max {settings.MAX_PDF_PAGES} pages).")
        text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
    except IngestError:
        raise
    except (PyPdfError, ValueError, KeyError, RecursionError):
        raise IngestError("Could not read that PDF. It may be damaged.")
    text = text.replace("\x00", "").strip()
    if not text:
        raise IngestError("No text found. Scanned (image-only) PDFs are not supported.")
    return text, pages


def ingest_pdf(upload, title: str, user) -> Document:
    if upload.size > settings.MAX_PDF_BYTES:
        raise IngestError(f"File is too large (max {settings.MAX_PDF_BYTES // (1024 * 1024)} MB).")
    data = upload.read()
    if not (upload.name or "").lower().endswith(".pdf"):
        raise IngestError("File name must end in .pdf.")

    digest = hashlib.sha256(data).hexdigest()
    if Document.objects.filter(sha256=digest).exists():
        raise IngestError("This PDF has already been uploaded.", status=409)

    text, pages = read_pdf(data)
    pieces = chunk_text(text)
    if len(pieces) > settings.MAX_CHUNKS_PER_DOC:
        raise IngestError("Document is too large to index. Split it into smaller PDFs.")

    vectors = embed(pieces)
    safe_name = re.sub(r"[^\w.\- ]", "_", upload.name)[:255]
    with transaction.atomic():
        doc = Document.objects.create(
            title=(title or safe_name.rsplit(".", 1)[0])[:200], filename=safe_name, sha256=digest,
            pages=pages, chunk_count=len(pieces), uploaded_by=user)
        Chunk.objects.bulk_create(
            [Chunk(document=doc, position=i, text=t, embedding=v) for i, (t, v) in enumerate(zip(pieces, vectors))])
    return doc
