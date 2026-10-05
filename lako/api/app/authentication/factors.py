"""Read legacy underscore-joined session methods without splitting factor names."""

from app.common.errors import ApiError
from app.common.models import Session

METHODS = ("RECOVERY_CODE", "EMAIL_CODE", "PASSWORD", "PASSKEY", "WEBAUTHN", "TOTP")


def authentication_factors(method: str) -> list[str]:
    factors = []
    remaining = method
    while remaining:
        factor = next((value for value in METHODS if remaining == value or remaining.startswith(value + "_")), None)
        if factor is None:
            # Do not invent factors for an unknown historical label.
            return []
        if factor not in factors:
            factors.append(factor)
        remaining = remaining[len(factor):].removeprefix("_")
    return factors


def require_primary_factor(session: Session) -> None:
    if not set(authentication_factors(session.authentication_method)) & {"PASSWORD", "PASSKEY", "WEBAUTHN"}:
        raise ApiError(403, "PRIMARY_FACTOR_REQUIRED", "Sign in with your password or passkey before a security change")


def record_second_factor(session: Session, method: str) -> None:
    require_primary_factor(session)
    factors = authentication_factors(session.authentication_method)
    if method not in factors:
        factors.append(method)
    session.authentication_method = "_".join(factors)
