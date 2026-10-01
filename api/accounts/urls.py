from django.urls import path

from api.accounts import views

urlpatterns = [
    path("auth/signup", views.SignupView.as_view()),
    path("auth/signup/borrower", views.BorrowerSignupView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/login/pin", views.PinLoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/refresh", views.RefreshView.as_view()),
    path("auth/identity/verify", views.IdentityVerifyView.as_view()),
    path("auth/pin", views.PinRegisterView.as_view()),
    path("auth/reauth", views.ReauthView.as_view()),
    path("auth/app-code", views.AppCodeIssueView.as_view()),
    path("auth/app-code/exchange", views.AppCodeExchangeView.as_view()),
    path("me", views.MeView.as_view()),
]
