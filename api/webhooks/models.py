from django.db import models


class WebhookEvent(models.Model):
    """수신 웹훅 로그. event_id 유니크로 멱등 처리."""

    class Status(models.TextChoices):
        RECEIVED = "received"
        PROCESSED = "processed"
        HELD = "held"
        FAILED = "failed"

    event_id = models.CharField(max_length=64, unique=True)
    type = models.CharField(max_length=40)
    payload = models.JSONField()
    signature = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.RECEIVED
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
