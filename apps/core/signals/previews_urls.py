from openbower_kernel.urls import api_path

from . import views

# `/v1/signal-previews`: a setup tried on chosen events before it is saved.
urlpatterns = [
    api_path("", views.PreviewsView.as_view(), name="signals_previews"),
    api_path("<str:id>", views.PreviewDetailView.as_view(), name="signals_preview"),
]
