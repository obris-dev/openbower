from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.LogsView.as_view(), name="logs_index"),
    api_path("<str:id>", views.LogDetailView.as_view(), name="logs_detail"),
]
