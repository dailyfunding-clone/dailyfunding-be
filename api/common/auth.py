import hmac
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from api.common.exceptions import ReauthRequired

ACCESS_COOKIE = "access"
REFRESH_COOKIE = "refresh"
CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"

_SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")


class CsrfDoubleSubmitMiddleware:
    """Cookie-authenticated mutations must echo the csrf cookie in
    X-CSRF-Token. Bearer-only requests carry no cookies and pass."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in _SAFE_METHODS and (
            request.COOKIES.get(ACCESS_COOKIE)
            or request.COOKIES.get(REFRESH_COOKIE)
        ):
            cookie = request.COOKIES.get(CSRF_COOKIE, "")
            header = request.headers.get(CSRF_HEADER, "")
            if not cookie or not hmac.compare_digest(cookie, header):
                return JsonResponse(
                    {
                        "code": "FORBIDDEN",
                        "message": "CSRF token missing or invalid",
                        "details": {},
                    },
                    status=403,
                )
        return self.get_response(request)


class CookieJWTAuthentication(JWTAuthentication):
    """JWT auth that reads the access token from httpOnly cookie first,
    then falls back to the Authorization header."""

    def authenticate(self, request):
        raw = request.COOKIES.get(ACCESS_COOKIE)
        if raw:
            try:
                validated = self.get_validated_token(raw)
            except Exception:
                return None
            return self.get_user(validated), validated
        return super().authenticate(request)


def set_auth_cookies(
    response: Response, user, persistent: bool = True
) -> Response:
    refresh = RefreshToken.for_user(user)
    access_max_age = (
        int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds())
        if persistent
        else None
    )
    refresh_max_age = (
        int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
        if persistent
        else None
    )
    secure = not settings.DEBUG
    response.set_cookie(
        ACCESS_COOKIE,
        str(refresh.access_token),
        httponly=True,
        samesite="Lax",
        secure=secure,
        max_age=access_max_age,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        str(refresh),
        httponly=True,
        samesite="Lax",
        secure=secure,
        max_age=refresh_max_age,
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        httponly=False,
        samesite="Lax",
        secure=secure,
        max_age=refresh_max_age,
    )
    return response


def clear_auth_cookies(response: Response) -> Response:
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return response


def issue_reauth_token(user):
    """Short-lived token for sensitive actions (F-AUTH-05)."""
    import secrets

    from api.accounts.models import ReauthToken

    ReauthToken.objects.filter(user=user).delete()
    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timezone.timedelta(
        seconds=settings.REAUTH_TOKEN_TTL_SEC
    )
    ReauthToken.objects.create(user=user, token=token, expires_at=expires_at)
    return token, settings.REAUTH_TOKEN_TTL_SEC


def require_reauth(request):
    """Validate X-Reauth-Token header; raises ReauthRequired."""
    from api.accounts.models import ReauthToken

    token = request.headers.get("X-Reauth-Token", "")
    ok = (
        token
        and ReauthToken.objects.filter(
            user=request.user, token=token, expires_at__gt=timezone.now()
        ).exists()
    )
    if not ok:
        raise ReauthRequired()
