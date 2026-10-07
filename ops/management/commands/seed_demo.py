"""데모 시드 생성기 (F-ADM-04). 가상 데이터만 만들고 실제 데이터를 긁지 않는다.

    python manage.py seed_demo [--users 5] [--products 20]
"""
import random

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.accounts.models import User
from api.accounts.services import create_user_account, issue_virtual_account
from api.contents.models import Disclosure, Event, Faq, News, Notice, Term
from api.loans.models import LoanProduct
from api.products.models import Product

AGREEMENTS = [
    {"term": "investment", "agreed": True},
    {"term": "service", "agreed": True},
    {"term": "privacy", "agreed": True},
    {"term": "credit_info", "agreed": True},
    {"term": "electronic_finance", "agreed": True},
]

TERMS = [
    ("investment", "온라인연계투자약관"),
    ("loan", "온라인연계대출약관"),
    ("electronic_finance", "전자금융거래약관"),
    ("service", "서비스 이용약관"),
    ("privacy", "개인정보 처리방침"),
    ("credit_info", "신용정보 활용체제"),
]


class Command(BaseCommand):
    help = "데모용 가상 사용자·상품·콘텐츠를 생성한다"

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=5)
        parser.add_argument("--products", type=int, default=20)
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **opts):
        rng = random.Random(opts["seed"])
        self._users(opts["users"])
        self._products(opts["products"], rng)
        self._contents()
        self.stdout.write(self.style.SUCCESS("seed complete"))

    def _users(self, n):
        for i in range(n):
            email = f"investor{i + 1}@demo.local"
            if User.objects.filter(email=email).exists():
                continue
            user = create_user_account(email, "Demo1234!", User.Role.INVESTOR, AGREEMENTS, name=f"투자자{i + 1}")
        if not User.objects.filter(email="admin@demo.local").exists():
            User.objects.create_superuser(
                email="admin@demo.local", password="Admin1234!", name="운영자"
            )

    def _products(self, n, rng):
        from api.adminpanel.views import SeedProductsView

        class _Req:
            data = {
                "count": n,
                "seed": rng.randint(0, 9999),
            }

        view = SeedProductsView()
        view.post(_Req())

    def _contents(self):
        for key, title in TERMS:
            Term.objects.get_or_create(
                key=key, defaults={"title": title, "body": f"{title} 본문 (시뮬레이션)"}
            )
        if not Notice.objects.exists():
            Notice.objects.create(
                category="important", title="서비스 오픈 안내", body="데일리펀딩 클론 데모"
            )
        if not Faq.objects.exists():
            Faq.objects.create(
                category="투자",
                question="예치금은 어떻게 충전하나요?",
                answer="가상계좌로 입금하면 자동 반영됩니다. (시뮬레이션)",
            )
        if not LoanProduct.objects.exists():
            LoanProduct.objects.create(
                category="personal",
                name="아파트담보대출",
                target="아파트 소유자",
                max_limit=300_000_000,
                rate_min="6.40",
                rate_max="14.00",
                term_desc="6~36개월",
                repay_method="원리금균등",
                info={"중도상환수수료": "1.06%", "연체금리": "약정+3%"},
            )
            LoanProduct.objects.create(
                category="business",
                name="이커머스 셀러론",
                target="온라인 셀러",
                max_limit=300_000_000,
                rate_min="7.00",
                rate_max="16.90",
                term_desc="3~24개월",
                repay_method="만기일시",
            )
        if not Event.objects.exists():
            Event.objects.create(
                title="신규 가입 포인트 이벤트",
                summary="가입만 해도 5,000P",
                status="ongoing",
                reward_points=5000,
            )
        if not News.objects.exists():
            News.objects.create(
                title="온투업 플랫폼 데모 기사",
                source="데일리뉴스",
                url="https://example.com/news/1",
                published_at=timezone.now().date(),
            )
        if not Disclosure.objects.exists():
            Disclosure.objects.create(
                year=timezone.now().year,
                month=timezone.now().month,
                kpi={
                    "average_rate": "9.42",
                    "total_loan": 128_400_000_000,
                    "loan_balance": 34_210_000_000,
                    "overdue_rate": "3.21",
                    "loss_rate": "0.84",
                    "reinvest_rate": "61.3",
                },
                management={
                    "회사정보": {
                        "상호": "데일리펀딩 주식회사 (시뮬레이션)",
                        "대표이사": "홍길동",
                        "온투업 등록번호": "2022-56",
                        "설립일": "2017-04-10",
                    },
                    "재무현황": [
                        {"구분": "자산총계", "금액": 12_300_000_000},
                        {"구분": "부채총계", "금액": 4_100_000_000},
                        {"구분": "자기자본", "금액": 8_200_000_000},
                    ],
                },
                operations={
                    "취급현황": [
                        {"구분": "누적 대출액", "금액": 128_400_000_000},
                        {"구분": "대출 잔액", "금액": 34_210_000_000},
                        {"구분": "평균 수익률", "금액": "9.42%"},
                    ],
                    "손실률 매트릭스": [
                        {"유형": "부동산", "연체율": "2.8%", "손실률": "0.6%"},
                        {"유형": "SCF", "연체율": "1.2%", "손실률": "0.1%"},
                        {"유형": "개인신용", "연체율": "5.4%", "손실률": "1.9%"},
                    ],
                },
                internal={
                    "내부통제": {
                        "여신심사": "차주별 한도 심사 규정 운영 (시뮬레이션)",
                        "리스크관리": "월별 포트폴리오 리뷰 (시뮬레이션)",
                    }
                },
            )
