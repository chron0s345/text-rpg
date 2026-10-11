"""Signed session cookie: ``<account id>.<expiry unix time>.<HMAC-SHA256>``.

Stateless: the cookie only names the account. Which character plays is stored in the database
(the account's one online character), so logging a character out never depends on the cookie.
Logging out of the account deletes the cookie; a copied cookie stays valid until it expires.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from uuid import UUID

COOKIE_NAME = "rpg_session"


def _signature(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def sign_session(secret: str, account_id: UUID, expires_at: datetime) -> str:
    payload = f"{account_id}.{int(expires_at.timestamp())}"
    return f"{payload}.{_signature(secret, payload)}"


def read_session(secret: str, cookie: str | None, now: datetime) -> UUID | None:
    """The account id, or ``None`` for a missing, forged, malformed or expired cookie."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    account, expires, signature = parts
    if not hmac.compare_digest(signature, _signature(secret, f"{account}.{expires}")):
        return None
    try:
        if int(expires) <= now.timestamp():
            return None
        return UUID(account)
    except ValueError:
        return None
