from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AuthAnonThrottle(AnonRateThrottle):
    scope = "auth"


class AuthUserThrottle(UserRateThrottle):
    scope = "auth"


class PinThrottle(UserRateThrottle):
    scope = "pin"


class AppCodeIssueThrottle(UserRateThrottle):
    scope = "app_code"


class AppCodeExchangeThrottle(AnonRateThrottle):
    scope = "app_code"


class PasswordResetThrottle(AnonRateThrottle):
    scope = "reset"


class VitalsThrottle(AnonRateThrottle):
    scope = "vitals"
