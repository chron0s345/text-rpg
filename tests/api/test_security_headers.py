"""Security headers: no framing anywhere, a strict CSP on the game's pages and API (the API docs
load Swagger from a CDN and only get the framing headers)."""

from __future__ import annotations

import json
import re

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


async def test_htmx_never_writes_inline_code_or_styles(api: Api) -> None:
    """The CSP blocks inline styles, so htmx must not create any: no indicator <style>, and no
    copying of ``style`` attributes when it settles a swap (an empty ``style=""`` that a browser
    extension or autofill left on an input would otherwise be re-applied and reported)."""
    async with api.client() as client:
        page = (await client.get("/welcome")).text
    [config] = re.findall(r"<meta name=\"htmx-config\" content='([^']*)'>", page)
    settings = json.loads(config)
    assert settings["allowEval"] is False
    assert settings["includeIndicatorStyles"] is False
    assert "style" not in settings["attributesToSettle"]
