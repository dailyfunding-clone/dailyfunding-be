from rest_framework import serializers


class NotifyIntentSerializer(serializers.Serializer):
    sender_name = serializers.CharField(max_length=50)
    amount = serializers.IntegerField(min_value=1)


class WithdrawSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1, required=False)
    all = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get("all") and not data.get("amount"):
            raise serializers.ValidationError("amount or all required")
        return data


class LinkedAccountSerializer(serializers.Serializer):
    bank_name = serializers.CharField(max_length=20)
    account_no = serializers.CharField(max_length=32)
    holder = serializers.CharField(max_length=50)


class AutoChargeSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class PointConvertSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
