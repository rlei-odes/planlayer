"""A cap on a request's body size: a request larger than its path's limit is refused with 413,
by its Content-Length or, without one, once its streamed body passes the limit. The limits are
far above what the app's own requests carry; they stop runaway input before it is read into
memory. HR's salary upload takes files up to its own limit (ADR 0025)."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from planlayer.personnel.salary_files import MAX_BYTES as SALARY_FILE_BYTES

MAX_BODY = 2 * 1024 * 1024
# The upload sends the file as the body; a little room above the file's own limit.
UPLOADS = ("/salaries/check", "/salaries/apply")
MAX_UPLOAD = SALARY_FILE_BYTES + 1024 * 1024


def body_limit_for(path: str) -> int:
    return MAX_UPLOAD if path.endswith(UPLOADS) else MAX_BODY


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit_for(scope["path"])
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            await _refuse(send, limit)
            return

        # A streamed body: once it passes the limit, the 413 is sent here and the body ends as
        # if the client left; whatever the app answers then is dropped. (An exception would be
        # turned into a 400 by the body parser.)
        received = 0
        started = refused = False

        async def counted() -> Message:
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request" and not refused:
                received += len(message.get("body", b""))
                if received > limit:
                    refused = True
                    if not started:
                        await _refuse(send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def tracked(message: Message) -> None:
            nonlocal started
            if refused:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except Exception:
            if not refused:
                raise


async def _refuse(send: Send, limit: int) -> None:
    body = f'{{"detail":"the request is larger than {limit // (1024 * 1024)} MB"}}'.encode()
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
