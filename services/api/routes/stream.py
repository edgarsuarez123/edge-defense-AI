"""
GET /stream — Server-Sent Events stream for real-time operator UI updates.

Why SSE and not WebSocket:
  The edge node only pushes data to the UI — it never receives data through the stream.
  SSE is unidirectional, simpler to proxy, and reconnects automatically in browsers.
  WebSocket adds bidirectional complexity with no benefit here.
  See decision-log.md for full rationale.

Why token via query param (?token=) and not Authorization header:
  The browser's EventSource API does not support custom headers. Tokens passed via
  query param are not ideal (they appear in server logs) but are the standard
  workaround for SSE auth. The stream is on an isolated LAN — the threat model
  doesn't include log scraping.

Why XREAD with block=1000:
  block=1000ms means the call returns after at most 1 second even if no messages
  arrived. This lets us emit SSE keepalive comments on each empty cycle so nginx
  and browsers don't drop idle connections (default 60s timeout).

Why last_id starts at "$":
  "$" means "messages added after this client connected." We don't replay history
  on connect — the UI does an initial REST fetch for existing incidents/tracks and
  only needs real-time deltas from the stream.
"""

import json
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["stream"])


async def _event_generator(token: str) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields SSE event dicts.
    sse-starlette accepts dicts with keys: event, data, id, retry.
    """
    api_key = os.environ.get("API_KEY", "")
    if token != api_key:
        yield {"event": "error", "data": json.dumps({"detail": "Unauthorized"})}
        return

    r = aioredis.from_url(os.environ["REDIS_URL"])
    # "$" = only new messages from this point forward (no history replay)
    last_id = "$"

    # Announce connection so the UI can confirm the stream is live
    yield {
        "event": "system_status",
        "data": json.dumps(
            {"state": "connected", "ts": datetime.now(timezone.utc).isoformat()}
        ),
    }

    try:
        while True:
            # block=1000 → returns after 1s even with no messages (keepalive cycle)
            results = await r.xread({"incident_events": last_id}, count=10, block=1000)

            if results:
                for _stream_name, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id  # advance cursor
                        payload = {
                            k.decode() if isinstance(k, bytes) else k: (
                                v.decode() if isinstance(v, bytes) else v
                            )
                            for k, v in fields.items()
                        }
                        yield {
                            "event": "track_update",
                            "id": msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                            "data": json.dumps(payload),
                        }
            else:
                # No messages in this 1s window — emit keepalive comment
                # SSE comments start with ":" and are ignored by browsers but
                # prevent proxy timeouts on idle connections.
                yield {"comment": "keepalive"}

    finally:
        await r.aclose()


@router.get("/stream")
async def stream(token: str = "") -> EventSourceResponse:
    """
    Single SSE connection for all real-time event types.
    token — pass the API_KEY value here (EventSource cannot send headers).
    """
    return EventSourceResponse(_event_generator(token))
