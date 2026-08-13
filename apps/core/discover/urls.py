from openbower_kernel.urls import api_path

from . import views

# Only the routes that genuinely talk to the data service (the look-alike
# engine). Local domains (lists, agents) mount their own prefixes as their
# phases land.
urlpatterns = [
    api_path("lookalikes", views.LookalikesView.as_view(), name="discover_lookalikes"),
    api_path("lookalikes/runs/<str:id>", views.LookalikeRunView.as_view(), name="discover_lookalike_run"),
    api_path(
        "lookalikes/runs/<str:id>/cancel",
        views.LookalikeRunCancelView.as_view(),
        name="discover_lookalike_run_cancel",
    ),
]
