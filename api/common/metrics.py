import json
import logging
import math

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.throttling import VitalsThrottle


class VitalSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    value = serializers.FloatField()
    path = serializers.CharField(max_length=2048)
    ts = serializers.FloatField()

    def validate(self, data):
        for field in ("value", "ts"):
            if type(self.initial_data[field]) not in (int, float) or not math.isfinite(data[field]):
                raise serializers.ValidationError({field: "A finite number is required."})
        return data


class VitalsView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (VitalsThrottle,)

    @extend_schema(request=VitalSerializer, responses={204: None})
    def post(self, request):
        serializer = VitalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        logging.getLogger("api.metrics").info(json.dumps(serializer.validated_data))
        return Response(status=204)
