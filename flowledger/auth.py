"""Opaque bearer authentication; callers own token issuance and rotation."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import hmac
import json
from typing import Mapping


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role

    def __post_init__(self):
        if (type(self.subject) is not str or self.subject != self.subject.strip()
                or not 1 <= len(self.subject) <= 100):
            raise ValueError("Subject must be 1-100 non-whitespace-padded characters")
        if not isinstance(self.role, Role):
            raise TypeError("Role must be a Role value")


@dataclass(frozen=True)
class Credential:
    principal: Principal
    expires_at: datetime | None = None
    revoked: bool = False

    def __post_init__(self):
        if not isinstance(self.principal, Principal):
            raise TypeError("Credential principal must be a Principal")
        if self.expires_at is not None:
            if (not isinstance(self.expires_at, datetime)
                    or self.expires_at.utcoffset() is None):
                raise ValueError("Credential expiry must include a timezone")
            object.__setattr__(self, "expires_at",
                               self.expires_at.astimezone(timezone.utc))
        if type(self.revoked) is not bool:
            raise TypeError("Credential revoked flag must be a boolean")


class TokenAuthenticator:
    """Matches only SHA-256 token digests and returns configured principals."""

    def __init__(self, credentials: Mapping[str, Principal | Credential],
                 clock: Callable[[], datetime] | None = None):
        records = []
        for token, value in credentials.items():
            if type(token) is not str or not 32 <= len(token) <= 4_096:
                raise ValueError("Bearer tokens must contain 32-4096 characters")
            credential = Credential(value) if isinstance(value, Principal) else value
            if not isinstance(credential, Credential):
                raise TypeError("Credentials must map tokens to Principal or Credential values")
            records.append((sha256(token.encode("utf-8")).digest(), credential))
        self._records = tuple(records)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def authenticate(self, token: str) -> Principal | None:
        """Compare every configured digest to avoid early-match timing differences."""
        if type(token) is not str or not token:
            return None
        presented = sha256(token.encode("utf-8")).digest()
        matched: Credential | None = None
        for expected, credential in self._records:
            if hmac.compare_digest(expected, presented):
                matched = credential
        if matched is None or matched.revoked:
            return None
        if matched.expires_at is not None:
            now = self._clock()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise RuntimeError("Authentication clock must return a timezone-aware datetime")
            if now.astimezone(timezone.utc) >= matched.expires_at:
                return None
        return matched.principal

    @classmethod
    def from_json(cls, value: str | None,
                  clock: Callable[[], datetime] | None = None) -> "TokenAuthenticator":
        """Parse FLOWLEDGER_AUTH_TOKENS; missing configuration denies every request."""
        if value is None or not value.strip():
            return cls({}, clock=clock)
        try:
            items = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("FLOWLEDGER_AUTH_TOKENS must be valid JSON") from error
        if not isinstance(items, list):
            raise ValueError("FLOWLEDGER_AUTH_TOKENS must be a JSON list")
        credentials = {}
        for item in items:
            required = {"token", "subject", "role"}
            allowed = required | {"expires_at", "revoked"}
            if (not isinstance(item, dict) or not required <= set(item)
                    or not set(item) <= allowed):
                raise ValueError(
                    "Each credential needs token, subject and role; only expires_at and revoked are optional"
                )
            try:
                principal = Principal(item["subject"], Role(item["role"]))
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid credential subject or role") from error
            token = item["token"]
            if type(token) is not str or not 32 <= len(token) <= 4_096:
                raise ValueError("Bearer tokens must contain 32-4096 characters")
            if token in credentials:
                raise ValueError("Duplicate bearer token")
            expires_at = item.get("expires_at")
            if expires_at is not None:
                if type(expires_at) is not str:
                    raise ValueError("Credential expires_at must be an ISO 8601 string")
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ValueError("Credential expires_at must be an ISO 8601 datetime") from error
                if expires_at.utcoffset() is None:
                    raise ValueError("Credential expires_at must include a timezone")
            revoked = item.get("revoked", False)
            if type(revoked) is not bool:
                raise ValueError("Credential revoked must be a boolean")
            credentials[token] = Credential(principal, expires_at, revoked)
        return cls(credentials, clock=clock)
