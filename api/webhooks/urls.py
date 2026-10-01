from django.urls import path

from api.webhooks import views

urlpatterns = [
    path("webhooks/bank/deposit", views.BankDepositWebhookView.as_view()),
    path("webhooks/bank/transfer", views.BankTransferWebhookView.as_view()),
]
