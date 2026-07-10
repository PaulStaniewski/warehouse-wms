from django.conf import settings
from django.db import models

from warehouse.models import Branch


class UserBranchMembership(models.Model):
    class Role(models.TextChoices):
        WORKER = "worker", "Worker"
        LEADER = "leader", "Leader"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="branch_memberships",
    )
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="user_memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.WORKER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username", "branch__code"]
        constraints = [
            models.UniqueConstraint(fields=["user", "branch"], name="unique_user_branch_membership"),
        ]
        indexes = [
            models.Index(fields=["user", "branch"]),
            models.Index(fields=["branch", "role"]),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} / {self.branch.code} / {self.get_role_display()}"
