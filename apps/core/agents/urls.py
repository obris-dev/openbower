from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.AgentsView.as_view(), name="agents_index"),
    api_path("catalog", views.AgentCatalogView.as_view(), name="agents_catalog"),
    api_path("test", views.AgentTestView.as_view(), name="agents_test"),
    api_path("test/<str:id>", views.AgentTestRunView.as_view(), name="agents_test_run"),
    api_path("<str:id>", views.AgentDetailView.as_view(), name="agents_detail"),
]
