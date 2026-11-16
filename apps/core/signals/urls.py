from openbower_kernel.urls import api_path

from . import views

# `/v1/signals`: an account's signals (a sheet of kind log and its sources).
urlpatterns = [
    api_path("", views.LogsView.as_view(), name="logs_index"),
    # Before the detail route, which would read "reconnect" as a signal id.
    api_path("reconnect", views.LogsReconnectView.as_view(), name="logs_reconnect"),
    api_path("<str:id>", views.LogDetailView.as_view(), name="logs_detail"),
]
