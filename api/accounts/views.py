from django.conf import settings
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
    AppCodeIssueResponseSerializer,
    BusinessNumberVerifyResponseSerializer,
    BusinessNumberVerifySerializer,
    FindIdResponseSerializer,
    FindIdSerializer,
    IdentityVerifyResponseSerializer,
    IdentityVerifySerializer,
    LoginResponseSerializer,
    LoginSerializer,
    OkResponseSerializer,
    PasswordResetRequestResponseSerializer,
    PasswordResetRequestSerializer,
    PasswordResetResponseSerializer,
    PasswordResetSerializer,
    PinLoginSerializer,
    PinRegisterResponseSerializer,
    PinRegisterSerializer,
    ReauthResponseSerializer,
    ReauthSerializer,
    SignupResponseSerializer,
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


UNREGISTERED_BUSINESS_NUMBERS = {"0000000000"}


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

    @extend_schema(
        request=SignupSerializer,
        responses={201: SignupResponseSerializer},
    )
    def post(self, request):
        s = SignupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        user = services.create_user_account(
            d["email"],
            d["password"],
            self.role,
            d["agreements"],
            d.get("name", ""),
            d["member_type"],
            d.get("business_number", ""),
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

    @extend_schema(request=LoginSerializer, responses=LoginResponseSerializer)
    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        if user is None or not user.check_password(s.validated_data["password"]):
            raise Unauthorized("invalid credentials")
        return set_auth_cookies(
            Response(_login_payload(user)),
            user,
            persistent=s.validated_data["keep_login"],
        )


class PinLoginView(APIView):
    """저장된 세션(로그인 상태) 사용자의 간편비밀번호 잠금 해제."""

    @extend_schema(request=PinLoginSerializer, responses=LoginResponseSerializer)
    def post(self, request):
        s = PinLoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not services.check_pin(request.user, s.validated_data["pin"]):
            raise Unauthorized("invalid pin")
        return set_auth_cookies(Response(_login_payload(request.user)), request.user)


class LogoutView(APIView):
    @extend_schema(request=None, responses=OkResponseSerializer)
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

    @extend_schema(request=None, responses=LoginResponseSerializer)
    def post(self, request):
        raw = request.COOKIES.get(REFRESH_COOKIE) or request.data.get("refresh")
        if not raw:
            raise Unauthorized("refresh token missing")
        try:
            refresh = RefreshToken(raw)
        except Exception:
            raise Unauthorized("refresh token invalid or expired")
        try:
            refresh.blacklist()
        except Exception:
            pass
        user = User.objects.filter(id=refresh["user_id"]).first()
        if user is None:
            raise Unauthorized("user not found")
        return set_auth_cookies(Response(_login_payload(user)), user)


class IdentityVerifyView(APIView):
    """모의 본인인증 (F-AUTH-04). 서버가 CI를 발급한다."""

    @extend_schema(
        request=IdentityVerifySerializer,
        responses=IdentityVerifyResponseSerializer,
    )
    def post(self, request):
        s = IdentityVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        identity = services.verify_identity(
            request.user, d["carrier"], d["name"], d["birth"], d["phone"]
        )
        return Response({"ci": identity.ci, "verified": True})


class BusinessNumberVerifyView(APIView):
    """사업자등록번호 인증 모의 (F-AUTH-01)."""

    permission_classes = (AllowAny,)

    @extend_schema(
        request=BusinessNumberVerifySerializer,
        responses=BusinessNumberVerifyResponseSerializer,
    )
    def post(self, request):
        s = BusinessNumberVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        number = s.validated_data["business_number"]
        if number in UNREGISTERED_BUSINESS_NUMBERS:
            return Response({"verified": False, "reason": "unregistered"})
        return Response({"verified": True})


class PinRegisterView(APIView):
    @extend_schema(
        request=PinRegisterSerializer,
        responses=PinRegisterResponseSerializer,
    )
    def post(self, request):
        s = PinRegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.register_pin(request.user, s.validated_data["pin"])
        return Response({"pin_registered": True})


class ReauthView(APIView):
    @extend_schema(request=ReauthSerializer, responses=ReauthResponseSerializer)
    def post(self, request):
        s = ReauthSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        if d.get("pin"):
            if not services.check_pin(request.user, d["pin"]):
                raise Unauthorized("invalid pin")
        elif not request.user.check_password(d["password"]):
            raise Unauthorized("invalid password")
        token, ttl = issue_reauth_token(request.user)
        return Response({"reauth_token": token, "expires_in": ttl})


class AppCodeIssueView(APIView):
    """앱(로그인 상태)이 발급하는 일회용 코드."""

    @extend_schema(request=None, responses=AppCodeIssueResponseSerializer)
    def post(self, request):
        code = services.issue_app_code(request.user)
        return Response({"code": code.code, "expires_in": 60})


class AppCodeExchangeView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        request=AppCodeExchangeSerializer, responses=LoginResponseSerializer
    )
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


class FindIdView(APIView):
    """아이디 찾기 (F-AUTH-03). 모의 SMS 본인확인."""

    permission_classes = (AllowAny,)

    @extend_schema(
        request=FindIdSerializer, responses=FindIdResponseSerializer
    )
    def post(self, request):
        s = FindIdSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        email = services.find_email_by_identity(
            d["name"], d["birth_date"], d["phone"]
        )
        return Response({"email": email})


class PasswordResetRequestView(APIView):
    """비밀번호 재설정 링크 발송 모의 (F-AUTH-03). 이메일 존재 여부를 숨긴다."""

    permission_classes = (AllowAny,)

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses=PasswordResetRequestResponseSerializer,
    )
    def post(self, request):
        s = PasswordResetRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = User.objects.filter(email=s.validated_data["email"]).first()
        dev_token = None
        if user is not None:
            token = services.issue_password_reset_token(user).token
            if settings.DEBUG:
                dev_token = token
        return Response({"sent": True, "dev_token": dev_token})


class PasswordResetView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        request=PasswordResetSerializer,
        responses=PasswordResetResponseSerializer,
    )
    def post(self, request):
        s = PasswordResetSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.reset_password(
            s.validated_data["token"], s.validated_data["new_password"]
        )
        return Response({"reset": True})


class MeView(APIView):
    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)
