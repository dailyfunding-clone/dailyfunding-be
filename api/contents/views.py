from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.exceptions import NotFound
from api.contents.models import (
    Disclosure,
    Event,
    EventEntry,
    Faq,
    News,
    Notice,
    Term,
)

FAQ_CATEGORIES = [
    "투자",
    "대출",
    "예치금",
    "회원",
    "포인트",
    "세금",
    "상환",
    "계좌",
    "앱",
    "기타",
]

POPULAR_KEYWORDS = ["예치금", "투자한도", "상환", "출금수수료", "적합성테스트"]


def _paginate(request, qs):
    page = int(request.query_params.get("page", 1))
    size = min(int(request.query_params.get("page_size", 20)), 100)
    total = qs.count()
    return qs[(page - 1) * size : page * size], total, page


class NoticeListView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        qs = Notice.objects.all().order_by("-id")
        if request.query_params.get("category"):
            qs = qs.filter(category=request.query_params["category"])
        if request.query_params.get("q"):
            qs = qs.filter(title__icontains=request.query_params["q"])
        rows, total, page = _paginate(request, qs)
        return Response(
            {
                "results": [
                    {
                        "id": n.id,
                        "category": n.category,
                        "title": n.title,
                        "created_at": n.created_at,
                    }
                    for n in rows
                ],
                "total": total,
                "page": page,
            }
        )


class NoticeDetailView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, pk):
        n = Notice.objects.filter(pk=pk).first()
        if n is None:
            raise NotFound()
        return Response(
            {
                "id": n.id,
                "category": n.category,
                "title": n.title,
                "body": n.body,
                "attachments": n.attachments,
                "created_at": n.created_at,
            }
        )


class FaqListView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        qs = Faq.objects.all().order_by("-id")
        if request.query_params.get("category"):
            qs = qs.filter(category=request.query_params["category"])
        if request.query_params.get("q"):
            q = request.query_params["q"]
            qs = qs.filter(question__icontains=q) | qs.filter(answer__icontains=q)
        rows, total, page = _paginate(request, qs)
        return Response(
            {
                "results": [
                    {
                        "id": f.id,
                        "category": f.category,
                        "question": f.question,
                        "answer": f.answer,
                    }
                    for f in rows
                ],
                "total": total,
                "page": page,
            }
        )


class FaqKeywordsView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        return Response({"keywords": POPULAR_KEYWORDS})


class EventListView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        qs = Event.objects.all().order_by("-id")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        rows, total, page = _paginate(request, qs)
        return Response(
            {
                "results": [
                    {
                        "id": e.id,
                        "title": e.title,
                        "summary": e.summary,
                        "status": e.status,
                        "thumbnail_url": e.thumbnail_url,
                        "start_at": e.start_at,
                        "end_at": e.end_at,
                    }
                    for e in rows
                ],
                "total": total,
                "page": page,
            }
        )


class EventDetailView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, pk):
        e = Event.objects.filter(pk=pk).first()
        if e is None:
            raise NotFound()
        prev_e = Event.objects.filter(id__lt=e.id).order_by("-id").first()
        next_e = Event.objects.filter(id__gt=e.id).order_by("id").first()
        ongoing = list(
            Event.objects.filter(status=Event.Status.ONGOING)
            .exclude(id=e.id)
            .values("id", "title", "thumbnail_url")[:5]
        )
        return Response(
            {
                "id": e.id,
                "title": e.title,
                "summary": e.summary,
                "body": e.body,
                "status": e.status,
                "thumbnail_url": e.thumbnail_url,
                "reward_points": e.reward_points,
                "start_at": e.start_at,
                "end_at": e.end_at,
                "prev_id": prev_e.id if prev_e else None,
                "next_id": next_e.id if next_e else None,
                "ongoing": ongoing,
            }
        )


class EventEnterView(APIView):
    """이벤트 참여 → 포인트 적립 규칙 연동 (F-CON-03)."""

    @transaction.atomic
    def post(self, request, pk):
        e = Event.objects.filter(pk=pk, status=Event.Status.ONGOING).first()
        if e is None:
            raise NotFound("event not found or not ongoing")
        _, created = EventEntry.objects.get_or_create(event=e, user=request.user)
        if created and e.reward_points:
            from api.ledger.services import grant_points

            grant_points(
                request.user,
                e.reward_points,
                ref_type="event",
                ref_id=str(e.id),
                memo=f"이벤트 참여: {e.title}",
            )
        return Response({"entered": True, "reward_points": e.reward_points})


class DisclosureListView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        year = request.query_params.get("year")
        month = request.query_params.get("month")
        qs = Disclosure.objects.all().order_by("-year", "-month")
        if year:
            qs = qs.filter(year=year)
        if month:
            qs = qs.filter(month=month)
        d = qs.first()
        if d is None:
            return Response({"results": []})
        return Response(
            {
                "results": [
                    {
                        "id": d.id,
                        "year": d.year,
                        "month": d.month,
                        "kpi": d.kpi,
                        "tabs": {
                            "management": d.management,
                            "operations": d.operations,
                            "internal": d.internal,
                        },
                        "published_at": d.published_at,
                    }
                ]
            }
        )


class NewsListView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        qs = News.objects.all().order_by("-published_at", "-id")
        cursor = request.query_params.get("cursor")
        if cursor:
            qs = qs.filter(id__lt=int(cursor))
        rows = list(qs[:20])
        return Response(
            {
                "results": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "source": n.source,
                        "url": n.url,
                        "thumbnail_url": n.thumbnail_url,
                        "published_at": n.published_at,
                    }
                    for n in rows
                ],
                "next_cursor": str(rows[-1].id) if rows else None,
            }
        )


class TermDetailView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request, key):
        t = Term.objects.filter(key=key).first()
        if t is None:
            raise NotFound()
        return Response(
            {"key": t.key, "title": t.title, "body": t.body, "updated_at": t.updated_at}
        )
