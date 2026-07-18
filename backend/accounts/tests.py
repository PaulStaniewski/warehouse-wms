from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import UserBranchMembership
from warehouse.models import Branch


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class AuthSessionWorkflowTests(APITestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="GDY", name="Magazyn Gdynia", city="Gdynia", country="Poland")
        User = get_user_model()
        self.leader = User.objects.create_user(username="GDY_LEADER", password="demo12345")
        self.worker = User.objects.create_user(username="GDY_WORKER", password="demo12345")
        UserBranchMembership.objects.create(
            user=self.leader,
            branch=self.branch,
            role=UserBranchMembership.Role.LEADER,
        )
        UserBranchMembership.objects.create(
            user=self.worker,
            branch=self.branch,
            role=UserBranchMembership.Role.WORKER,
        )

    def login(self, username):
        return self.client.post(
            "/api/auth/login/",
            {"username": username, "password": "demo12345"},
            format="json",
        )

    def memberships(self):
        return self.client.get("/api/me/branch-memberships/")

    def test_logout_clears_authenticated_session_and_memberships(self):
        login_response = self.login("GDY_LEADER")
        leader_memberships = self.memberships()

        logout_response = self.client.post("/api/auth/logout/", {}, format="json")
        session_response = self.client.get("/api/auth/session/")
        logged_out_memberships = self.memberships()

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(leader_memberships.data[0]["role"], "leader")
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)
        self.assertEqual(session_response.status_code, status.HTTP_200_OK)
        self.assertFalse(session_response.data["is_authenticated"])
        self.assertEqual(logged_out_memberships.status_code, status.HTTP_403_FORBIDDEN)

    def test_worker_login_after_leader_logout_receives_worker_role(self):
        self.login("GDY_LEADER")
        self.client.post("/api/auth/logout/", {}, format="json")

        worker_login = self.login("GDY_WORKER")
        worker_memberships = self.memberships()

        self.assertEqual(worker_login.status_code, status.HTTP_200_OK)
        self.assertEqual(worker_memberships.status_code, status.HTTP_200_OK)
        self.assertEqual(worker_memberships.data[0]["branch_code"], "GDY")
        self.assertEqual(worker_memberships.data[0]["role"], "worker")

    def test_cookie_login_requires_csrf_when_csrf_checks_are_enforced(self):
        csrf_client = APIClient(enforce_csrf_checks=True)

        missing_csrf = csrf_client.post(
            "/api/auth/login/",
            {"username": "GDY_WORKER", "password": "demo12345"},
            format="json",
        )
        session_response = csrf_client.get("/api/auth/session/")
        token = csrf_client.cookies["csrftoken"].value
        login_response = csrf_client.post(
            "/api/auth/login/",
            {"username": "GDY_WORKER", "password": "demo12345"},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(missing_csrf.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(session_response.status_code, status.HTTP_200_OK)
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_health_and_readiness_are_public_and_minimal(self):
        live = self.client.get("/api/health/live/")
        ready = self.client.get("/api/health/ready/")

        self.assertEqual(live.status_code, status.HTTP_200_OK)
        self.assertEqual(live.json(), {"status": "ok"})
        self.assertEqual(ready.status_code, status.HTTP_200_OK)
        self.assertEqual(ready.json()["status"], "ready")
        self.assertEqual(ready.json()["checks"], {"database": "ok"})

    def test_anonymous_operational_api_remains_denied(self):
        response = self.client.get("/api/products/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
