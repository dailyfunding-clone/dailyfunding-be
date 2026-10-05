from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from api.common.exceptions import ReauthRequired

ACCESS_COOKIE = "access"
REFRESH_COOKIE = "refresh"


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


def set_auth_cookies(response: Response, user) -> Response:
    refresh = RefreshToken.for_user(user)
    response.set_cookie(
        ACCESS_COOKIE,
        str(refresh.access_token),
        httponly=True,
        samesite="Lax",
        max_age=int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
    )
    response.set_cookie(
        REFRESH_COOKIE,
        str(refresh),
        httponly=True,
        samesite="Lax",
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
    )
    return response


def clear_auth_cookies(response: Response) -> Response:
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE)
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
