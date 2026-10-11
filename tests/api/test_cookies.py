from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.api.cookies import read_session, sign_session

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_round_trip_and_expiry() -> None:
    account = uuid4()
    cookie = sign_session("s", account, NOW + timedelta(hours=1))
    assert read_session("s", cookie, NOW) == account
    assert read_session("s", cookie, NOW + timedelta(hours=1)) is None


def test_forged_or_malformed_cookies_are_rejected() -> None:
    account = uuid4()
    cookie = sign_session("s", account, NOW + timedelta(hours=1))
    other = uuid4()
    forged = cookie.replace(str(account), str(other))
    assert read_session("other-secret", cookie, NOW) is None
    assert read_session("s", forged, NOW) is None
    for broken in (None, "", "x", "a.b.c", f"{account}.later.{cookie.rsplit('.', 1)[1]}"):
        assert read_session("s", broken, NOW) is None
