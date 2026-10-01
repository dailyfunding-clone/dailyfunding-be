from django.urls import path

from api.adminpanel import views

urlpatterns = [
    path("products", views.ProductListCreateView.as_view()),
    path("products/<int:pk>", views.ProductDetailAdminView.as_view()),
    path("products/<int:pk>/status", views.ProductStatusView.as_view()),
    path("products/<int:pk>/execute", views.ProductExecuteView.as_view()),
    path("batch/repay", views.BatchRepayView.as_view()),
    path("batch/expire-points", views.BatchExpirePointsView.as_view()),
    path("batch/reconcile", views.BatchReconcileView.as_view()),
    path("time/advance", views.TimeAdvanceView.as_view()),
    path("grade-requests", views.GradeRequestListView.as_view()),
    path("grade-requests/<int:pk>", views.GradeRequestDetailView.as_view()),
    path("deposit/holds", views.DepositHoldListView.as_view()),
    path("deposit/holds/<str:pk>", views.DepositHoldDetailView.as_view()),
    path("loan-applications", views.LoanApplicationListView.as_view()),
    path("loan-applications/<int:pk>", views.LoanApplicationDetailView.as_view()),
    path("seed/products", views.SeedProductsView.as_view()),
    path("notices", views.AdminNoticeList.as_view()),
    path("notices/<int:pk>", views.AdminNoticeDetail.as_view()),
    path("faqs", views.AdminFaqList.as_view()),
    path("faqs/<int:pk>", views.AdminFaqDetail.as_view()),
    path("events", views.AdminEventList.as_view()),
    path("events/<int:pk>", views.AdminEventDetail.as_view()),
    path("disclosures", views.AdminDisclosureList.as_view()),
    path("disclosures/<int:pk>", views.AdminDisclosureDetail.as_view()),
    path("news", views.AdminNewsList.as_view()),
    path("news/<int:pk>", views.AdminNewsDetail.as_view()),
]
