from django.contrib.auth.models import User
from django.db import models


class Appointment(models.Model):
    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    service = models.CharField(max_length=100)
    starts_at = models.DateTimeField()
    status = models.CharField(max_length=20, default="booked")   # booked / cancelled
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            # Two customers cannot hold the same slot (cancelled ones don't count)
            models.UniqueConstraint(
                fields=["starts_at"],
                condition=models.Q(status="booked"),
                name="one_booking_per_slot",
            )
        ]

    def __str__(self):
        return f"{self.service} @ {self.starts_at:%Y-%m-%d %H:%M}"


class Ticket(models.Model):
    customer = models.ForeignKey(User, on_delete=models.CASCADE)
    summary = models.TextField()
    priority = models.CharField(max_length=10, default="normal")   # low / normal / high
    status = models.CharField(max_length=20, default="open")       # open / closed
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.id} [{self.priority}] {self.summary[:40]}"
