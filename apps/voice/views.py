import logging
import time

import requests
from django.conf import settings
from django.http import HttpResponse
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from agent_core.service import run_agent
from mlops.metrics import VOICE_STT_SECONDS
from rag_core.llm import LLMUnavailable, transcribe

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 5 * 1024 * 1024      # 5 MB guardrail
AUDIO_EXTENSIONS = {                   # content type -> file extension (we never trust the client's file name)
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
}


class VoiceView(APIView):
    """Audio in -> transcript + agent reply out."""
    parser_classes = [MultiPartParser]

    def post(self, request):
        audio = request.FILES.get("audio")
        if not audio:
            return Response({"error": 'Send an audio file in the "audio" field.'}, status=400)
        if audio.size > MAX_AUDIO_BYTES:
            return Response({"error": "Audio is too large (max 5 MB)."}, status=400)
        ext = AUDIO_EXTENSIONS.get((audio.content_type or "").split(";")[0].strip().lower())
        if not ext:
            return Response({"error": "Unsupported audio type."}, status=400)

        start = time.time()
        try:
            transcript = transcribe(f"speech.{ext}", audio.read())
        except Exception:
            logger.exception("speech-to-text failed")
            return Response({"error": "Could not understand the audio. Please try again."}, status=502)
        VOICE_STT_SECONDS.observe(time.time() - start)

        if not transcript:
            return Response({"error": "I couldn't hear anything. Please try again."}, status=400)
        try:
            out = run_agent(request.user.id, transcript)
        except ValueError as e:
            return Response({"error": str(e), "transcript": transcript}, status=400)
        except LLMUnavailable:
            return Response({"error": "The assistant is busy right now.", "transcript": transcript}, status=503)
        except Exception:
            return Response({"error": "Something went wrong. Please try again.", "transcript": transcript},
                            status=500)
        return Response({"transcript": transcript, **out})


class SpeakView(APIView):
    """Text in -> MP3 audio out (ElevenLabs). Returns 501 if not configured,
    and the browser page then falls back to the free built-in voice."""
    parser_classes = [JSONParser]

    def post(self, request):
        if not (settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_VOICE_ID):
            return Response({"error": "ElevenLabs is not configured."}, status=501)
        body = request.data if isinstance(request.data, dict) else {}
        text = str(body.get("text", ""))[:600]
        if not text:
            return Response({"error": "No text."}, status=400)
        try:
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}",
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY,
                         "Content-Type": "application/json", "Accept": "audio/mpeg"},
                json={"text": text, "model_id": settings.ELEVENLABS_MODEL},
                timeout=20,
            )
        except requests.RequestException:
            return Response({"error": "Voice service unreachable."}, status=502)
        if r.status_code != 200:
            return Response({"error": "Voice service error."}, status=502)
        return HttpResponse(r.content, content_type="audio/mpeg")
