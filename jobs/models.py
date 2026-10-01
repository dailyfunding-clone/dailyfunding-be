from django.db import models


class ReconcileReport(models.Model):
    """일일 대사 배치 결과."""

    run_at = models.DateTimeField()
    ok = models.BooleanField()
    diffs = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class BatchRun(models.Model):
    """배치 실행 기록 (감사/재실행 추적)."""

    name = models.CharField(max_length=40)
    run_for = models.DateField()
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
