# drf-spectacular 인증 확장 등록 (import 부수효과)
try:
    from api.common import schema  # noqa: F401
except Exception:  # migrations 등 초기 단계에서 drf_spectacular 미설치 대비
    pass
