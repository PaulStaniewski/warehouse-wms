from django.db.models import QuerySet
from rest_framework.exceptions import NotAuthenticated, PermissionDenied

from accounts.models import UserBranchMembership
from warehouse.models import Branch


LEADER_ACTION_TYPES = {
    "confirm_shortage",
    "record_final_reconciliation_outcome",
}


def require_authenticated(user):
    if not user or not user.is_authenticated:
        raise NotAuthenticated("Authentication is required for this WMS operation.")


def allowed_branch_codes(user) -> set[str]:
    require_authenticated(user)
    if user.is_superuser:
        return set(Branch.objects.values_list("code", flat=True))
    return set(
        UserBranchMembership.objects.filter(user=user).values_list("branch__code", flat=True)
    )


def branch_queryset_for_user(user) -> QuerySet:
    require_authenticated(user)
    if user.is_superuser:
        return Branch.objects.all()
    return Branch.objects.filter(user_memberships__user=user).distinct()


def branch_codes_filter(user, requested_code: str = "") -> set[str]:
    allowed = allowed_branch_codes(user)
    if not requested_code:
        return allowed
    matched = {code for code in allowed if code.lower() == requested_code.lower()}
    if not matched:
        raise PermissionDenied("You do not have access to this branch or operation.")
    return matched


def branch_ids_filter(user, requested_id: str = "") -> set[int]:
    allowed = set(branch_queryset_for_user(user).values_list("id", flat=True))
    if not requested_id:
        return allowed
    try:
        branch_id = int(requested_id)
    except (TypeError, ValueError):
        raise PermissionDenied("You do not have access to this branch or operation.")
    if branch_id not in allowed:
        raise PermissionDenied("You do not have access to this branch or operation.")
    return {branch_id}


def membership_role(user, branch: Branch) -> str | None:
    require_authenticated(user)
    if user.is_superuser:
        return UserBranchMembership.Role.LEADER
    return (
        UserBranchMembership.objects.filter(user=user, branch=branch)
        .values_list("role", flat=True)
        .first()
    )


def has_branch_access(user, branch: Branch) -> bool:
    return membership_role(user, branch) is not None


def require_branch_access(user, branch: Branch, *, leader_required: bool = False):
    role = membership_role(user, branch)
    if role is None:
        raise PermissionDenied("You do not have access to this branch or operation.")
    if leader_required and role != UserBranchMembership.Role.LEADER:
        raise PermissionDenied("This action requires a Leader role.")
    return role


def require_any_branch_access(user, branches, *, leader_required: bool = False):
    require_authenticated(user)
    for branch in branches:
        role = membership_role(user, branch)
        if role is None:
            continue
        if not leader_required or role == UserBranchMembership.Role.LEADER:
            return role
    if leader_required:
        raise PermissionDenied("This action requires a Leader role in a participating branch.")
    raise PermissionDenied("You do not have access to this branch or operation.")


def filter_rows_for_user(rows: list[dict], user, active_branch: str = "") -> list[dict]:
    allowed_codes = {code.lower() for code in allowed_branch_codes(user)}
    active = active_branch.lower()
    filtered = []
    for row in rows:
        visible_codes = {code.lower() for code in row.get("visible_branches", [])}
        if active and active not in visible_codes:
            continue
        if not visible_codes.intersection(allowed_codes):
            continue
        if row.get("action_type") in LEADER_ACTION_TYPES:
            if not any(_row_branch_role_is_leader(user, code) for code in visible_codes):
                continue
        filtered.append(row)
    return filtered


def _row_branch_role_is_leader(user, branch_code: str) -> bool:
    if user.is_superuser:
        return True
    return UserBranchMembership.objects.filter(
        user=user,
        branch__code__iexact=branch_code,
        role=UserBranchMembership.Role.LEADER,
    ).exists()
