import base64
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.common.config import get_settings


def b64int(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class SigningKeys:
    def __init__(self) -> None:
        settings = get_settings()
        if settings.jwt_private_key:
            self.private_key = serialization.load_pem_private_key(
                settings.jwt_private_key.replace("\\n", "\n").encode(), password=None
            )
        else:
            self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()
        self.jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": settings.jwt_key_id,
            "n": b64int(numbers.n),
            "e": b64int(numbers.e),
        }

    def sign(self, claims: dict) -> str:
        settings = get_settings()
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": settings.jwt_key_id})


@lru_cache
def get_signing_keys() -> SigningKeys:
    return SigningKeys()
