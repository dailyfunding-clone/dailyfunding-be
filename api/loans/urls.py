from django.urls import path

from api.loans import views

urlpatterns = [
    path("loans", views.LoanProductListView.as_view()),
    path("loans/<int:pk>", views.LoanProductDetailView.as_view()),
    path("loans/limit-check", views.LimitCheckView.as_view()),
    path("loans/applications", views.LoanApplicationView.as_view()),
]
