"""Groq chat + speech-to-text, with model fallbacks."""
import logging

from django.conf import settings
from groq import Groq

logger = logging.getLogger(__name__)
_client = None


class LLMUnavailable(Exception):
    """Every configured model failed."""


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=settings.GROQ_API_KEY, timeout=20.0, max_retries=1)
    return _client


def chat(prompt: str, *, system: str | None = None, temperature: float = 0.0, max_tokens: int = 400) -> str:
    """Try each model in settings.LLM_MODELS until one answers."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for model in settings.LLM_MODELS:
        try:
            r = _get_client().chat.completions.create(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            return (r.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("LLM model %s failed: %s", model, type(exc).__name__)
    raise LLMUnavailable("All language models failed.")


def transcribe(filename: str, data: bytes) -> str:
    r = _get_client().audio.transcriptions.create(file=(filename, data), model=settings.STT_MODEL)
    return (r.text or "").strip()
