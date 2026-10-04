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
    """Rejects oversized bodies before a single byte reaches the app.

    `overrides` maps an exact path to its own cap — the BYO-audio upload
    endpoint (Faz 5 P4) legitimately carries tens of MB; everything else
    keeps the tiny JSON ceiling."""

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = MAX_BODY_BYTES,
        overrides: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = overrides or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        limit = self._limit_for(scope, headers)

        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send)
            return

        # No/lying Content-Length (chunked upload): count what actually streams.
        cap = _BodyCap(receive, send, limit)
        await self.app(scope, cap.receive, cap.send)

    def _limit_for(self, scope: Scope, headers: Headers) -> int:
        limit = self.overrides.get(scope.get("path", ""), self.max_bytes)
        if limit != self.max_bytes and not headers.get("authorization"):
            # The big-body override is for AUTHENTICATED uploads; auth deps
            # run only after the body is parsed, so an anonymous client could
            # otherwise stream the full cap before every 401 (reviewer).
            # Header PRESENCE is enough here — the real key check still
            # happens in deps; this only denies the free bandwidth.
            return self.max_bytes
        return limit


class _BodyCap:
    """Counts the request body as it streams. Past the limit it cuts the
    stream short and turns whatever the app answers into the 413."""

    def __init__(self, receive: Receive, send: Send, limit: int) -> None:
        self._receive = receive
        self._send = send
        self._limit = limit
        self._received = 0
        self._exceeded = False

    async def receive(self) -> Message:
        message = await self._receive()
        if message["type"] == "http.request":
            self._received += len(message.get("body", b""))
            if self._received > self._limit:
                self._exceeded = True
                # Cut the stream short; send() below answers 413.
                return {"type": "http.disconnect"}
        return message

    async def send(self, message: Message) -> None:
        if self._exceeded and message["type"] == "http.response.start":
            await _reject(self._send)
            return
        if self._exceeded and message["type"] == "http.response.body":
            return
        await self._send(message)
