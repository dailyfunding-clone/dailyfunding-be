import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from api.common.exceptions import DuplicateCi, EmailTaken, PinLocked, ValidationFailed
from api.accounts.models import (
    REQUIRED_TERMS_BORROWER,
    REQUIRED_TERMS_INVESTOR,
    AppLoginCode,
    IdentityVerification,
    User,
    UserAgreement,
    VirtualAccount,
)


def make_account_no(user_id: int) -> str:
    return f"100-{(user_id // 1_000_000) % 10000:04d}-{user_id % 1_000_000:06d}"


def issue_virtual_account(user) -> VirtualAccount:
    return VirtualAccount.objects.create(
        user=user,
        bank_code=settings.MOCKBANK_CODE,
        bank_name=settings.MOCKBANK_NAME,
        account_no=make_account_no(user.id),
        holder=user.name or user.email.split("@")[0],
    )


def validate_password_policy(password: str):
    ok = (
        isinstance(password, str)
        and 8 <= len(password) <= 15
        and any(c.isalpha() for c in password)
        and any(c.isdigit() for c in password)
        and any(not c.isalnum() for c in password)
    )
    if not ok:
        raise ValidationFailed(
            "password must be 8-15 chars with letters, digits and symbols",
            {"password": ["영문+숫자+특수문자 조합 8~15자"]},
        )


@transaction.atomic
def create_user_account(email, password, role, agreements, name=""):
    if User.objects.filter(email=email).exists():
        raise EmailTaken()
    validate_password_policy(password)
    required_keys = (
        REQUIRED_TERMS_BORROWER
        if role == User.Role.BORROWER
        else REQUIRED_TERMS_INVESTOR
    )
    agreed_map = {a.get("term"): bool(a.get("agreed")) for a in (agreements or [])}
    missing = [t for t in required_keys if not agreed_map.get(t)]
    if missing:
        raise ValidationFailed(
            "required terms not agreed", {"agreements": missing}
        )
    user = User.objects.create_user(
        email=email, password=password, role=role, name=name
    )
    for term, agreed in agreed_map.items():
        UserAgreement.objects.create(user=user, term=term, agreed=agreed)
    if role == User.Role.INVESTOR:
        issue_virtual_account(user)
    return user


def verify_identity(user, carrier, name, birth, phone):
    """모의 본인인증: 서버가 CI를 생성해 발급한다."""
    raw = f"{carrier}|{name}|{birth}|{phone}"
    ci = "mock-ci-" + hashlib.sha256(raw.encode()).hexdigest()[:48]
    if IdentityVerification.objects.filter(ci=ci).exclude(user=user).exists():
        raise DuplicateCi()
    identity, _ = IdentityVerification.objects.update_or_create(
        user=user,
        defaults={
            "ci": ci,
            "name": name,
            "birth": birth,
            "phone": phone,
            "carrier": carrier,
        },
    )
    if not user.name:
        user.name = name
        user.save(update_fields=["name"])
        if hasattr(user, "virtual_account"):
            va = user.virtual_account
            va.holder = name
            va.save(update_fields=["holder"])
    return identity


def register_pin(user, pin: str):
    if not (isinstance(pin, str) and len(pin) == 6 and pin.isdigit()):
        raise ValidationFailed("pin must be 6 digits", {"pin": ["6자리 숫자"]})
    user.pin_hash = make_password(pin)
    user.pin_failures = 0
    user.pin_locked_at = None
    user.save(update_fields=["pin_hash", "pin_failures", "pin_locked_at"])


def check_pin(user, pin: str) -> bool:
    if user.pin_locked_at is not None:
        raise PinLocked()
    if not user.pin_registered or not check_password(pin, user.pin_hash):
        user.pin_failures += 1
        if user.pin_failures >= settings.PIN_MAX_FAILURES:
            user.pin_locked_at = timezone.now()
        user.save(update_fields=["pin_failures", "pin_locked_at"])
        if user.pin_locked_at:
            raise PinLocked()
        return False
    user.pin_failures = 0
    user.save(update_fields=["pin_failures"])
    return True


def issue_app_code(user):
    code = f"{secrets.randbelow(1_000_000):06d}"
    return AppLoginCode.objects.create(
        user=user,
        code=code,
        expires_at=timezone.now() + timezone.timedelta(seconds=60),
    )
