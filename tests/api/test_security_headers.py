"""Security headers: no framing anywhere, a strict CSP on the game's pages and API (the API docs
load Swagger from a CDN and only get the framing headers)."""

from __future__ import annotations

from app.api.security import CONTENT_SECURITY_POLICY
from tests.api.conftest import Api


async def test_pages_and_api_get_all_headers(api: Api) -> None:
    async with api.client() as client:
        for path in ("/welcome", "/health", "/static/app.js", "/characters"):
            response = await client.get(path)
            assert response.headers["X-Frame-Options"] == "DENY", path
            assert response.headers["X-Content-Type-Options"] == "nosniff", path
            assert response.headers["Referrer-Policy"] == "same-origin", path
            assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY, path


async def test_csp_forbids_framing_and_foreign_scripts() -> None:
    directives = dict(d.split(" ", 1) for d in CONTENT_SECURITY_POLICY.split("; "))
    assert directives["frame-ancestors"] == "'none'"
    assert directives["script-src"] == "'self'"  # no 'unsafe-inline', no 'unsafe-eval'


async def test_api_docs_keep_working(api: Api) -> None:
    async with api.client() as client:
        response = await client.get("/docs")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" not in response.headers
