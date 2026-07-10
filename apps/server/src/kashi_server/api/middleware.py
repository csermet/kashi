"""Request-size guard.

FastAPI parses the whole body BEFORE dependencies run, so an unauthenticated
POST with a 200 MB JSON body drives the process to ~1 GB RSS and only then
answers 401 — trivially OOM-kills a memory-limited pod. nginx's
client_max_body_size only helps when nginx is actually in front, so the app
carries its own cap (defense in depth; review finding, Faz 3A/A2).

Bodies here are tiny: the largest legitimate one is an ingest request with a
few hint strings.
"""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 64 * 1024

_TOO_LARGE_HEADERS = [(b"content-type", b"application/json")]
_TOO_LARGE_BODY = b'{"error":"payload_too_large"}'


async def _reject(send: Send) -> None:
    await send({"type": "http.response.start", "status": 413, "headers": _TOO_LARGE_HEADERS})
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


class ContentLengthLimitMiddleware:
    """Rejects oversized bodies before a single byte reaches the app."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _reject(send)
            return

        # No/lying Content-Length (chunked upload): count what actually streams.
        received = 0
        exceeded = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Cut the stream short; the guard below sends 413.
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if exceeded and message["type"] == "http.response.start":
                await _reject(send)
                return
            if exceeded and message["type"] == "http.response.body":
                return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)
