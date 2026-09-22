from django.conf import settings
from django.urls import path

from openbower_kernel.urls import api_include
from openbower_kernel.views import healthz

# `api_include` makes the trailing slash on the prefix optional; `api_path`
# (inside each app's urlconf) makes the trailing slash on each leaf route
# optional. `/healthz` stays unversioned.
_V1 = settings.API_VERSION_PREFIX

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    api_include(f"{_V1}/auth", "auth_client.urls"),
    api_include(f"{_V1}/discover", "discover.urls"),
    api_include(f"{_V1}/lists", "lists.urls"),
    api_include(f"{_V1}/runs", "lists.runs_urls"),
    api_include(f"{_V1}/agents", "agents.urls"),
    api_include(f"{_V1}/webhooks", "webhooks.urls"),
]
