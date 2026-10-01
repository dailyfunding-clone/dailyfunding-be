from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from api.accounts import services
from api.accounts.models import AppLoginCode, User
from api.accounts.serializers import (
    AppCodeExchangeSerializer,
    IdentityVerifySerializer,
    LoginSerializer,
    PinLoginSerializer,
    PinRegisterSerializer,
    ReauthSerializer,
    SignupSerializer,
    UserSerializer,
)
from api.common.auth import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    issue_reauth_token,
    set_auth_cookies,
)
from api.common.exceptions import NotFound, Unauthorized, ValidationFailed


def _login_payload(user):
    return {
        "user_id": user.id,
        "name": user.name,
        "grade": user.grade,
        "pin_registered": user.pin_registered,
    }


class SignupView(APIView):
    permission_classes = (AllowAny,)
    role = User.Role.INVESTOR

    @extend_schema(request=SignupSerializer)
    def post(self, request):
        s = SignupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        user = services.create_user_account(
            d["email"], d["password"], self.role, d["agreements"], d.get("name", "")
        )
        referrer_email = d.get("referrer_email")
        if referrer_email:
            referrer = User.objects.filter(email=referrer_email).first()
            if referrer:
                from api.ledger.services import grant_points

                grant_points(
                    referrer,
                    2000,
                    ref_type="referral",
                    ref_id=str(user.id),
                    memo="친구 추천",
                )
        return Response(
            {
                "user_id": user.id,
                "email": user.email,
                "next_step": "identity_verification",
            },
            status=201,
        )


class BorrowerSignupView(SignupView):
    role = User.Role.BORROWER


class LoginView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(request=LoginSerializer)
    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        if user is None or not user.check_password(s.validated_data["password"]):
            raise Unauthorized("invalid credentials")
        return set_auth_cookies(Response(_login_payload(user)), user)


class PinLoginView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(request=PinLoginSerializer)
    def post(self, request):
        s = PinLoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user if request.user.is_authenticated else None
        if user is None:
            email = s.validated_data.get("email")
            if not email:
                raise ValidationFailed(
                    "email required for pin login", {"email": ["required"]}
                )
            user = User.objects.filter(email=email).first()
        if user is None:
            raise Unauthorized("invalid credentials")
        if not services.check_pin(user, s.validated_data["pin"]):
            raise Unauthorized("invalid pin")
        return set_auth_cookies(Response(_login_payload(user)), user)


class LogoutView(APIView):
    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE)
        if raw:
            try:
                RefreshToken(raw).blacklist()
            except Exception:
                pass
        return clear_auth_cookies(Response({"ok": True}))


class RefreshView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE)
        if not raw:
            raise Unauthorized("refresh token missing")
        try:
            refresh = RefreshToken(raw)
        except Exception:
            raise Unauthorized("refresh token invalid or expired")
        user = User.objects.filter(id=refresh["user_id"]).first()
        if user is None:
            raise Unauthorized("user not found")
        return set_auth_cookies(Response(_login_payload(user)), user)


class IdentityVerifyView(APIView):
    """모의 본인인증 (F-AUTH-04). 서버가 CI를 발급한다."""

    permission_classes = (AllowAny,)

    @extend_schema(request=IdentityVerifySerializer)
    def post(self, request):
        s = IdentityVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = request.user if request.user.is_authenticated else None
        if user is None:
            # 가입 위저드 직후 비로그인 상태에서도 호출 가능하도록 email 지원
            email = request.data.get("email")
            if not email:
                raise Unauthorized("login or email required")
            user = User.objects.filter(email=email).first()
            if user is None:
                raise NotFound("user not found")
        d = s.validated_data
        identity = services.verify_identity(
            user, d["carrier"], d["name"], d["birth"], d["phone"]
        )
        return Response({"ci": identity.ci, "verified": True})


class PinRegisterView(APIView):
    @extend_schema(request=PinRegisterSerializer)
    def post(self, request):
        s = PinRegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.register_pin(request.user, s.validated_data["pin"])
        return Response({"pin_registered": True})


class ReauthView(APIView):
    @extend_schema(request=ReauthSerializer)
    def post(self, request):
        s = ReauthSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not request.user.check_password(s.validated_data["password"]):
            raise Unauthorized("invalid password")
        token, ttl = issue_reauth_token(request.user)
        return Response({"reauth_token": token, "expires_in": ttl})


class AppCodeIssueView(APIView):
    """앱(로그인 상태)이 발급하는 일회용 코드."""

    def post(self, request):
        code = services.issue_app_code(request.user)
        return Response({"code": code.code, "expires_in": 60})


class AppCodeExchangeView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(request=AppCodeExchangeSerializer)
    def post(self, request):
        s = AppCodeExchangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        code = (
            AppLoginCode.objects.filter(
                code=s.validated_data["code"],
                used=False,
                expires_at__gt=timezone.now(),
            )
            .select_related("user")
            .first()
        )
        if code is None:
            raise Unauthorized("invalid or expired code")
        code.used = True
        code.save(update_fields=["used"])
        return set_auth_cookies(Response(_login_payload(code.user)), code.user)


class MeView(APIView):
    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)
