from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from api.common.metrics import VitalsView

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/metrics/vitals", VitalsView.as_view()),
    path("api/", include("api.accounts.urls")),
    path("api/", include("api.products.urls")),
    path("api/", include("api.investments.urls")),
    path("api/", include("api.ledger.urls")),
    path("api/", include("api.loans.urls")),
    path("api/", include("api.contents.urls")),
    path("api/", include("api.mypage.urls")),
    path("api/", include("api.webhooks.urls")),
    path("api/", include("api.notifications.urls")),
    path("api/admin/", include("api.adminpanel.urls")),
    path("mockbank/", include("mockbank.urls")),
]
