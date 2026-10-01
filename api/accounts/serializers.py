from rest_framework import serializers

from api.accounts.models import User


class AgreementItemSerializer(serializers.Serializer):
    term = serializers.CharField()
    agreed = serializers.BooleanField()


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, default="")
    referrer_email = serializers.EmailField(required=False, allow_blank=True)
    agreements = AgreementItemSerializer(many=True)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class PinLoginSerializer(serializers.Serializer):
    pin = serializers.CharField()
    email = serializers.EmailField(required=False)


class PinRegisterSerializer(serializers.Serializer):
    pin = serializers.CharField()


class IdentityVerifySerializer(serializers.Serializer):
    carrier = serializers.CharField()
    name = serializers.CharField()
    birth = serializers.CharField()
    phone = serializers.CharField()


class ReauthSerializer(serializers.Serializer):
    password = serializers.CharField()


class AppCodeExchangeSerializer(serializers.Serializer):
    code = serializers.CharField()


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
            "pin_registered",
            "identity_verified",
        )
