"""
Formats des expéditeurs de logs : API de réception de Loki et logs OpenTelemetry (OTLP/HTTP), en JSON ou en Protobuf.

Fluent Bit, Grafana Alloy, Promtail, Vector ou le collecteur OpenTelemetry deviennent ainsi des sources, sans dépendance
ajoutée : seuls quelques champs sont lus, le reste du message est ignoré.
"""
import base64
import gzip
import json
from typing import Iterator, List, Optional, Tuple

NAME_LABELS = ("container", "container_name", "service_name", "service", "app", "job", "host", "hostname")


class BadPayload(Exception):
    pass


# ── Compression ────────────────────────────────

def snappy_decompress(data: bytes) -> bytes:
    """Format bloc de Snappy : une longueur, puis des littéraux et des copies."""
    i, size, shift = 0, 0, 0
    while True:
        if i >= len(data):
            raise BadPayload("snappy")
        byte = data[i]
        i += 1
        size |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    out = bytearray()
    while i < len(data):
        tag = data[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                extra = length - 59
                length = int.from_bytes(data[i:i + extra], "little")
                i += extra
            length += 1
            out += data[i:i + length]
            i += length
            continue
        if kind == 1:
            length, offset = 4 + ((tag >> 2) & 7), ((tag >> 5) << 8) | data[i]
            i += 1
        elif kind == 2:
            length, offset = (tag >> 2) + 1, int.from_bytes(data[i:i + 2], "little")
            i += 2
        else:
            length, offset = (tag >> 2) + 1, int.from_bytes(data[i:i + 4], "little")
            i += 4
        if offset == 0 or offset > len(out):
            raise BadPayload("snappy")
        for _ in range(length):  # les copies peuvent se chevaucher
            out.append(out[-offset])
    if len(out) != size:
        raise BadPayload("snappy")
    return bytes(out)


def decompress(body: bytes, encoding: str, content_type: str, loki: bool) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError) as e:
            raise BadPayload("gzip") from e
    # Le protocole Loki compresse toujours son Protobuf en Snappy, sans toujours le dire
    if encoding == "snappy" or (loki and "json" not in content_type.lower()):
        return snappy_decompress(body)
    return body


# ── Protobuf : juste ce qu'il faut pour marcher dans les messages ──

def _fields(data: bytes) -> Iterator[Tuple[int, int, object]]:
    i, end = 0, len(data)
    while i < end:
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = _varint(data, i)
            if i + length > end:
                raise BadPayload("protobuf")
            value, i = data[i:i + length], i + length
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise BadPayload("protobuf")
        yield number, wire, value


def _varint(data: bytes, i: int) -> Tuple[int, int]:
    value, shift = 0, 0
    while True:
        if i >= len(data) or shift > 63:
            raise BadPayload("protobuf")
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, i


def _text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, (bytes, bytearray)) else str(value)


# ── Loki ───────────────────────────────────────

def _labels_name(labels: dict) -> Optional[str]:
    for key in NAME_LABELS:
        if labels.get(key):
            return str(labels[key]).lstrip("/")
    return None


def _parse_loki_labels(text: str) -> dict:
    """Étiquettes du Protobuf Loki : `{job="a", host="b"}`."""
    out, i = {}, text.find("{") + 1
    while 0 < i < len(text):
        eq = text.find('="', i)
        if eq < 0:
            break
        key = text[i:eq].strip(" ,")
        j, value = eq + 2, []
        while j < len(text) and text[j] != '"':
            if text[j] == "\\" and j + 1 < len(text):
                j += 1
            value.append(text[j])
            j += 1
        out[key] = "".join(value)
        i = j + 1
    return out


def _prefixed(name: Optional[str], line: str) -> str:
    return f"[{name}] {line}" if name else line


