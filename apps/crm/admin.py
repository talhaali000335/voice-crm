from django.contrib import admin

from .models import Appointment, Ticket


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("service", "customer", "starts_at", "status")
    list_filter = ("status",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "priority", "status", "created_at")
    list_filter = ("priority", "status")
