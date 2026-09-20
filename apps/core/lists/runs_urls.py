from openbower_kernel.urls import api_path

from .runs_views import BenchRunView, RunCancelView, RunDetailView

urlpatterns = [
    # Literal routes before the id catch-all, the agents urlconf's rule.
    api_path("bench", BenchRunView.as_view(), name="runs_bench"),
    api_path("<str:id>", RunDetailView.as_view(), name="runs_detail"),
    api_path("<str:id>/cancel", RunCancelView.as_view(), name="runs_cancel"),
]
