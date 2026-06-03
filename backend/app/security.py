import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
_pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(plaintext: str) -> str:
    return _pwd_ctx.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plaintext, hashed)
    except Exception:
        return False


def create_access_token(subject: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def create_customer_access_token(customer_id: int, email: str) -> str:
    """Müşteri JWT'si — admin token'larından `type` ile ayrılır."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(customer_id),
        "email": email,
        "type": "customer_access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_access_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def decode_customer_token(token: str) -> dict | None:
    """Müşteri token'ını decode et + type='customer_access' garanti et."""
    payload = decode_access_token(token)
    if not payload or payload.get("type") != "customer_access":
        return None
    return payload


def create_refresh_token() -> tuple[str, str, datetime]:
    """Refresh token: kullanıcıya verilen plaintext + DB'ye hash + expiry."""
    raw = secrets.token_urlsafe(48)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_ttl_days)
    return raw, digest, expiry


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
    except JWTError:
        return None


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
