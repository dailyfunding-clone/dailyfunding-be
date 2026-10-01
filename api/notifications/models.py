from django.conf import settings
from django.db import models


class Device(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices"
    )
    expo_push_token = models.CharField(max_length=100, unique=True)
    platform = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationSetting(models.Model):
    """알림 카테고리별 ON/OFF (F-APP-05)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notif_setting"
    )
    new_product = models.BooleanField(default=True)
    recruit_closed = models.BooleanField(default=True)
    repayment = models.BooleanField(default=True)


class Notification(models.Model):
    """알림 내역 (푸시 발행 트리거의 결과 로그)."""

    class Kind(models.TextChoices):
        NEW_PRODUCT = "new_product"
        RECRUIT_CLOSED = "recruit_closed"
        REPAYMENT = "repayment"
        SYSTEM = "system"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


def notify(user_ids, kind, title, body="", ref_id=""):
    """설정이 ON인 사용자에게 알림 레코드 생성."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    setting_field = {
        Notification.Kind.NEW_PRODUCT: "new_product",
        Notification.Kind.RECRUIT_CLOSED: "recruit_closed",
        Notification.Kind.REPAYMENT: "repayment",
    }.get(kind)
    for uid in user_ids:
        if setting_field:
            setting = NotificationSetting.objects.filter(user_id=uid).first()
            if setting and not getattr(setting, setting_field):
                continue
        Notification.objects.create(
            user_id=uid, kind=kind, title=title, body=body, ref_id=str(ref_id)
        )
