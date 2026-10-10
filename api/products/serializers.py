from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.products.models import Product


class ProductListSerializer(serializers.ModelSerializer):
    progress_pct = serializers.CharField()

    class Meta:
        model = Product
        fields = (
            "id",
            "product_no",
            "name",
            "type",
            "annual_rate",
            "term_months",
            "target_amount",
            "raised_amount",
            "progress_pct",
            "status",
            "tags",
            "registered_at",
        )


class ProductTabsSerializer(serializers.Serializer):
    overview = serializers.DictField()
    detail = serializers.DictField()
    notice = serializers.CharField()


class ProductMySerializer(serializers.Serializer):
    deposit = serializers.IntegerField()
    investable = serializers.IntegerField()
    grade_remaining_limit = serializers.IntegerField(allow_null=True)
    same_borrower_remaining = serializers.IntegerField(allow_null=True)


class ProductDetailSerializer(ProductListSerializer):
    remaining_amount = serializers.IntegerField()
    tabs = serializers.SerializerMethodField()
    my = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "repay_type",
            "platform_fee_rate",
            "repay_day",
            "remaining_amount",
            "recruit_open_at",
            "tabs",
            "my",
        )

    @extend_schema_field(ProductTabsSerializer)
    def get_tabs(self, product):
        return {
            "overview": product.overview,
            "detail": product.detail,
            "notice": product.notice,
        }

    @extend_schema_field(ProductMySerializer(allow_null=True))
    def get_my(self, product):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        from api.products.views import _my_block

        return _my_block(request.user, product)


class SchedulePreviewRowSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    pay_date = serializers.DateField()
    principal = serializers.IntegerField()
    repay_principal = serializers.IntegerField()
    interest_gross = serializers.IntegerField()
    tax = serializers.IntegerField()
    platform_fee = serializers.IntegerField()
    interest_net = serializers.IntegerField()


class SchedulePreviewSerializer(serializers.Serializer):
    gross_rate = serializers.CharField()
    net_rate = serializers.CharField()
    gross_return = serializers.IntegerField()
    net_return = serializers.IntegerField()
    schedule = SchedulePreviewRowSerializer(many=True)
