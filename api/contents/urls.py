from django.urls import path

from api.contents import views

urlpatterns = [
    path("notices", views.NoticeListView.as_view()),
    path("notices/<int:pk>", views.NoticeDetailView.as_view()),
    path("faqs", views.FaqListView.as_view()),
    path("faqs/keywords", views.FaqKeywordsView.as_view()),
    path("events", views.EventListView.as_view()),
    path("events/<int:pk>", views.EventDetailView.as_view()),
    path("events/<int:pk>/enter", views.EventEnterView.as_view()),
    path("disclosures", views.DisclosureListView.as_view()),
    path("news", views.NewsListView.as_view()),
    path("terms/<slug:key>", views.TermDetailView.as_view()),
]
