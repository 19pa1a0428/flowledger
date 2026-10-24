"""Opaque bearer authentication; callers own token issuance and rotation."""
from dataclasses import dataclass
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


class TokenAuthenticator:
    """Matches only SHA-256 token digests and returns configured principals."""

    def __init__(self, credentials: Mapping[str, Principal]):
        records = []
        for token, principal in credentials.items():
            if type(token) is not str or not 32 <= len(token) <= 4_096:
                raise ValueError("Bearer tokens must contain 32-4096 characters")
            if not isinstance(principal, Principal):
                raise TypeError("Credentials must map tokens to Principal values")
            records.append((sha256(token.encode("utf-8")).digest(), principal))
        self._records = tuple(records)

    def authenticate(self, token: str) -> Principal | None:
        """Compare every configured digest to avoid early-match timing differences."""
        if type(token) is not str or not token:
            return None
        presented = sha256(token.encode("utf-8")).digest()
        matched = None
        for expected, principal in self._records:
            if hmac.compare_digest(expected, presented):
                matched = principal
        return matched

    @classmethod
    def from_json(cls, value: str | None) -> "TokenAuthenticator":
        """Parse FLOWLEDGER_AUTH_TOKENS; missing configuration denies every request."""
        if value is None or not value.strip():
            return cls({})
        try:
            items = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("FLOWLEDGER_AUTH_TOKENS must be valid JSON") from error
        if not isinstance(items, list):
            raise ValueError("FLOWLEDGER_AUTH_TOKENS must be a JSON list")
        credentials = {}
        for item in items:
            if not isinstance(item, dict) or set(item) != {"token", "subject", "role"}:
                raise ValueError("Each credential needs only token, subject and role")
            try:
                principal = Principal(item["subject"], Role(item["role"]))
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid credential subject or role") from error
            token = item["token"]
            if type(token) is not str or not 32 <= len(token) <= 4_096:
                raise ValueError("Bearer tokens must contain 32-4096 characters")
            if token in credentials:
                raise ValueError("Duplicate bearer token")
            credentials[token] = principal
        return cls(credentials)