def loki_lines(body: bytes, content_type: str, encoding: str) -> List[str]:
    data = decompress(body, encoding, content_type, loki=True)
    lines = []
    if "json" in (content_type or "").lower():
        try:
            streams = json.loads(data).get("streams") or []
        except (ValueError, AttributeError) as e:
            raise BadPayload("json") from e
        for stream in streams:
            name = _labels_name(stream.get("stream") or {})
            for value in stream.get("values") or []:
                if isinstance(value, list) and len(value) >= 2:
                    lines.append(_prefixed(name, str(value[1])))
        return lines
    for number, wire, stream in _fields(data):
        if number != 1 or wire != 2:
            continue
        name, entries = None, []
        for field, field_wire, value in _fields(stream):
            if field == 1 and field_wire == 2:
                name = _labels_name(_parse_loki_labels(_text(value)))
            elif field == 2 and field_wire == 2:
                entries.append(next((_text(v) for f, w, v in _fields(value) if f == 2 and w == 2), ""))
        lines += [_prefixed(name, entry) for entry in entries]
    return lines


# ── OpenTelemetry ──────────────────────────────

def _any_json(value: dict) -> str:
    if not isinstance(value, dict):
        return "" if value is None else str(value)
    if "stringValue" in value:
        return str(value["stringValue"])
    for key in ("intValue", "doubleValue", "boolValue"):
        if key in value:
            return str(value[key])
    if "kvlistValue" in value or "arrayValue" in value:
        return json.dumps(value.get("kvlistValue") or value.get("arrayValue"), ensure_ascii=False)
    if "bytesValue" in value:
        try:
            return base64.b64decode(value["bytesValue"]).decode("utf-8", errors="replace")
        except ValueError:
            return ""
    return ""


def _otel_line(service: Optional[str], severity: str, body: str) -> Optional[str]:
    if not body.strip():
        return None
    return " ".join(part for part in (severity, f"[{service}]" if service else "", body) if part)


def _otlp_json(data: bytes) -> List[str]:
    try:
        resources = json.loads(data).get("resourceLogs") or []
    except (ValueError, AttributeError) as e:
        raise BadPayload("json") from e
    lines = []
    for resource in resources:
        attributes = (resource.get("resource") or {}).get("attributes") or []
        service = next((_any_json(a.get("value")) for a in attributes if a.get("key") == "service.name"), None)
        for scope in resource.get("scopeLogs") or []:
            for record in scope.get("logRecords") or []:
                line = _otel_line(service, record.get("severityText") or "", _any_json(record.get("body")))
                if line:
                    lines.append(line)
    return lines


def _any_proto(data: bytes) -> str:
    """AnyValue : le texte (1), sinon une représentation lisible du reste."""
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2:
            return _text(value)
        if number == 3 and wire == 0:
            return str(value)
        if number in (5, 6) and wire == 2:  # tableau ou structure : les textes qu'ils contiennent
            return " ".join(_strings(value))
        if number == 7 and wire == 2:
            return _text(value)
    return ""


def _strings(data: bytes) -> List[str]:
    out = []
    try:
        for _number, wire, value in _fields(data):
            if wire == 2:
                inner = _strings(value) if value else []
                out += inner or [_text(value)]
    except BadPayload:
        return [_text(data)]
    return out


def _otlp_proto(data: bytes) -> List[str]:
    lines = []
    for number, wire, resource_logs in _fields(data):
        if number != 1 or wire != 2:
            continue
        service, scopes = None, []
        for field, field_wire, value in _fields(resource_logs):
            if field == 1 and field_wire == 2:  # Resource : ses attributs
                for attr_field, attr_wire, attribute in _fields(value):
                    if attr_field == 1 and attr_wire == 2:
                        pairs = {f: v for f, w, v in _fields(attribute) if w == 2}
                        if _text(pairs.get(1, b"")) == "service.name":
                            service = _any_proto(pairs.get(2, b""))
            elif field == 2 and field_wire == 2:
                scopes.append(value)
        for scope in scopes:
            for field, field_wire, record in _fields(scope):
                if field != 2 or field_wire != 2:
                    continue
                pairs = {f: v for f, w, v in _fields(record) if w == 2}
                line = _otel_line(service, _text(pairs.get(3, b"")), _any_proto(pairs.get(5, b"")))
                if line:
                    lines.append(line)
    return lines


def otlp_lines(body: bytes, content_type: str, encoding: str) -> List[str]:
    data = decompress(body, encoding, content_type, loki=False)
    return _otlp_json(data) if "json" in (content_type or "").lower() else _otlp_proto(data)
