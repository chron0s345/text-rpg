"""Security headers on every response (ARCHITECTURE.md, "Fehler & Schutz").

- No framing (clickjacking: a foreign page must not put the game under someone's clicks).
- Content-Security-Policy: scripts, styles and everything else only from this origin. The pages
  have no inline scripts or styles; htmx runs without ``eval`` (``htmx-config`` in base.html).
- The interactive API docs (FastAPI's ``/docs``, ``/redoc``) load Swagger from a CDN with inline
  scripts; they only get the framing and sniffing headers.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY: Final = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
BASE_HEADERS: Final = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
DOCS_PREFIXES: Final = ("/docs", "/redoc")


class SecurityHeaders:
    """Pure ASGI middleware: only adds headers when a response starts. (``BaseHTTPMiddleware``
    would wrap streaming responses and get in the way of the SSE stream's disconnect handling.)"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with_csp = not str(scope.get("path", "")).startswith(DOCS_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in BASE_HEADERS.items():
                    headers.setdefault(name, value)
                if with_csp:
                    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_headers)
