from rest_framework import serializers


class InvestOrderSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)
    use_points = serializers.IntegerField(min_value=0, required=False, default=0)
    confirm = serializers.CharField(required=False, allow_blank=True)


class SuitabilityAnswerSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    choice = serializers.CharField()


class SuitabilitySubmitSerializer(serializers.Serializer):
    answers = SuitabilityAnswerSerializer(many=True)


class CartAddSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()


class ReservationCreateSerializer(serializers.Serializer):
    investment_id = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)


class ReservationPatchSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
