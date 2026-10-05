from rest_framework import serializers


class NoticeListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    category = serializers.CharField()
    title = serializers.CharField()
    created_at = serializers.DateTimeField()


class NoticeListResponseSerializer(serializers.Serializer):
    results = NoticeListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()


class NoticeDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    category = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    attachments = serializers.ListField(child=serializers.CharField())
    created_at = serializers.DateTimeField()


class FaqItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    category = serializers.CharField()
    question = serializers.CharField()
    answer = serializers.CharField()


class FaqListResponseSerializer(serializers.Serializer):
    results = FaqItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()


class FaqKeywordsSerializer(serializers.Serializer):
    keywords = serializers.ListField(child=serializers.CharField())


class EventListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    summary = serializers.CharField()
    status = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_blank=True)
    start_at = serializers.DateTimeField(allow_null=True)
    end_at = serializers.DateTimeField(allow_null=True)


class EventListResponseSerializer(serializers.Serializer):
    results = EventListItemSerializer(many=True)
    total = serializers.IntegerField()
    page = serializers.IntegerField()


class OngoingEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_blank=True)


class EventDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    summary = serializers.CharField()
    body = serializers.CharField()
    status = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_blank=True)
    reward_points = serializers.IntegerField()
    start_at = serializers.DateTimeField(allow_null=True)
    end_at = serializers.DateTimeField(allow_null=True)
    prev_id = serializers.IntegerField(allow_null=True)
    next_id = serializers.IntegerField(allow_null=True)
    ongoing = OngoingEventSerializer(many=True)


class EventEnterResponseSerializer(serializers.Serializer):
    entered = serializers.BooleanField()
    reward_points = serializers.IntegerField()


class DisclosureSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    kpi = serializers.DictField()
    tabs = serializers.DictField()
    published_at = serializers.DateTimeField()


class DisclosureListResponseSerializer(serializers.Serializer):
    results = DisclosureSerializer(many=True)


class NewsItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    source = serializers.CharField()
    url = serializers.CharField()
    thumbnail_url = serializers.CharField(allow_blank=True)
    published_at = serializers.DateField()


class NewsListResponseSerializer(serializers.Serializer):
    results = NewsItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class TermSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    updated_at = serializers.DateTimeField()
