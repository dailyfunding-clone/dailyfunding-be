from rest_framework import serializers

from api.accounts.models import User


class AgreementItemSerializer(serializers.Serializer):
    term = serializers.CharField()
    agreed = serializers.BooleanField()


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, default="")
    member_type = serializers.ChoiceField(
        choices=["personal", "corporate"], default="personal"
    )
    business_number = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=10
    )
    referrer_email = serializers.EmailField(required=False, allow_blank=True)
    agreements = AgreementItemSerializer(many=True)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    keep_login = serializers.BooleanField(required=False, default=True)


class PinLoginSerializer(serializers.Serializer):
    pin = serializers.CharField()


class PinRegisterSerializer(serializers.Serializer):
    pin = serializers.CharField()


class IdentityVerifySerializer(serializers.Serializer):
    carrier = serializers.CharField()
    name = serializers.CharField()
    birth = serializers.CharField()
    phone = serializers.CharField()


class BusinessNumberVerifySerializer(serializers.Serializer):
    business_number = serializers.CharField()

    def validate_business_number(self, value):
        if len(value) != 10 or not value.isdigit():
            raise serializers.ValidationError(
                "business_number must be exactly 10 digits"
            )
        return value


class ReauthSerializer(serializers.Serializer):
    password = serializers.CharField(required=False)
    pin = serializers.CharField(required=False, min_length=6, max_length=6)

    def validate(self, attrs):
        if not attrs.get("password") and not attrs.get("pin"):
            raise serializers.ValidationError("password or pin required")
        return attrs


class FindIdSerializer(serializers.Serializer):
    name = serializers.CharField()
    birth_date = serializers.CharField()
    phone = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField()


class AppCodeExchangeSerializer(serializers.Serializer):
    code = serializers.CharField()


class SignupResponseSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    email = serializers.EmailField()
    next_step = serializers.CharField()


class LoginResponseSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    grade = serializers.CharField()
    pin_registered = serializers.BooleanField()


class OkResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()


class IdentityVerifyResponseSerializer(serializers.Serializer):
    ci = serializers.CharField()
    verified = serializers.BooleanField()


class BusinessNumberVerifyResponseSerializer(serializers.Serializer):
    verified = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_blank=True)


class PinRegisterResponseSerializer(serializers.Serializer):
    pin_registered = serializers.BooleanField()


class ReauthResponseSerializer(serializers.Serializer):
    reauth_token = serializers.CharField()
    expires_in = serializers.IntegerField()


class AppCodeIssueResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    expires_in = serializers.IntegerField()


class FindIdResponseSerializer(serializers.Serializer):
    email = serializers.CharField()


class PasswordResetRequestResponseSerializer(serializers.Serializer):
    sent = serializers.BooleanField()
    dev_token = serializers.CharField(allow_null=True)


class PasswordResetResponseSerializer(serializers.Serializer):
    reset = serializers.BooleanField()


class UserSerializer(serializers.ModelSerializer):
    pin_registered = serializers.BooleanField(read_only=True)
    identity_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "name",
            "role",
            "grade",
            "is_staff",
            "member_type",
            "pin_registered",
            "identity_verified",
        )
