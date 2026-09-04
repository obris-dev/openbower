from openbower_kernel.urls import api_path

from .fills_views import FillRunCancelView, FillRunDetailView, TestFillView

urlpatterns = [
    # Literal routes before the id catch-all, the agents urlconf's rule.
    api_path("test", TestFillView.as_view(), name="fills_test"),
    api_path("<str:id>", FillRunDetailView.as_view(), name="fills_detail"),
    api_path("<str:id>/cancel", FillRunCancelView.as_view(), name="fills_cancel"),
]
