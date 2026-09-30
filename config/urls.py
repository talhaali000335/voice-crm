from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from mlops.views import metrics, ready

urlpatterns = [
    # /health/ (liveness) is answered by HealthCheckMiddleware
    path("health/ready/", ready),
    path("health/metrics/", metrics),
    path("admin/", admin.site.urls),
    path("api/token/", TokenObtainPairView.as_view(), name="token"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/agent/", include("apps.agent.urls")),
    path("api/voice/", include("apps.voice.urls")),
    path("api/documents/", include("apps.documents.urls")),
    path("voice/", TemplateView.as_view(template_name="voice.html")),
    path("", TemplateView.as_view(template_name="voice.html")),
]
