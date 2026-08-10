from openbower_kernel.urls import api_path

from . import views

urlpatterns = [
    api_path("login", views.LoginRedirectView.as_view(), name="auth_login"),
    api_path("callback", views.CallbackView.as_view(), name="auth_callback"),
    api_path("logout", views.LogoutView.as_view(), name="auth_logout"),
    api_path("me", views.MeView.as_view(), name="auth_me"),
]
