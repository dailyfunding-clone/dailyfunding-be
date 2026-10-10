from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from api.notifications.models import Device, Notification, NotificationSetting


class DeviceSerializer(serializers.Serializer):
    expo_push_token = serializers.CharField(max_length=100)
    platform = serializers.ChoiceField(choices=["ios", "android", "web"])


class NotifSettingSerializer(serializers.Serializer):
    new_product = serializers.BooleanField(required=False)
    recruit_closed = serializers.BooleanField(required=False)
    repayment = serializers.BooleanField(required=False)


class NotifSettingResponseSerializer(serializers.Serializer):
    new_product = serializers.BooleanField()
    recruit_closed = serializers.BooleanField()
    repayment = serializers.BooleanField()


class DeviceResponseSerializer(serializers.Serializer):
    registered = serializers.BooleanField()


class NotificationItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    created_at = serializers.DateTimeField()


class NotificationListResponseSerializer(serializers.Serializer):
    results = NotificationItemSerializer(many=True)


class DeviceView(APIView):
    """POST /api/devices — 푸시 토큰 등록."""

    @extend_schema(
        request=DeviceSerializer, responses={201: DeviceResponseSerializer}
    )
    def post(self, request):
        s = DeviceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        Device.objects.update_or_create(
            expo_push_token=s.validated_data["expo_push_token"],
            defaults={
                "user": request.user,
                "platform": s.validated_data["platform"],
            },
        )
        return Response({"registered": True}, status=201)


class NotificationSettingsGetSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class NotificationSettingsView(APIView):
    """GET/POST /api/notifications/settings — 알림 설정 조회·변경."""

    @extend_schema(responses=NotificationSettingsGetSerializer)
    def get(self, request):
        setting, _ = NotificationSetting.objects.get_or_create(
            user=request.user
        )
        return Response(
            {
                "enabled": any(
                    [
                        setting.new_product,
                        setting.recruit_closed,
                        setting.repayment,
                    ]
                )
            }
        )

    @extend_schema(
        request=NotifSettingSerializer,
        responses=NotifSettingResponseSerializer,
    )
    def post(self, request):
        s = NotifSettingSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        setting, _ = NotificationSetting.objects.get_or_create(user=request.user)
        for k, v in s.validated_data.items():
            setattr(setting, k, v)
        setting.save()
        return Response(
            {
                "new_product": setting.new_product,
                "recruit_closed": setting.recruit_closed,
                "repayment": setting.repayment,
            }
        )


class NotificationListView(APIView):
    @extend_schema(responses=NotificationListResponseSerializer)
    def get(self, request):
        rows = Notification.objects.filter(user=request.user).order_by("-id")[:100]
        return Response(
            {
                "results": [
                    {
                        "id": n.id,
                        "kind": n.kind,
                        "title": n.title,
                        "body": n.body,
                        "created_at": n.created_at,
                    }
                    for n in rows
                ]
            }
        )
