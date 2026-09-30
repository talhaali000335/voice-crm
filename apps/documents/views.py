import logging

from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .ingest import IngestError, ingest_pdf
from .models import Document

logger = logging.getLogger(__name__)


def _row(d):
    return {"id": d.id, "title": d.title, "filename": d.filename, "pages": d.pages,
            "chunks": d.chunk_count, "created_at": d.created_at}


class DocumentListCreateView(APIView):
    """Staff only: manage the FAQ knowledge base."""
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser]

    def get(self, request):
        return Response([_row(d) for d in Document.objects.all()])

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"error": 'Send a PDF in the "file" field.'}, status=400)
        try:
            doc = ingest_pdf(upload, str(request.data.get("title", "")).strip(), request.user)
        except IngestError as e:
            return Response({"error": str(e)}, status=e.status)
        except Exception:
            logger.exception("document ingest failed")
            return Response({"error": "Could not process the PDF. Please try again."}, status=500)
        return Response(_row(doc), status=201)


class DocumentDetailView(APIView):
    permission_classes = [IsAdminUser]

    def delete(self, request, pk):
        deleted, _ = Document.objects.filter(pk=pk).delete()
        return Response(status=204 if deleted else 404)
