from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.SignalsCatalogView.as_view(), name="signals_catalog"),
    api_path("previews", views.PreviewsView.as_view(), name="signals_previews"),
    api_path("previews/<str:id>", views.PreviewDetailView.as_view(), name="signals_preview"),
    api_path("<str:signal>/events", views.SignalEventsView.as_view(), name="signals_events"),
]
