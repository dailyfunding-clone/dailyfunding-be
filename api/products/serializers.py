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


class ProductDetailSerializer(ProductListSerializer):
    remaining_amount = serializers.IntegerField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "repay_type",
            "platform_fee_rate",
            "repay_day",
            "remaining_amount",
            "recruit_open_at",
        )
