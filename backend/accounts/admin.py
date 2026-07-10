from django.contrib import admin

from accounts.models import UserBranchMembership


@admin.register(UserBranchMembership)
class UserBranchMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "branch", "role", "created_at"]
    list_filter = ["role", "branch"]
    search_fields = ["user__username", "branch__code", "branch__name"]
