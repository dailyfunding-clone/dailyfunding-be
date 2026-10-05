from django.urls import path

from api.mypage import views

urlpatterns = [
    path("me/dashboard", views.DashboardView.as_view()),
    path("me/calendar", views.CalendarView.as_view()),
    path("me/investments", views.MyInvestmentsView.as_view()),
    path("me/grade", views.GradeView.as_view()),
    path("me/grade/history", views.GradeHistoryView.as_view()),
    path("me/grade-request", views.GradeRequestView.as_view()),
    path("me/limit-assessment", views.LimitAssessmentView.as_view()),
]
