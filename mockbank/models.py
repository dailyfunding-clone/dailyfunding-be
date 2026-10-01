from django.db import models


class WebhookDelivery(models.Model):
    """모의 은행이 발행한 웹훅의 전송 이력. 실패 시 지수 백오프 재시도 대상."""

    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"  # 재시도 예정
        DEAD = "dead"  # 8회 초과

    url = models.CharField(max_length=300)
    payload = models.JSONField()
    event_id = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=8)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
