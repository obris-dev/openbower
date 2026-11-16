from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.SignalPreviewsView.as_view(), name="signal_previews_index"),
    api_path("<str:id>", views.SignalPreviewView.as_view(), name="signal_previews_detail"),
]
