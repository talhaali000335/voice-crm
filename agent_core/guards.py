import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def mask_pii(text: str) -> str:
    """Hide emails and phone numbers. Use this for LOGS, never for replies."""
    text = EMAIL.sub("[email]", text)
    return PHONE.sub("[phone]", text)


def clean_for_speech(text: str, limit: int = 600) -> str:
    """Remove markdown symbols (a voice would read them out) and cap the length."""
    text = re.sub(r"[*#`_>]", "", text or "")
    return text.strip()[:limit]
