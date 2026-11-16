from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.SignalsView.as_view(), name="signals_index"),
    # Before the detail route, which would read "reconnect" as a signal id.
    api_path("reconnect", views.SignalsReconnectView.as_view(), name="signals_reconnect"),
    api_path("<str:id>", views.SignalDetailView.as_view(), name="signals_detail"),
    api_path("<str:id>/ingest", views.SignalIngestView.as_view(), name="signals_ingest"),
    api_path("<str:id>/ingest/<str:token>", views.SignalWebhookIngestView.as_view(), name="signals_webhook_ingest"),
    api_path("<str:id>/webhook/rotate", views.SignalWebhookRotateView.as_view(), name="signals_webhook_rotate"),
]
