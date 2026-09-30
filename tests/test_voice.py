from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from rag_core.llm import LLMUnavailable


class VoiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("vera", password="x"))

    def audio(self, content_type="audio/webm", data=b"abc"):
        return {"audio": SimpleUploadedFile("speech", data, content_type=content_type)}

    def test_requires_login(self):
        self.assertEqual(APIClient().post("/api/voice/", self.audio(), format="multipart").status_code, 401)

    def test_no_file(self):
        self.assertEqual(self.client.post("/api/voice/", {}, format="multipart").status_code, 400)

    def test_unsupported_type(self):
        r = self.client.post("/api/voice/", self.audio("application/pdf"), format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_too_large(self):
        r = self.client.post("/api/voice/", self.audio(data=b"x" * (5 * 1024 * 1024 + 1)), format="multipart")
        self.assertEqual(r.status_code, 400)

    @patch("apps.voice.views.run_agent", return_value={"reply": "Hello", "intent": "faq"})
    @patch("apps.voice.views.transcribe", return_value="hi there")
    def test_happy_path_accepts_codec_suffix(self, stt, agent):
        r = self.client.post("/api/voice/", self.audio("audio/webm;codecs=opus"), format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"transcript": "hi there", "reply": "Hello", "intent": "faq"})
        self.assertEqual(stt.call_args[0][0], "speech.webm")          # extension chosen by us, not the client

    @patch("apps.voice.views.transcribe", side_effect=RuntimeError("boom"))
    def test_stt_failure_is_generic(self, _):
        r = self.client.post("/api/voice/", self.audio(), format="multipart")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("boom", r.content.decode())

    @patch("apps.voice.views.transcribe", return_value="")
    def test_silence(self, _):
        self.assertEqual(self.client.post("/api/voice/", self.audio(), format="multipart").status_code, 400)

    @patch("apps.voice.views.run_agent", side_effect=LLMUnavailable)
    @patch("apps.voice.views.transcribe", return_value="hello")
    def test_llm_down_returns_503(self, *_):
        self.assertEqual(self.client.post("/api/voice/", self.audio(), format="multipart").status_code, 503)

    def test_speak_not_configured(self):
        r = self.client.post("/api/voice/speak/", {"text": "hi"}, format="json")
        self.assertEqual(r.status_code, 501)

    @override_settings(ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="v")
    @patch("apps.voice.views.requests.post")
    def test_speak_returns_audio(self, post):
        post.return_value = MagicMock(status_code=200, content=b"MP3DATA")
        r = self.client.post("/api/voice/speak/", {"text": "hi"}, format="json")
        self.assertEqual((r.status_code, r["Content-Type"], r.content), (200, "audio/mpeg", b"MP3DATA"))

    @override_settings(ELEVENLABS_API_KEY="k", ELEVENLABS_VOICE_ID="v")
    @patch("apps.voice.views.requests.post")
    def test_speak_upstream_error(self, post):
        post.return_value = MagicMock(status_code=401, content=b"")
        self.assertEqual(self.client.post("/api/voice/speak/", {"text": "hi"}, format="json").status_code, 502)
