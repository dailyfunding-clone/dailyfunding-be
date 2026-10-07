from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("email required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        INVESTOR = "investor", "투자자"
        BORROWER = "borrower", "대출자"

    class Grade(models.TextChoices):
        GENERAL = "general", "일반투자자"
        INCOME_ELIGIBLE = "income_eligible", "소득적격투자자"
        PROFESSIONAL = "professional", "전문투자자"

    class MemberType(models.TextChoices):
        PERSONAL = "personal", "개인"
        CORPORATE = "corporate", "법인"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=50, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.INVESTOR)
    member_type = models.CharField(
        max_length=16, choices=MemberType.choices, default=MemberType.PERSONAL
    )
    business_number = models.CharField(max_length=10, blank=True)
    grade = models.CharField(
        max_length=20, choices=Grade.choices, default=Grade.GENERAL
    )
    pin_hash = models.CharField(max_length=128, blank=True)
    pin_failures = models.PositiveSmallIntegerField(default=0)
    pin_locked_at = models.DateTimeField(null=True, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    @property
    def pin_registered(self):
        return bool(self.pin_hash)

    @property
    def identity_verified(self):
        return hasattr(self, "identity")

    def __str__(self):
        return self.email


# F-MY-05 등급별 한도 (원). None = 무제한.
GRADE_LIMITS = {
    User.Grade.GENERAL: {
        "total": 40_000_000,
        "real_estate": 20_000_000,
        "same_borrower": 5_000_000,
        "per_product_pct": None,
    },
    User.Grade.INCOME_ELIGIBLE: {
        "total": 100_000_000,
        "real_estate": 100_000_000,
        "same_borrower": 20_000_000,
        "per_product_pct": None,
    },
    User.Grade.PROFESSIONAL: {
        "total": None,
        "real_estate": None,
        "same_borrower": None,
        "per_product_pct": "0.40",
    },
}

REAL_ESTATE_TYPES = {"mortgage"}

REQUIRED_TERMS_INVESTOR = [
    "investment",
    "service",
    "privacy",
    "credit_info",
    "electronic_finance",
]
REQUIRED_TERMS_BORROWER = REQUIRED_TERMS_INVESTOR + [
    "credit_inquiry",
    "loan_terms",
]


class UserAgreement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="agreements")
    term = models.CharField(max_length=40)
    agreed = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "term")


class IdentityVerification(models.Model):
    """모의 본인인증 결과 (F-AUTH-04)."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="identity"
    )
    ci = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=50)
    birth = models.CharField(max_length=8)
    phone = models.CharField(max_length=20)
    carrier = models.CharField(max_length=10)
    verified_at = models.DateTimeField(auto_now_add=True)


class ReauthToken(models.Model):
    """F-AUTH-05 2차 인증 게이트 토큰."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class VirtualAccount(models.Model):
    """F-DEP-01 회원당 1개 발급되는 가상계좌."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="virtual_account"
    )
    bank_code = models.CharField(max_length=8)
    bank_name = models.CharField(max_length=20)
    account_no = models.CharField(max_length=32, unique=True)
    holder = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)


class LinkedAccount(models.Model):
    """F-DEP-05 연결계좌 (출금 수령·간편충전)."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="linked_account"
    )
    bank_name = models.CharField(max_length=20)
    account_no = models.CharField(max_length=32)
    holder = models.CharField(max_length=50)
    auto_charge = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class GradeRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        APPROVED = "approved"
        REJECTED = "rejected"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="grade_requests"
    )
    to_grade = models.CharField(max_length=20, choices=User.Grade.choices)
    document = models.FileField(upload_to="grade_docs/", null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.SUBMITTED
    )
    reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class AppLoginCode(models.Model):
    """'앱으로 로그인' 일회용 코드 (F-AUTH-03)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6, unique=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class PasswordResetToken(models.Model):
    """비밀번호 재설정 토큰 (F-AUTH-03). 모의 이메일 링크용."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
