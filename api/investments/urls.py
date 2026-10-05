from django.urls import path

from api.investments import views

urlpatterns = [
    path("investments", views.InvestmentListCreateView.as_view()),
    path("investments/<int:pk>", views.InvestmentDetailView.as_view()),
    path("suitability-test", views.SuitabilityTestView.as_view()),
    path("cart", views.CartView.as_view()),
    path("cart/<int:pk>", views.CartItemView.as_view()),
    path("reservations/eligible", views.ReservationEligibleView.as_view()),
    path("reservations", views.ReservationListCreateView.as_view()),
    path("reservations/<int:pk>", views.ReservationDetailView.as_view()),
]
