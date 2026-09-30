"""Health check, abuse protection and input sanitization."""
import json
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)


class HealthCheckMiddleware:
    """Answers /health/ for the load balancer.

    It sits before every other middleware so the ALB (which calls the task by its private IP,
    a Host header that is not in ALLOWED_HOSTS) never triggers Django's host validation.
    It does not touch the database; /health/ready/ does the deep check.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


def client_ip(request) -> str:
    """Real client IP when running behind N trusted proxies (CloudFront + ALB = 2).

    Each proxy appends the address it saw, so the trusted part is the END of X-Forwarded-For.
    Anything the client put at the start of the header is ignored, so it cannot be spoofed.
    """
    n = settings.TRUSTED_PROXY_COUNT
    parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if n > 0 and len(parts) >= n:
        return parts[-n]
    return request.META.get("REMOTE_ADDR", "unknown")


# (path prefix, bucket name, max requests, window in seconds). First match wins.
RATE_RULES = (
    ("/api/auth/register/", "register", 5, 3600),
    ("/api/token/", "login", 10, 60),
    ("/api/voice/speak/", "speak", 30, 60),
    ("/api/voice/", "voice", 20, 60),
    ("/api/documents/", "docs", 10, 60),
    ("/api/", "api", 60, 60),
)


def hit(key: str, limit: int, window: int) -> bool:
    """Atomic fixed-window counter. True while the caller is still within the limit.

    cache.add is SET-if-not-exists (it creates the key AND its expiry in one step) and
    cache.incr is an atomic increment, so concurrent requests can never slip past the limit.
    """
    if cache.add(key, 1, window):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:               # key expired between add() and incr()
        cache.add(key, 1, window)
        return True


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        for prefix, name, limit, window in RATE_RULES:
            if request.path.startswith(prefix):
                try:
                    allowed = hit(f"rl:{name}:{client_ip(request)}", limit, window)
                except Exception:        # cache down: fail open, but leave a trace
                    logger.exception("rate limiter unavailable")
                    allowed = True
                if not allowed:
                    resp = JsonResponse({"error": "Too many requests. Please wait and try again."}, status=429)
                    resp["Retry-After"] = str(window if window <= 60 else 60)
                    return resp
                break
        return self.get_response(request)


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HTML_TAGS = re.compile(r"</?[a-zA-Z][^>]*>")
SECRET_KEYS = {"password", "password2", "old_password", "new_password", "current_password", "invite_code"}
MAX_JSON_BYTES = 64 * 1024


def _clean(value, key=None):
    if isinstance(value, str):
        if key in SECRET_KEYS:          # never alter passwords: what the user typed is what gets hashed
            return value
        return HTML_TAGS.sub("", CONTROL_CHARS.sub("", value)).strip()
    if isinstance(value, dict):
        return {k: _clean(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, key) for v in value]
    return value


class InputSanitizationMiddleware:
    """Strips control characters and HTML tags from JSON request bodies (password fields excluded)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/") and request.content_type == "application/json":
            length = int(request.META.get("CONTENT_LENGTH") or 0)
            if length > MAX_JSON_BYTES:
                return JsonResponse({"error": "Request too large."}, status=413)
            try:
                data = json.loads(request.body or b"null")
            except (ValueError, UnicodeDecodeError):
                data = None               # let DRF report the parse error
            if data is not None:
                request._body = json.dumps(_clean(data)).encode()
        return self.get_response(request)
