from django.urls import path

from api.notifications import views

urlpatterns = [
    path("devices", views.DeviceView.as_view()),
    path("notifications/settings", views.NotificationSettingsView.as_view()),
    path("notifications", views.NotificationListView.as_view()),
]
