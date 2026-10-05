import hashlib
import json

from django.db import transaction
from rest_framework.response import Response

from api.common.exceptions import IdempotencyKeyMismatch, IdempotencyKeyRequired
from api.ledger.models import IdempotencyRecord


def _payload_hash(user_id, path, payload):
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{user_id}:{path}:{canonical}".encode()).hexdigest()


def run_idempotent(request, payload, handler):
    """Idempotent-Key 규약 (api-spec §13) 실행 래퍼.

    handler() -> (body, http_status). (user, key) 유니크로 요청을 기록하고,
    재수신 시 저장된 응답을 200으로 재생한다. 같은 키 + 다른 페이로드는 409.
    동시에 들어온 동일 키 요청은 행 잠금으로 직렬화된다.
    """
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise IdempotencyKeyRequired()

    request_hash = _payload_hash(request.user.id, request.path, payload)
    with transaction.atomic():
        record, created = IdempotencyRecord.objects.select_for_update().get_or_create(
            user=request.user, key=key, defaults={"request_hash": request_hash}
        )
        if not created:
            if record.request_hash != request_hash:
                raise IdempotencyKeyMismatch()
            if record.response_body is None:
                raise IdempotencyKeyMismatch(
                    "request with this key is still in progress"
                )
            return Response(record.response_body, status=200)
        body, status_code = handler()
        record.response_body = body
        record.response_status = status_code
        record.save(update_fields=["response_body", "response_status"])
        return Response(body, status=status_code)
