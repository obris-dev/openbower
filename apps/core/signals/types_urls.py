from openbower_kernel.urls import api_path

from . import views

# `/v1/signal-types`: the hosted signals a person can set up, and a type's
# recent events to try a setup on.
urlpatterns = [
    api_path("", views.SignalsCatalogView.as_view(), name="signals_catalog"),
    api_path("<str:signal>/events", views.SignalEventsView.as_view(), name="signals_events"),
]
