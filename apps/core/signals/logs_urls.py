from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.LogsView.as_view(), name="logs_index"),
    # Before the detail route, which would read "reconnect" as a log id.
    api_path("reconnect", views.LogsReconnectView.as_view(), name="logs_reconnect"),
    api_path("<str:id>", views.LogDetailView.as_view(), name="logs_detail"),
]
