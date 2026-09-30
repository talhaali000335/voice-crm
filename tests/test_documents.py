from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.documents.ingest import chunk_text
from apps.documents.models import Chunk, Document
from rag_core.answer import NO_ANSWER, answer_question
from tests.helpers import make_pdf, vec

TEXT = "Our opening hours are nine to five, Monday to Friday."


def pdf_file(text=TEXT, name="faq.pdf"):
    return SimpleUploadedFile(name, make_pdf(text), content_type="application/pdf")


class ChunkingTests(SimpleTestCase):
    def test_empty(self):
        self.assertEqual(chunk_text("  \n "), [])

    def test_short_text_is_one_chunk(self):
        self.assertEqual(chunk_text("Hello there."), ["Hello there."])

    def test_long_text_is_split_with_overlap_and_nothing_is_lost(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(200))
        chunks = chunk_text(text, size=300, overlap=60)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 300 for c in chunks))
        self.assertTrue(chunks[0].startswith("Sentence number 0"))
        self.assertTrue(chunks[-1].endswith("Sentence number 199 is here."))

    def test_null_bytes_removed(self):
        self.assertNotIn("\x00", chunk_text("a\x00b")[0])


@patch("apps.documents.ingest.embed", side_effect=lambda texts: [vec(0) for _ in texts])
class UploadTests(TestCase):
    def setUp(self):
        cache.clear()          # rate-limit counters live in the cache
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def upload(self, f):
        return self.client.post("/api/documents/", {"file": f}, format="multipart")

    def test_staff_can_upload(self, _):
        r = self.upload(pdf_file())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Document.objects.get().title, "faq")
        self.assertGreaterEqual(Chunk.objects.count(), 1)
        self.assertEqual(Document.objects.get().uploaded_by, self.staff)

    def test_regular_user_forbidden(self, _):
        c = APIClient()
        c.force_authenticate(User.objects.create_user("norm", password="x"))
        self.assertEqual(c.post("/api/documents/", {"file": pdf_file()}, format="multipart").status_code, 403)
        self.assertEqual(c.get("/api/documents/").status_code, 403)

    def test_anonymous_rejected(self, _):
        self.assertEqual(APIClient().post("/api/documents/", {"file": pdf_file()}, format="multipart").status_code, 401)

    def test_duplicate_is_conflict(self, _):
        self.upload(pdf_file())
        self.assertEqual(self.upload(pdf_file()).status_code, 409)
        self.assertEqual(Document.objects.count(), 1)

    def test_fake_pdf_rejected(self, _):
        r = self.upload(SimpleUploadedFile("evil.pdf", b"MZ\x90 not a pdf", content_type="application/pdf"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Document.objects.count(), 0)

    def test_truncated_pdf_rejected(self, _):
        r = self.upload(SimpleUploadedFile("bad.pdf", make_pdf(TEXT)[:120], content_type="application/pdf"))
        self.assertEqual(r.status_code, 400)

    def test_wrong_extension_rejected(self, _):
        self.assertEqual(self.upload(pdf_file(name="faq.txt")).status_code, 400)

    def test_missing_file(self, _):
        self.assertEqual(self.client.post("/api/documents/", {}, format="multipart").status_code, 400)

    @override_settings(MAX_PDF_BYTES=100)
    def test_too_large(self, _):
        self.assertEqual(self.upload(pdf_file()).status_code, 400)

    @override_settings(MAX_PDF_PAGES=0)
    def test_too_many_pages(self, _):
        self.assertEqual(self.upload(pdf_file()).status_code, 400)

    def test_pdf_without_text_rejected(self, _):
        r = self.upload(pdf_file(text=""))
        self.assertEqual(r.status_code, 400)
        self.assertIn("No text", r.json()["error"])

    def test_list_and_delete(self, _):
        doc_id = self.upload(pdf_file()).json()["id"]
        self.assertEqual(len(self.client.get("/api/documents/").json()), 1)
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}/").status_code, 204)
        self.assertEqual(Chunk.objects.count(), 0)
        self.assertEqual(self.client.delete(f"/api/documents/{doc_id}/").status_code, 404)


class GroundedAnswerTests(TestCase):
    def setUp(self):
        doc = Document.objects.create(title="FAQ", filename="faq.pdf", sha256="a" * 64, pages=1, chunk_count=1)
        Chunk.objects.create(document=doc, position=0, text=TEXT, embedding=vec(0))

    @patch("rag_core.answer.embed", return_value=[vec(0)])
    @patch("rag_core.answer.chat", return_value="We are open nine to five on weekdays.")
    @patch("rag_core.retrieval.embed_one", return_value=vec(0))
    def test_grounded_answer_is_returned(self, *_):
        out = answer_question("when are you open?")
        self.assertTrue(out["grounded"])
        self.assertEqual(out["answer"], "We are open nine to five on weekdays.")

    @patch("rag_core.answer.chat")
    @patch("rag_core.retrieval.embed_one", return_value=vec(1))
    def test_irrelevant_question_never_reaches_the_llm(self, _, chat):
        self.assertEqual(answer_question("what is the meaning of life?")["answer"], NO_ANSWER)
        chat.assert_not_called()

    @patch("rag_core.answer.embed", return_value=[vec(1)])         # answer unrelated to the document
    @patch("rag_core.answer.chat", return_value="We also sell cars and boats.")
    @patch("rag_core.retrieval.embed_one", return_value=vec(0))
    def test_ungrounded_answer_is_blocked(self, *_):
        out = answer_question("when are you open?")
        self.assertFalse(out["grounded"])
        self.assertEqual(out["answer"], NO_ANSWER)

    @patch("rag_core.answer.chat", return_value="NOT_FOUND")
    @patch("rag_core.retrieval.embed_one", return_value=vec(0))
    def test_llm_not_found(self, *_):
        self.assertEqual(answer_question("what is your refund policy?")["answer"], NO_ANSWER)
