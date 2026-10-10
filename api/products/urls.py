from django.urls import path

from api.products import views

urlpatterns = [
    path("products", views.ProductListView.as_view()),
    path("products/stream", views.ProductStreamView.as_view()),
    path("products/<int:pk>", views.ProductDetailView.as_view()),
    path("products/<int:pk>/schedule-preview", views.SchedulePreviewView.as_view()),
]
