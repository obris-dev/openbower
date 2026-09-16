from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.WebhooksView.as_view(), name="webhooks_index"),
    api_path("<str:id>", views.WebhookDetailView.as_view(), name="webhooks_detail"),
    api_path("<str:id>/test", views.WebhookTestView.as_view(), name="webhooks_test"),
    api_path("<str:id>/deliveries", views.WebhookDeliveriesView.as_view(), name="webhooks_deliveries"),
]
