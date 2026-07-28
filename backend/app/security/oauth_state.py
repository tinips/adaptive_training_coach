"""One-time OAuth state generation and hashing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class OAuthStateToken:
    """Raw browser state plus the digest safe to persist."""

    raw: str
    digest: str
    expires_at: datetime


def digest_oauth_state(raw_state: str) -> str:
    """Return the canonical SHA-256 digest for a raw state value."""

    if not raw_state:
        raise ValueError("OAuth state must not be empty.")
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def oauth_state_matches(raw_state: str, expected_digest: str) -> bool:
    """Compare a raw state to a persisted digest in constant time."""

    try:
        actual = digest_oauth_state(raw_state)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected_digest)


def new_oauth_state(
    *,
    ttl: timedelta,
    now: datetime | None = None,
) -> OAuthStateToken:
    """Create a high-entropy, expiring OAuth state."""

    if ttl <= timedelta(0):
        raise ValueError("OAuth state TTL must be positive.")
    issued_at = now or datetime.now(UTC)
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise ValueError("OAuth state timestamps must be timezone-aware.")
    raw = secrets.token_urlsafe(48)
    return OAuthStateToken(
        raw=raw,
        digest=digest_oauth_state(raw),
        expires_at=issued_at.astimezone(UTC) + ttl,
    )
