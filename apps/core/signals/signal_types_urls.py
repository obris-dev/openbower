from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.SignalTypesView.as_view(), name="signal_types_index"),
    api_path("<str:signal_type>/events", views.SignalTypeEventsView.as_view(), name="signal_types_events"),
]
