"""Logs d'un conteneur Docker, lus en continu par le proxy en lecture seule du diagnostic. Le socket n'est jamais monté."""
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

from app.services.source_errors import SourceError

CONNECT_TIMEOUT_S = 10.0
# Pas de ligne pendant ce temps : on rouvre le flux, une connexion morte ne prévient pas toujours
READ_TIMEOUT_S = 300.0
STOPPED_RETRY_S = 30.0
MISSING_RETRY_S = 30.0
CATCH_UP_LINES = 5000
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def parse_timestamp(value: str) -> Optional[float]:
    """Horodatage Docker (RFC 3339, jusqu'à la nanoseconde) en secondes."""
    match = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z$", value)
    if not match:
        return None
    seconds = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return seconds + float(f"0.{match.group(2) or 0}")


class LogStream:
    """Découpe le flux de logs en lignes. Sans TTY, Docker entrelace stdout et stderr en trames de 8 octets d'en-tête ;
    une ligne peut arriver en plusieurs morceaux, et une trame à cheval sur deux lectures."""

    def __init__(self, tty: bool):
        self.tty = tty
        self._frames = b""
        self._partial = {1: b"", 2: b""}

    def _lines(self, stream: int, data: bytes) -> List[bytes]:
        parts = (self._partial[stream] + data).split(b"\n")
        self._partial[stream] = parts.pop()
        return parts

    def feed(self, chunk: bytes) -> List[Tuple[Optional[float], str]]:
        raw = []
        if self.tty:
            raw = self._lines(1, chunk)
        else:
            self._frames += chunk
            while len(self._frames) >= 8:
                stream, size = self._frames[0], int.from_bytes(self._frames[4:8], "big")
                if len(self._frames) < 8 + size:
                    break
                raw += self._lines(2 if stream == 2 else 1, self._frames[8:8 + size])
                self._frames = self._frames[8 + size:]
        out = []
        for item in raw:
            text = item.decode("utf-8", errors="replace").rstrip("\r")
            stamp, _, line = text.partition(" ")
            seconds = parse_timestamp(stamp)
            out.append((seconds, ANSI.sub("", line if seconds is not None else text)))
        return out


def _raise_for(response: httpx.Response):
    if response.status_code == 404:
        raise SourceError("container_missing", retry_seconds=MISSING_RETRY_S)
    if response.status_code == 403:
        raise SourceError("docker_denied", retry_seconds=MISSING_RETRY_S)
    if response.status_code >= 400:
        raise SourceError("docker_error", f"HTTP {response.status_code}")


async def run(spec: dict, sink):
    name = spec["container"]
    if not CONTAINER_RE.match(name):
        raise SourceError("container_invalid", retry_seconds=MISSING_RETRY_S)
    timeout = httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(base_url=spec["url"], timeout=timeout) as client:
            info = await client.get(f"/containers/{name}/json")
            _raise_for(info)
            info = info.json()
            if not (info.get("State") or {}).get("Running"):
                raise SourceError("container_stopped", retry_seconds=STOPPED_RETRY_S, state="waiting")
            stream = LogStream(bool((info.get("Config") or {}).get("Tty")))
            # Première lecture : à partir de maintenant. Ensuite : on reprend où on s'était arrêté, redémarrages compris
            since = sink.checkpoint or time.time()
            params = {"follow": "1", "stdout": "1", "stderr": "1", "timestamps": "1", "since": f"{since:.9f}", "tail": str(CATCH_UP_LINES)}
            sink.checkpoint = since
            async with client.stream("GET", f"/containers/{name}/logs", params=params) as response:
                _raise_for(response)
                sink.status("connected")
                async for chunk in response.aiter_bytes():
                    lines, last = [], sink.checkpoint
                    for seconds, line in stream.feed(chunk):
                        if seconds is None or seconds > since:  # `since` est inclusif : pas de doublon à la reprise
                            lines.append(line)
                            last = max(last, seconds or last)
                    if lines:
                        await sink.emit(lines, last)
    except httpx.ReadTimeout:
        return
    except httpx.HTTPError as e:
        raise SourceError("docker_unreachable", type(e).__name__)
