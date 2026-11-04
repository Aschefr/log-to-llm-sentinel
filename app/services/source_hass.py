"""
Journal de Home Assistant, lu par son API WebSocket avec un jeton d'accès longue durée. Rien à installer côté Home
Assistant, et ni guillemet ni trace d'exception ne peuvent se perdre en route comme avec une automatisation.

Sentinel relit `system_log/list` : un journal dédoublonné (message, nombre de répétitions, dernière date) qui ne contient
que les WARNING et les ERROR. Cette commande est réservée aux administrateurs.
"""
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple

import websockets

from app.services.source_errors import SourceError

POLL_SECONDS = 10.0
OPEN_TIMEOUT_S = 10.0
ANSWER_TIMEOUT_S = 20.0
AUTH_RETRY_S = 60.0
MAX_REPEATS_PER_POLL = 20
MAX_EXCEPTION_LINES = 40


def websocket_url(address: str) -> str:
    url = address.strip().rstrip("/")
    if url.endswith("/api/websocket"):
        url = url[: -len("/api/websocket")]
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif not url.startswith(("ws://", "wss://")):
        url = "ws://" + url
    return url + "/api/websocket"


def entry_lines(entry: dict) -> List[str]:
    """Même forme que le gabarit webhook : les mots-clés et les événements d'une règle migrée restent valables."""
    message = entry.get("message")
    if isinstance(message, list):
        message = message[-1] if message else ""
    head = f"{entry.get('level', 'ERROR')} [{entry.get('name', '')}] {message}"
    exception = (entry.get("exception") or "").splitlines()[-MAX_EXCEPTION_LINES:]
    return head.splitlines() + exception


class LogDiff:
    """Ce qui est nouveau entre deux lectures du journal dédoublonné."""

    def __init__(self, since: float):
        self.since = since
        self.seen: Dict[Tuple, int] = {}

    def lines(self, entries: List[dict]) -> Tuple[List[str], float]:
        out, last = [], self.since
        for entry in sorted(entries, key=lambda e: e.get("timestamp") or 0):
            stamp = entry.get("timestamp") or 0
            message = entry.get("message")
            first = message[0] if isinstance(message, list) and message else message
            key = (entry.get("name"), entry.get("level"), str(first), str(entry.get("source")))
            count = entry.get("count") or 1
            before = self.seen.get(key)
            self.seen[key] = count
            if before is None:
                # Déjà vu avant cette connexion : seule la dernière occurrence peut être nouvelle
                new = (count if (entry.get("first_occurred") or 0) > self.since else 1) if stamp > self.since else 0
            elif count < before:
                new = count  # sorti du journal puis revenu : le compteur repart de zéro
            else:
                new = count - before
            if new:
                out += entry_lines(entry) * min(new, MAX_REPEATS_PER_POLL)
            last = max(last, stamp)
        self.since = last
        return out, last


class Session:
    def __init__(self, socket):
        self.socket, self.next_id = socket, 1

    async def _receive(self) -> dict:
        return json.loads(await asyncio.wait_for(self.socket.recv(), ANSWER_TIMEOUT_S))

    async def authenticate(self, token: str) -> Optional[str]:
        hello = await self._receive()
        if hello.get("type") != "auth_required":
            raise SourceError("hass_protocol", hello.get("type"))
        await self.socket.send(json.dumps({"type": "auth", "access_token": token}))
        answer = await self._receive()
        if answer.get("type") == "auth_invalid":
            raise SourceError("hass_token_refused", retry_seconds=AUTH_RETRY_S)
        if answer.get("type") != "auth_ok":
            raise SourceError("hass_protocol", answer.get("type"))
        return answer.get("ha_version")

    async def system_log(self) -> List[dict]:
        request_id = self.next_id
        self.next_id += 1
        await self.socket.send(json.dumps({"id": request_id, "type": "system_log/list"}))
        while True:
            answer = await self._receive()
            if answer.get("id") == request_id and answer.get("type") == "result":
                break
        if not answer.get("success"):
            code = (answer.get("error") or {}).get("code")
            raise SourceError("hass_not_admin" if code == "unauthorized" else "hass_error", code, retry_seconds=AUTH_RETRY_S)
        return answer.get("result") or []


def _unreachable(e: Exception) -> SourceError:
    return SourceError("hass_unreachable", type(e).__name__)


async def run(spec: dict, sink):
    diff = LogDiff(sink.checkpoint or time.time())
    sink.checkpoint = diff.since
    try:
        async with websockets.connect(websocket_url(spec["address"]), open_timeout=OPEN_TIMEOUT_S, max_size=32 * 1024 * 1024) as socket:
            session = Session(socket)
            await session.authenticate(spec["secret"] or "")
            sink.status("connected")
            while True:
                lines, last = diff.lines(await session.system_log())
                if lines:
                    await sink.emit(lines, last)
                await asyncio.sleep(POLL_SECONDS)
    except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
        raise _unreachable(e)


async def test(address: str, token: str) -> dict:
    try:
        async with websockets.connect(websocket_url(address), open_timeout=OPEN_TIMEOUT_S, max_size=32 * 1024 * 1024) as socket:
            session = Session(socket)
            version = await session.authenticate(token or "")
            entries = await session.system_log()
            return {"version": version, "entries": len(entries)}
    except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
        raise _unreachable(e)
