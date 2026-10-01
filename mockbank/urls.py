from django.urls import path

from mockbank import views

urlpatterns = [
    path("deposits/execute", views.DepositExecuteView.as_view()),
    path("transfers/execute", views.TransferExecuteView.as_view()),
    path("deliveries", views.DeliveryListView.as_view()),
]
