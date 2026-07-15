import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
PASSWORD_ITERATIONS = 310_000
TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_TOKEN_TTL_HOURS", "24")) * 3600


class AuthenticationError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    load_dotenv(BASE_DIR / ".env", override=True)
    value = os.environ.get("AUTH_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("AUTH_SECRET must be configured with at least 32 characters.")
    return value.encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(actual, _decode(expected))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, email: str) -> tuple[str, int]:
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = _encode(
        json.dumps(
            {"sub": user_id, "email": email, "exp": expires_at},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _encode(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires_at


def verify_access_token(token: str) -> dict:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise AuthenticationError("Invalid session token.")
        claims = json.loads(_decode(payload))
        if int(claims.get("exp", 0)) <= int(time.time()):
            raise AuthenticationError("Session expired.")
        if not claims.get("sub"):
            raise AuthenticationError("Invalid session token.")
        return claims
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        if isinstance(error, AuthenticationError):
            raise
        raise AuthenticationError("Invalid session token.") from error
