from django.db import models


class Review(models.Model):
    # One current submission per rule. The rule is locked while creating it.
    rule_id = models.UUIDField(db_index=True)
    snapshot = models.JSONField()
    decisions = models.JSONField(default=dict)
    state = models.CharField(max_length=16, default="pending")
    submitted_by = models.CharField(max_length=255)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
