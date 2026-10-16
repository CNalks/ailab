"""ASGI middleware: request ids + access log, request-size limit; log redaction filter.

Logs never contain request headers, bodies or query strings (so no API keys or payloads).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

logger = logging.getLogger("ailab.api")
_RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RedactFilter(logging.Filter):
    """Replace configured secrets and anything that looks like an API-key header value."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]
        self._hdr = re.compile(r"(x-api-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self._secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = self._hdr.sub(r"\1***", msg), ()
        return True


def error_body(code: str, message: str, request_id: str | None, details=None) -> bytes:
    err: dict = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        err["details"] = details
    return json.dumps({"error": err}).encode()


class _TooLarge(Exception):
    pass


class RequestContextMiddleware:
    """Assigns ``request_id`` (scope['state']), echoes it as X-Request-ID, writes an access log."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = None
        for k, v in scope.get("headers", []):
            if k == b"x-request-id" and _RID_RE.match(v.decode("latin-1")):
                rid = v.decode("latin-1")
        rid = rid or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request_id=%s %s %s -> %s %.0fms",
                rid,
                scope["method"],
                scope["path"],
                status["code"],
                (time.perf_counter() - t0) * 1000,
            )


class BodySizeLimitMiddleware:
    """413 for bodies above ``max_bytes`` (Content-Length checked up front, stream counted)."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = scope.get("state", {}).get("request_id")

        async def too_large():
            body = error_body(
                "payload_too_large", f"request body exceeds {self.max_bytes} bytes", rid
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        return await too_large()
                except ValueError:
                    pass
        seen = 0
        state = {"overflow": False, "sent": False}

        async def counting_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    state["overflow"] = True
                    raise _TooLarge
            return msg

        async def tracking_send(message):
            if state["overflow"]:
                # the framework may have turned our abort into a 400/422: answer 413 instead
                if not state["sent"]:
                    state["sent"] = True
                    await too_large()
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            pass
        if state["overflow"] and not state["sent"]:
            await too_large()
