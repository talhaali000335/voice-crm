from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "filename", "pages", "chunk_count", "uploaded_by", "created_at")
    readonly_fields = ("sha256", "pages", "chunk_count", "uploaded_by")
