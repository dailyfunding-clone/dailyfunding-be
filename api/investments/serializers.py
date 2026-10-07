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


class ScheduleRowSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    pay_date = serializers.DateField()
    principal = serializers.IntegerField()
    repay_principal = serializers.IntegerField()
    interest_gross = serializers.IntegerField()
    tax = serializers.IntegerField()
    platform_fee = serializers.IntegerField()
    interest_net = serializers.IntegerField()


class InvestmentResponseSerializer(serializers.Serializer):
    investment_id = serializers.IntegerField()
    amount = serializers.IntegerField()
    points_used = serializers.IntegerField()
    expected_net_return = serializers.IntegerField()
    status = serializers.CharField()
    schedule = ScheduleRowSerializer(many=True)


class InvestmentListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    product_no = serializers.CharField()
    product_name = serializers.CharField()
    type = serializers.CharField()
    amount = serializers.IntegerField()
    points_used = serializers.IntegerField()
    expected_net_return = serializers.IntegerField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class InvestmentListResponseSerializer(serializers.Serializer):
    results = InvestmentListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()


class InvestmentDetailProductSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_no = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    annual_rate = serializers.CharField()
    status = serializers.CharField()


class InvestmentDetailResponseSerializer(InvestmentResponseSerializer):
    product = InvestmentDetailProductSerializer()
    paid_net = serializers.IntegerField()


class SuitabilityQuestionSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    text = serializers.CharField()
    answer_options = serializers.ListField(child=serializers.CharField())


class SuitabilityQuestionsResponseSerializer(serializers.Serializer):
    questions = SuitabilityQuestionSerializer(many=True)
    valid_until = serializers.DateTimeField(allow_null=True)


class SuitabilityResultSerializer(serializers.Serializer):
    passed = serializers.BooleanField()
    expires_at = serializers.DateTimeField(allow_null=True)


class CartItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    product_no = serializers.CharField()
    name = serializers.CharField()
    annual_rate = serializers.CharField()
    term_months = serializers.IntegerField()
    target_amount = serializers.IntegerField()
    remaining_amount = serializers.IntegerField()
    status = serializers.CharField()
    closed = serializers.BooleanField()


class CartListResponseSerializer(serializers.Serializer):
    results = CartItemSerializer(many=True)
    count = serializers.IntegerField()


class CartAddResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()


class ReservationEligibleItemSerializer(serializers.Serializer):
    investment_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    amount = serializers.IntegerField()
    maturity_date = serializers.DateField()
    refinance_open = serializers.BooleanField()


class ReservationEligibleResponseSerializer(serializers.Serializer):
    results = ReservationEligibleItemSerializer(many=True)


class ReservationResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    amount = serializers.IntegerField()
    investment_id = serializers.IntegerField(required=False)
    product_name = serializers.CharField(required=False)
    created_at = serializers.DateTimeField(required=False)
