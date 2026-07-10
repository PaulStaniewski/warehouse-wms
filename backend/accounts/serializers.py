from rest_framework import serializers

from accounts.models import UserBranchMembership
from warehouse.models import Branch


class UserBranchMembershipSerializer(serializers.ModelSerializer):
    branch_id = serializers.IntegerField(source="branch.id", read_only=True)
    branch_code = serializers.CharField(source="branch.code", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    branch_city = serializers.CharField(source="branch.city", read_only=True)
    branch_country = serializers.CharField(source="branch.country", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = UserBranchMembership
        fields = [
            "branch_id",
            "branch_code",
            "branch_name",
            "branch_city",
            "branch_country",
            "role",
            "role_label",
        ]


class SuperuserBranchMembershipSerializer(serializers.ModelSerializer):
    branch_id = serializers.IntegerField(source="id", read_only=True)
    branch_code = serializers.CharField(source="code", read_only=True)
    branch_name = serializers.CharField(source="name", read_only=True)
    branch_city = serializers.CharField(source="city", read_only=True)
    branch_country = serializers.CharField(source="country", read_only=True)
    role = serializers.SerializerMethodField()
    role_label = serializers.SerializerMethodField()

    def get_role(self, obj: Branch) -> str:
        return UserBranchMembership.Role.LEADER

    def get_role_label(self, obj: Branch) -> str:
        return "Leader"

    class Meta:
        model = Branch
        fields = [
            "branch_id",
            "branch_code",
            "branch_name",
            "branch_city",
            "branch_country",
            "role",
            "role_label",
        ]
