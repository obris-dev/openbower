from django.conf import settings
from django.urls import include, path, re_path

from openbower_kernel.urls import api_include
from openbower_kernel.views import healthz

# `api_include` makes the trailing slash on the prefix optional; `api_path`
# (inside each app's urlconf) makes the trailing slash on each leaf route
# optional. `/healthz` stays unversioned.
_V1 = settings.API_VERSION_PREFIX

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    api_include(f"{_V1}/auth", "auth_client.urls"),
    api_include(f"{_V1}/sheets", "sheets.urls"),
    # The sheets' old path, for API keys still pushing there; to come out a
    # release after the web stopped calling it. Namespaced so the
    # unnamespaced names, and so every reverse(), stay on /sheets (two
    # unnamespaced includes would leave each name on whichever came last).
    re_path(rf"^{_V1}/lists/?", include(("sheets.urls", "sheets"), namespace="lists_alias")),
    api_include(f"{_V1}/runs", "sheets.runs_urls"),
    api_include(f"{_V1}/agents", "agents.urls"),
    api_include(f"{_V1}/webhooks", "webhooks.urls"),
]
