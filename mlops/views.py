import hmac

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def ready(request):
    """Deep check: database and cache reachable. Use for debugging, not as the ALB health check."""
    status, code = {}, 200
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        status["database"] = "ok"
    except Exception:
        status["database"], code = "down", 503
    try:
        cache.set("health", 1, 5)
        status["cache"] = "ok" if cache.get("health") == 1 else "down"
    except Exception:
        status["cache"] = "down"
    if status["cache"] == "down":
        code = 503
    return JsonResponse(status, status=code)


def metrics(request):
    token = settings.METRICS_TOKEN
    if not token:
        raise Http404
    if not hmac.compare_digest(request.headers.get("X-Metrics-Token", ""), token):
        return HttpResponse(status=403)
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
