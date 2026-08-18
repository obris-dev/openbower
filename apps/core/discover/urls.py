from openbower_kernel.urls import api_path

from . import views

# Only the routes that genuinely talk to the data service (the look-alike
# engine); local domains mount their own prefixes. save-list lives HERE,
# not under /lists, because its work is paging the data service (the
# list is just where the rows land).
urlpatterns = [
    api_path("lookalikes", views.LookalikesView.as_view(), name="discover_lookalikes"),
    api_path("lookalikes/runs/<str:id>", views.LookalikeRunView.as_view(), name="discover_lookalike_run"),
    api_path(
        "lookalikes/runs/<str:id>/cancel",
        views.LookalikeRunCancelView.as_view(),
        name="discover_lookalike_run_cancel",
    ),
    api_path(
        "lookalikes/runs/<str:id>/save-list",
        views.LookalikeRunSaveListView.as_view(),
        name="discover_lookalike_run_save_list",
    ),
]
