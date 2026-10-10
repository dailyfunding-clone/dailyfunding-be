import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from api.common.exceptions import (
    DuplicateCi,
    EmailTaken,
    NotFound,
    PinLocked,
    ValidationFailed,
)
from api.accounts.models import (
    REQUIRED_TERMS_BORROWER,
    REQUIRED_TERMS_INVESTOR,
    AppLoginCode,
    IdentityVerification,
    PasswordResetToken,
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
def create_user_account(
    email, password, role, agreements, name="", member_type="personal", business_number=""
):
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
        email=email,
        password=password,
        role=role,
        name=name,
        member_type=member_type,
        business_number=business_number,
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
    user.save(update_fields=["pin_hash"])


def grant_referral_reward(referrer_email, new_user):
    if not referrer_email or referrer_email.lower() == new_user.email.lower():
        return False
    referrer = User.objects.filter(email=referrer_email).first()
    if referrer is None:
        return False
    from api.ledger.models import PointEntry
    from api.ledger.services import grant_points

    rewards = PointEntry.objects.filter(
        user=referrer, kind=PointEntry.Kind.EARN, ref_type="referral"
    ).count()
    if rewards >= settings.REFERRAL_MAX_REWARDS:
        return False
    grant_points(
        referrer,
        2000,
        ref_type="referral",
        ref_id=str(new_user.id),
        memo="친구 추천",
    )
    return True


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


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}{'*' * max(len(local) - 2, 4)}@{domain}"


def find_email_by_identity(name, birth, phone) -> str:
    """아이디 찾기 (F-AUTH-03): 본인인증 레코드 매칭 → 마스킹 이메일."""
    identity = (
        IdentityVerification.objects.filter(name=name, birth=birth, phone=phone)
        .select_related("user")
        .first()
    )
    if identity is None:
        raise NotFound("no account matching that identity")
    return mask_email(identity.user.email)


def issue_password_reset_token(user):
    return PasswordResetToken.objects.create(
        user=user,
        token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + timezone.timedelta(minutes=30),
    )


def reset_password(token_str: str, new_password: str):
    reset = (
        PasswordResetToken.objects.filter(
            token=token_str, used=False, expires_at__gt=timezone.now()
        )
        .select_related("user")
        .first()
    )
    if reset is None:
        raise ValidationFailed(
            "invalid or expired token", {"token": ["invalid or expired"]}
        )
    validate_password_policy(new_password)
    user = reset.user
    user.set_password(new_password)
    user.pin_hash = ""
    user.pin_failures = 0
    user.pin_locked_at = None
    user.save(
        update_fields=["password", "pin_hash", "pin_failures", "pin_locked_at"]
    )
    reset.used = True
    reset.save(update_fields=["used"])
