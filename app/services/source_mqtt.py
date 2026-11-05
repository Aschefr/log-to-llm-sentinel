"""Messages d'un serveur MQTT (Zigbee2MQTT, Tasmota, ESPHome…). Le client tourne dans son fil et se reconnecte seul."""
import asyncio
import uuid
from typing import List, Optional

import paho.mqtt.client as mqtt

from app.services.source_errors import SourceError

CONNECT_TIMEOUT_S = 8.0
SAMPLE_SECONDS = 3.0
MAX_QUEUE = 20_000
MAX_BATCH = 500
BATCH_PAUSE_S = 0.2
# Codes MQTT 3.1.1 de refus : identifiants ou droits
REFUSED_AUTH = {"Bad user name or password", "Not authorized"}


def valid_topic(topic: str) -> bool:
    if not topic or len(topic) > 512 or "\x00" in topic:
        return False
    levels = topic.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def message_lines(topic: str, payload: bytes) -> List[str]:
    text = payload.decode("utf-8", errors="replace")
    return [f"{topic} {line}" for line in text.splitlines() if line.strip()] or [topic]


def _client(spec: dict) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sentinel-{uuid.uuid4().hex[:12]}")
    if spec.get("username"):
        client.username_pw_set(spec["username"], spec.get("secret") or None)
    if spec.get("use_tls"):
        client.tls_set()
    client.reconnect_delay_set(1, 60)
    return client


def _refusal(reason_code) -> SourceError:
    name = getattr(reason_code, "getName", lambda: str(reason_code))()
    if name in REFUSED_AUTH:
        return SourceError("mqtt_refused", name)
    return SourceError("mqtt_error", name)


async def run(spec: dict, sink):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(MAX_QUEUE)
    topic = spec["topic"]
    if not valid_topic(topic):
        raise SourceError("mqtt_topic_invalid", retry_seconds=60)

    def report(state, code=None, detail=None):
        loop.call_soon_threadsafe(sink.status, state, code, detail)

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            error = _refusal(reason_code)
            report("error", error.code, error.detail)
            return
        client.subscribe(topic)
        report("connected")

    def on_connect_fail(_client, _userdata):
        report("error", "mqtt_unreachable")

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            report("error", "mqtt_unreachable", getattr(reason_code, "getName", lambda: str(reason_code))())

    def enqueue(item):
        if queue.full():  # un sujet trop bavard : on sacrifie les plus anciens, pas le processus
            queue.get_nowait()
        queue.put_nowait(item)

    def on_message(_client, _userdata, message):
        if not message.retain:  # un message retenu est un état ancien, pas un événement
            loop.call_soon_threadsafe(enqueue, message_lines(message.topic, message.payload))

    client = _client(spec)
    client.on_connect, client.on_connect_fail, client.on_disconnect, client.on_message = on_connect, on_connect_fail, on_disconnect, on_message
    client.connect_async(spec["address"], spec.get("port") or 1883)
    client.loop_start()
    try:
        while True:
            lines = list(await queue.get())
            while not queue.empty() and len(lines) < MAX_BATCH:
                lines += queue.get_nowait()
            await sink.emit(lines)
            await asyncio.sleep(BATCH_PAUSE_S)
    finally:
        client.loop_stop()
        client.disconnect()


def topic_overview(topics: set, per_root: int = 3, limit: int = 30) -> dict:
    """Shared first levels as `root/#`, busiest first, and a few topics of each root rather than the first 30 in alphabetical order."""
    roots: dict = {}
    for t in sorted(topics):
        roots.setdefault(t.split("/")[0] if "/" in t else "", []).append(t)
    order = sorted(roots, key=lambda r: (-len(roots[r]), r))
    prefixes = [{"topic": f"{r}/#", "count": len(roots[r])} for r in order if r and len(roots[r]) > 1][:12]
    sample = [t for r in order for t in roots[r][:per_root]][:limit]
    return {"prefixes": prefixes, "topics": sample}


async def test(spec: dict, topic: Optional[str] = None) -> dict:
    """Vérifie la connexion et écoute un instant : montrer les sujets actifs évite de les deviner."""
    loop = asyncio.get_running_loop()
    done: asyncio.Future = loop.create_future()
    topics = set()

    def finish(result):
        if not done.done():
            done.set_result(result)

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            loop.call_soon_threadsafe(finish, _refusal(reason_code))
            return
        client.subscribe(topic or "#")
        loop.call_soon_threadsafe(finish, None)

    def on_message(_client, _userdata, message):
        topics.add(message.topic)

    client = _client(spec)
    client.on_connect, client.on_message = on_connect, on_message
    client.on_connect_fail = lambda _c, _u: loop.call_soon_threadsafe(finish, SourceError("mqtt_unreachable"))
    client.connect_async(spec["address"], spec.get("port") or 1883)
    client.loop_start()
    try:
        try:
            error = await asyncio.wait_for(done, CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise SourceError("mqtt_unreachable", "timeout")
        if error:
            raise error
        await asyncio.sleep(SAMPLE_SECONDS)
        return {**topic_overview(topics), "topic_count": len(topics)}
    finally:
        client.loop_stop()
        client.disconnect()
