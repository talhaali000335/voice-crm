from django.urls import path

from .views import DocumentDetailView, DocumentListCreateView

urlpatterns = [
    path("", DocumentListCreateView.as_view(), name="documents"),
    path("<int:pk>/", DocumentDetailView.as_view(), name="document-detail"),
]
