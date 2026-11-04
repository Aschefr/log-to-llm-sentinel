"""
Sources que Sentinel va lire lui-même : logs Docker, Home Assistant, MQTT.

Chaque règle a sa tâche, qui se reconnecte seule et ne s'arrête qu'avec la règle. Les lignes suivent ensuite le même
chemin que les autres sources : un fichier (aperçu, apprentissage, contexte), puis l'orchestrateur.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional

from app import logger
from app.database import SessionLocal
from app.models import DiagnosticLink, Rule, SourceConnection
from app.services import log_source, source_docker, source_hass, source_mqtt
from app.services.source_errors import SourceError

SYNC_SECONDS = 5.0
RETRY_MIN_SECONDS = 2.0
RETRY_MAX_SECONDS = 60.0
HEALTHY_AFTER_SECONDS = 60.0
MAX_LINE_CHARS = 10_000


def clean_lines(lines: List[str]) -> List[str]:
    out = []
    for line in lines:
        line = line.strip()
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + f" …[TRONQUÉ : {len(line)} chars]"
        if line:
            out.append(line)
    return out


def _save(rule_id: int, path, stamped: List[str], checkpoint: Optional[float]):
    if stamped:
        log_source.append_lines(path, stamped)
    values = {}
    if stamped:
        values = {Rule.last_line_received_at: datetime.utcnow(), Rule.inactivity_notified: False}
    if checkpoint is not None:
        values[Rule.last_position] = checkpoint
    if values:
        db = SessionLocal()
        try:
            db.query(Rule).filter(Rule.id == rule_id).update(values, synchronize_session=False)
            db.commit()
        finally:
            db.close()


class Sink:
    """Ce qu'un connecteur voit de Sentinel : où remettre ses lignes, où signaler son état."""

    def __init__(self, manager: "SourceManager", rule_id: int, log_path, checkpoint: float):
        self.manager, self.rule_id, self.log_path, self.checkpoint = manager, rule_id, log_path, checkpoint

    def status(self, state: str, code: Optional[str] = None, detail: Optional[str] = None):
        self.manager.set_status(self.rule_id, state, code, detail)

    async def emit(self, lines: List[str], checkpoint: Optional[float] = None):
        lines = clean_lines(lines)
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if checkpoint is not None:
            self.checkpoint = checkpoint
        try:
            await asyncio.to_thread(_save, self.rule_id, self.log_path, [f"{stamp}  {line}" for line in lines], checkpoint)
        except Exception as e:
            logger.warning("Sources", f"Enregistrement des lignes de la règle {self.rule_id} : {e}")
        if lines and self.manager.on_new_lines:
            try:
                await self.manager.on_new_lines(SimpleNamespace(id=self.rule_id), lines)
            except Exception as e:
                logger.error("Sources", f"Traitement de {len(lines)} ligne(s) de la règle {self.rule_id} : {e}")


def _spec(db, rule: Rule) -> Optional[dict]:
    """Ce qu'il faut pour lire la source de cette règle. Sa valeur change si la connexion change : la tâche repart."""
    parsed = log_source.parse_connected(rule.log_file_path)
    if not parsed:
        return None
    if parsed["kind"] == "docker":
        link = db.query(DiagnosticLink).filter(DiagnosticLink.id == parsed["connection_id"], DiagnosticLink.kind == "docker").first()
        return {**parsed, "missing": not link, "url": link.docker_url if link else ""}
    conn = db.query(SourceConnection).filter(SourceConnection.id == parsed["connection_id"], SourceConnection.kind == parsed["kind"]).first()
    if not conn:
        return {**parsed, "missing": True}
    return {**parsed, "missing": False, "address": conn.address, "port": conn.port, "username": conn.username,
            "secret": conn.secret, "use_tls": bool(conn.use_tls)}


RUNNERS = {"docker": source_docker.run, "homeassistant": source_hass.run, "mqtt": source_mqtt.run}


class SourceManager:
    def __init__(self, on_new_lines: Optional[Callable] = None):
        self.on_new_lines = on_new_lines
        self._running = False
        self._tasks: Dict[int, asyncio.Task] = {}
        self._specs: Dict[int, dict] = {}
        self._status: Dict[int, dict] = {}

    def set_status(self, rule_id: int, state: str, code: Optional[str] = None, detail: Optional[str] = None):
        previous = self._status.get(rule_id) or {}
        if previous.get("state") == state and previous.get("code") == code and previous.get("detail") == detail:
            return
        self._status[rule_id] = {"state": state, "code": code, "detail": detail, "since": datetime.utcnow().isoformat() + "Z"}
        if state == "error" and previous.get("code") != code:
            logger.warning("Sources", f"Règle {rule_id} : {code}{f' ({detail})' if detail else ''}")

    def status(self, rule_id: int) -> Optional[dict]:
        return self._status.get(rule_id)

    async def start(self):
        self._running = True
        while self._running:
            try:
                self.sync()
            except Exception as e:
                logger.error("Sources", f"Rechargement des règles : {e}")
            await asyncio.sleep(SYNC_SECONDS)

    def sync(self):
        db = SessionLocal()
        try:
            wanted = {}
            for rule in db.query(Rule).filter(Rule.enabled == True).all():
                spec = _spec(db, rule)
                if spec:
                    wanted[rule.id] = (spec, rule.log_file_path, rule.last_position or 0.0)
        finally:
            db.close()
        for rule_id in list(self._tasks):
            task = self._tasks[rule_id]
            if task.done() or rule_id not in wanted or wanted[rule_id][0] != self._specs.get(rule_id):
                task.cancel()
                del self._tasks[rule_id]
                self._specs.pop(rule_id, None)
                if rule_id not in wanted:
                    self._status.pop(rule_id, None)
        for rule_id, (spec, path, checkpoint) in wanted.items():
            if rule_id not in self._tasks:
                self._specs[rule_id] = spec
                sink = Sink(self, rule_id, log_source.resolve_path(path), checkpoint)
                self._tasks[rule_id] = asyncio.create_task(self._keep_running(spec, sink), name=f"source_{rule_id}")

    async def _keep_running(self, spec: dict, sink: Sink):
        """Une source peut tomber autant de fois qu'elle veut : on y retourne, de moins en moins vite tant que ça échoue."""
        delay = RETRY_MIN_SECONDS
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            try:
                if spec.get("missing"):
                    raise SourceError("connection_missing", retry_seconds=30)
                sink.status("connecting")
                await RUNNERS[spec["kind"]](spec, sink)
                wait = RETRY_MIN_SECONDS
            except asyncio.CancelledError:
                raise
            except SourceError as e:
                sink.status(e.state, e.code, e.detail)
                wait = e.retry_seconds or delay
            except Exception as e:
                sink.status("error", "internal", type(e).__name__)
                logger.error("Sources", f"Règle {sink.rule_id} : {type(e).__name__} : {e}")
                wait = delay
            delay = RETRY_MIN_SECONDS if loop.time() - started >= HEALTHY_AFTER_SECONDS else min(delay * 2, RETRY_MAX_SECONDS)
            await asyncio.sleep(wait)

    def stop(self):
        self._running = False
        for task in self._tasks.values():
            task.cancel()


manager = SourceManager()
