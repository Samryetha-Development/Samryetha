import base64
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from app.common.config import get_settings

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def normalize_email(value: str) -> str:
    local, separator, domain = value.strip().partition("@")
    return f"{local.casefold()}{separator}{domain.casefold()}"


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def credential_cipher() -> Fernet:
    key = hashlib.sha256(get_settings().credential_encryption_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_credential(value: str) -> str:
    return credential_cipher().encrypt(value.encode()).decode()


def decrypt_credential(value: str) -> str:
    return credential_cipher().decrypt(value.encode()).decode()
