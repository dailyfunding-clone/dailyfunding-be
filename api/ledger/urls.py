from django.urls import path

from api.ledger import views

urlpatterns = [
    path("deposit/account", views.DepositAccountView.as_view()),
    path("deposit/notify-intent", views.DepositNotifyIntentView.as_view()),
    path("deposit/withdraw", views.WithdrawView.as_view()),
    path("deposit/history", views.DepositHistoryView.as_view()),
    path("deposit/linked-account", views.LinkedAccountView.as_view()),
    path("deposit/auto-charge", views.AutoChargeView.as_view()),
    path("points", views.PointsView.as_view()),
    path("points/history", views.PointsHistoryView.as_view()),
    path("points/convert", views.PointsConvertView.as_view()),
]
