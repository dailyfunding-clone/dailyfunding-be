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


class DepositAccountSerializer(serializers.Serializer):
    bank = serializers.CharField()
    account_no = serializers.CharField()
    holder = serializers.CharField()
    deposit = serializers.IntegerField()
    held = serializers.IntegerField()
    withdrawable = serializers.IntegerField()


class DepositIntentResponseSerializer(serializers.Serializer):
    intent_id = serializers.CharField()
    status = serializers.CharField()


class WithdrawResponseSerializer(serializers.Serializer):
    withdrawal_id = serializers.IntegerField()
    fee = serializers.IntegerField()
    status = serializers.CharField()


class LedgerEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    amount = serializers.IntegerField()
    ref_type = serializers.CharField()
    ref_id = serializers.CharField()
    created_at = serializers.DateTimeField()


class DepositHistorySerializer(serializers.Serializer):
    results = LedgerEntrySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class LinkedAccountResponseSerializer(LinkedAccountSerializer):
    linked = serializers.BooleanField()


class EnabledResponseSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class PointBalanceSerializer(serializers.Serializer):
    balance = serializers.IntegerField()
    expiring_this_month = serializers.IntegerField()


class PointEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    amount = serializers.IntegerField()
    memo = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class PointHistorySerializer(serializers.Serializer):
    results = PointEntrySerializer(many=True)


class PointConvertResponseSerializer(serializers.Serializer):
    converted = serializers.IntegerField()
    points = serializers.IntegerField()
    deposit = serializers.IntegerField()
