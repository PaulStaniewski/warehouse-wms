from django.contrib import admin
from django.urls import path

from config.views import health_check


urlpatterns = [
    path("api/health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
]
