from django.contrib.auth import authenticate, get_user, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import UserBranchMembership
from accounts.serializers import SuperuserBranchMembershipSerializer, UserBranchMembershipSerializer
from warehouse.models import Branch


class CurrentUserBranchMembershipsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        user = get_user(request._request)
        if not user.is_authenticated:
            return Response({"detail": "Authentication is required."}, status=status.HTTP_403_FORBIDDEN)

        if user.is_superuser:
            serializer = SuperuserBranchMembershipSerializer(Branch.objects.filter(is_active=True), many=True)
            return Response(serializer.data)

        memberships = (
            UserBranchMembership.objects.select_related("branch")
            .filter(user=user, branch__is_active=True)
            .order_by("branch__code")
        )
        serializer = UserBranchMembershipSerializer(memberships, many=True)
        return Response(serializer.data)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AuthSessionView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        user = get_user(request._request)
        if not user.is_authenticated:
            return Response({"is_authenticated": False, "username": None, "is_superuser": False})
        return Response(
            {
                "is_authenticated": True,
                "username": user.username,
                "is_superuser": user.is_superuser,
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class AuthLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        if not username or not password:
            return Response({"detail": "Username and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request._request, username=username, password=password)
        if user is None:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            return Response({"detail": "This account is inactive."}, status=status.HTTP_403_FORBIDDEN)

        login(request._request, user)
        return Response(
            {
                "is_authenticated": True,
                "username": user.username,
                "is_superuser": user.is_superuser,
            }
        )


@method_decorator(csrf_protect, name="dispatch")
class AuthLogoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        logout(request._request)
        return Response({"message": "Logged out."})
