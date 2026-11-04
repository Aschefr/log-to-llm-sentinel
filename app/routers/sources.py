"""Connexions des sources que Sentinel va lire lui-même (Home Assistant, MQTT), leurs tests, leur état, leurs lignes.
Un secret enregistré n'est jamais renvoyé : un champ laissé vide le conserve."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import SessionLocal
from app.models import DiagnosticLink, Rule, SourceConnection
from app.services import connected_sources, diagnostics as diag, log_source, source_hass, source_mqtt
from app.services.source_errors import SourceError

router = APIRouter(prefix="/api/sources", tags=["sources"])
KINDS = ("homeassistant", "mqtt")


class ConnectionBody(BaseModel):
    name: str = ""
    kind: str
    address: str
    port: Optional[int] = None
    username: Optional[str] = ""
    secret: Optional[str] = ""
    use_tls: bool = False
    id: Optional[int] = None  # test d'une connexion existante : son secret enregistré sert si le champ est vide


def _public(conn: SourceConnection) -> dict:
    return {"id": conn.id, "name": conn.name, "kind": conn.kind, "address": conn.address, "port": conn.port,
            "username": conn.username or "", "use_tls": bool(conn.use_tls), "has_secret": bool(conn.secret)}


def _rules_using(db, conn: SourceConnection) -> list:
    rules = db.query(Rule).filter(Rule.log_file_path.like("[%")).all()
    return [r.name for r in rules if (parsed := log_source.parse_connected(r.log_file_path))
            and parsed["kind"] == conn.kind and parsed["connection_id"] == conn.id]


def _values(db, body: ConnectionBody, conn_id: Optional[int] = None) -> dict:
    if body.kind not in KINDS:
        raise HTTPException(status_code=422, detail="source_kind_invalid")
    name, address = body.name.strip(), body.address.strip()
    if not name:
        raise HTTPException(status_code=422, detail="source_name_required")
    if not address:
        raise HTTPException(status_code=422, detail="source_address_required")
    if any(c.name.lower() == name.lower() for c in db.query(SourceConnection).filter(SourceConnection.id != (conn_id or 0)).all()):
        raise HTTPException(status_code=422, detail="source_name_taken")
    port = body.port or (1883 if body.kind == "mqtt" else None)
    if port is not None and not 1 <= port <= 65535:
        raise HTTPException(status_code=422, detail="source_port_invalid")
    return {"name": name, "kind": body.kind, "address": address, "port": port, "username": (body.username or "").strip(),
            "use_tls": bool(body.use_tls)}


@router.get("/connections")
def list_connections():
    db = SessionLocal()
    try:
        rows = db.query(SourceConnection).order_by(SourceConnection.kind, SourceConnection.name).all()
        return {"connections": [{**_public(c), "rules": _rules_using(db, c)} for c in rows]}
    finally:
        db.close()


@router.post("/connections")
def add_connection(body: ConnectionBody):
    db = SessionLocal()
    try:
        conn = SourceConnection(**_values(db, body), secret=body.secret or "")
        db.add(conn)
        db.commit()
        return _public(conn)
    finally:
        db.close()


@router.put("/connections/{conn_id}")
def update_connection(conn_id: int, body: ConnectionBody):
    db = SessionLocal()
    try:
        conn = db.query(SourceConnection).filter(SourceConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(status_code=404, detail="source_connection_not_found")
        for key, value in _values(db, body, conn_id).items():
            setattr(conn, key, value)
        if body.secret:
            conn.secret = body.secret
        db.commit()
        return _public(conn)
    finally:
        db.close()


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: int):
    db = SessionLocal()
    try:
        conn = db.query(SourceConnection).filter(SourceConnection.id == conn_id).first()
        if not conn:
            raise HTTPException(status_code=404, detail="source_connection_not_found")
        used = _rules_using(db, conn)
        if used:
            raise HTTPException(status_code=409, detail="source_connection_used")
        db.delete(conn)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


def _failure(e: SourceError) -> dict:
    return {"ok": False, "code": e.code, "detail": e.detail}


@router.post("/connections/test")
async def test_connection(body: ConnectionBody, topic: Optional[str] = None):
    secret = body.secret or ""
    if not secret and body.id:
        db = SessionLocal()
        try:
            conn = db.query(SourceConnection).filter(SourceConnection.id == body.id).first()
            secret = conn.secret if conn else ""
        finally:
            db.close()
    try:
        if body.kind == "homeassistant":
            return {"ok": True, **await source_hass.test(body.address, secret)}
        if body.kind == "mqtt":
            spec = {"address": body.address.strip(), "port": body.port or 1883, "username": (body.username or "").strip(),
                    "secret": secret, "use_tls": body.use_tls}
            return {"ok": True, **await source_mqtt.test(spec, topic)}
    except SourceError as e:
        return _failure(e)
    raise HTTPException(status_code=422, detail="source_kind_invalid")


@router.get("/docker/containers")
def docker_containers(link_id: int):
    db = SessionLocal()
    try:
        link = db.query(DiagnosticLink).filter(DiagnosticLink.id == link_id, DiagnosticLink.kind == "docker").first()
    finally:
        db.close()
    if not link:
        raise HTTPException(status_code=404, detail="link_not_found")
    try:
        rows = diag.docker_containers(link.docker_url)
    except diag.DiagnosticError as e:
        return {"ok": False, "detail": str(e), "containers": []}
    return {"ok": True, "containers": sorted(({"name": c.get("name"), "state": c.get("state")} for c in rows), key=lambda c: c["name"] or "")}


@router.get("/status/{rule_id}")
def rule_status(rule_id: int):
    return {"status": connected_sources.manager.status(rule_id)}


@router.get("/tail/{rule_id}")
def tail(rule_id: int, lines: int = Query(60, ge=1, le=2000), keywords: Optional[str] = None):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
    finally:
        db.close()
    if not rule or not log_source.parse_connected(rule.log_file_path):
        raise HTTPException(status_code=404, detail="rule_not_found")
    wanted = [k.strip().lower() for k in (keywords or "").split(",") if k.strip()]
    out = []
    for text in log_source.read_tail_lines(log_source.resolve_path(rule.log_file_path))[-lines:]:
        hits = [k for k in wanted if k in text.lower()]
        out.append({"text": text, "matched": bool(hits), "matched_keywords": hits})
    return {"lines": out}
