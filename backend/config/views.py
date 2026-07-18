from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

try:
    import redis
except ImportError:  # pragma: no cover - dependency is installed in normal runtime
    redis = None


@require_GET
def health_check(request):
    return JsonResponse({"status": "ok"})


@require_GET
def liveness_check(request):
    return JsonResponse({"status": "ok"})


@require_GET
def readiness_check(request):
    checks = {"database": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "not_ready", "checks": {"database": "unavailable"}}, status=503)

    if settings.READINESS_CHECK_REDIS:
        if redis is None:
            return JsonResponse({"status": "not_ready", "checks": {"database": "ok", "redis": "unavailable"}}, status=503)
        try:
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception:
            return JsonResponse({"status": "not_ready", "checks": {"database": "ok", "redis": "unavailable"}}, status=503)

    return JsonResponse({"status": "ready", "checks": checks})
