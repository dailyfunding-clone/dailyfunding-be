from django.conf import settings
from django.db import models


class Notice(models.Model):
    class Category(models.TextChoices):
        IMPORTANT = "important", "중요공지"
        NOTICE = "notice", "공지"

    category = models.CharField(max_length=20, choices=Category.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    attachments = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class Faq(models.Model):
    category = models.CharField(max_length=40)
    question = models.CharField(max_length=300)
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Event(models.Model):
    class Status(models.TextChoices):
        ONGOING = "ongoing"
        WINNERS = "winners"
        ENDED = "ended"

    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ONGOING
    )
    thumbnail_url = models.CharField(max_length=300, blank=True)
    reward_points = models.PositiveIntegerField(default=0)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EventEntry(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")


class Disclosure(models.Model):
    """공시 (F-CON-04): 연/월 + 탭 3종 데이터."""

    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    kpi = models.JSONField(default=dict)
    management = models.JSONField(default=dict)
    operations = models.JSONField(default=dict)
    internal = models.JSONField(default=dict)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("year", "month")


class News(models.Model):
    title = models.CharField(max_length=300)
    source = models.CharField(max_length=100, blank=True)
    url = models.CharField(max_length=500)
    thumbnail_url = models.CharField(max_length=300, blank=True)
    published_at = models.DateField()


class Term(models.Model):
    """약관 6종 (F-CON-06)."""

    key = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=100)
    body = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)
