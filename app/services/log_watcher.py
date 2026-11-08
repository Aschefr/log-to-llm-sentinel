import asyncio
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from app.database import SessionLocal
from app.models import Rule
from app import logger
from app.utils.protected_paths import is_protected

# Protects UI rendering + LLM context from oversized lines (e.g. Nextcloud JSON dumps on restart)
MAX_LINE_LENGTH = 10_000
READ_CHUNK_BYTES = 1024 * 1024
POLL_SECONDS = 1.0
# Un programme écrit souvent sa ligne en plusieurs fois : on attend la fin de la ligne, sans bloquer sur une dernière
# ligne qui ne se termine jamais.
PARTIAL_LINE_WAIT_SECONDS = 5.0


def _clean_line(raw: bytes, filepath: str) -> str:
    line = raw.decode("utf-8", errors="ignore").strip()
    if len(line) > MAX_LINE_LENGTH:
        logger.warning("LogWatcher", f"Ligne surdimensionnée ({len(line)} chars) dans {filepath}, tronquée")
        return line[:MAX_LINE_LENGTH] + f" …[TRONQUÉ : {len(line)} chars]"
    return line


def read_lines(filepath: str, position: int, flush_partial: bool = False, skipping: bool = False) -> Tuple[List[str], int, bool, bool]:
    """Lit depuis position. Retourne (lignes complètes, nouvelle position, reste une ligne incomplète, ligne géante en cours).

    Les positions sont des octets : une ligne n'est jamais coupée, même au milieu d'un caractère. Une ligne plus longue
    que le morceau lu ne bloque pas la lecture : son début est gardé, son reste ignoré."""
    if is_protected(filepath):
        return [], position, False, skipping  # clé SSH ou base de Sentinel : jamais surveillée
    with open(filepath, "rb") as f:
        f.seek(position)
        chunk = f.read(READ_CHUNK_BYTES)
    if not chunk:
        return [], position, False, skipping
    end = chunk.rfind(b"\n") + 1
    if skipping:
        first = chunk.find(b"\n") + 1
        if not first:
            return [], position + len(chunk), False, True
        lines, new_position, partial, giant = read_lines(filepath, position + first, flush_partial)
        return lines, new_position, partial, giant
    giant = False
    if not end:
        giant = len(chunk) == READ_CHUNK_BYTES
        if not giant and not flush_partial:
            return [], position, True, False
        end = len(chunk)
    lines = [_clean_line(raw, filepath) for raw in chunk[:end].split(b"\n")]
    return [line for line in lines if line], position + end, end < len(chunk), giant


class LogWatcher:
    """
    Watcher asynchrone qui surveille les fichiers de log définis dans les règles.
    Détecte les nouvelles lignes et les envoie au callback.
    """

    def __init__(self, on_new_lines: Optional[Callable] = None):
        self.on_new_lines = on_new_lines
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._file_inodes: Dict[int, int] = {}  # rule_id -> last known inode
        self._paths: Dict[int, str] = {}  # rule_id -> fichier surveillé

    async def start(self):
        """Démarre la surveillance de tous les fichiers de log."""
        self._running = True
        await self._safe_reload()
        if not self._tasks:
            logger.info("LogWatcher", "Aucune règle active. Attente...")

        # Task principale qui garde le watcher vivant
        while self._running:
            await asyncio.sleep(5)
            # Recharger les règles périodiquement
            await self._safe_reload()

    async def _safe_reload(self):
        # Une base occupée un instant ne doit pas arrêter la surveillance pour de bon
        try:
            await self._reload_rules()
        except Exception as e:
            logger.error("LogWatcher", f"Rechargement des règles : {e}")

    async def _reload_rules(self):
        """Recharge les règles et ajuste les tâches de surveillance."""
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.enabled == True).all()
            paths = {r.id: r.log_file_path for r in rules}

            # Arrêter les watchers des règles supprimées, désactivées ou dont le fichier a changé, oublier ceux qui sont morts
            tasks_to_keep = []
            for t in self._tasks:
                rule_id = int(t.get_name().split("_")[1])
                if t.done():
                    logger.warning("LogWatcher", f"Watcher de la règle {rule_id} arrêté, relance")
                elif paths.get(rule_id) == self._paths.get(rule_id):
                    tasks_to_keep.append(t)
                else:
                    logger.info("LogWatcher", f"Arrêt du watcher pour la règle ID: {rule_id}")
                    t.cancel()
            self._tasks = tasks_to_keep

            # Démarrer de nouveaux watchers si besoin
            watched_rule_ids = {int(t.get_name().split("_")[1]) for t in self._tasks}
            for rule in rules:
                if rule.id not in watched_rule_ids:
                    self._paths[rule.id] = rule.log_file_path
                    self._tasks.append(asyncio.create_task(self._watch_file(rule), name=f"rule_{rule.id}"))
                    logger.info("LogWatcher", f"Surveillance de {rule.log_file_path} (règle: {rule.name})")
        finally:
            db.close()

    def _get_file_inode(self, filepath: str) -> int:
        """Retourne l'inode du fichier (0 si indisponible)."""
        try:
            return os.stat(filepath).st_ino
        except OSError:
            return 0

    async def _send(self, rule: Rule, lines: List[str]):
        result = self.on_new_lines(rule, lines)
        if asyncio.iscoroutine(result):
            await result

    async def _watch_file(self, rule: Rule):
        """Surveille un fichier de log spécifique."""
        filepath = rule.log_file_path
        rule_id = rule.id

        # Récupérer la dernière position
        db = SessionLocal()
        try:
            db_rule = db.query(Rule).filter(Rule.id == rule_id).first()
            position = int(db_rule.last_position or 0) if db_rule else 0
        finally:
            db.close()

        # Stocker l'inode initial (si le fichier existe déjà)
        self._file_inodes.pop(rule_id, None)
        if os.path.exists(filepath):
            self._file_inodes[rule_id] = self._get_file_inode(filepath)

        partial_since = None
        skipping = False
        last_error = None
        while self._running:
            try:
                if not os.path.exists(filepath):
                    await asyncio.sleep(POLL_SECONDS * 2)
                    continue

                file_size = os.path.getsize(filepath)
                current_inode = self._get_file_inode(filepath)
                known_inode = self._file_inodes.get(rule_id, 0)

                # Détecter recréation du fichier (inode changé = nouveau fichier)
                if known_inode != 0 and current_inode != 0 and current_inode != known_inode:
                    logger.info("LogWatcher", f"Fichier {filepath} recréé (inode {known_inode} → {current_inode}), remise à zéro de la position.")
                    position, partial_since, skipping = 0, None, False
                # Fallback : vérifier si le fichier a été tronqué
                elif file_size < position:
                    logger.info("LogWatcher", f"Fichier {filepath} tronqué, remise à zéro de la position.")
                    position, partial_since, skipping = 0, None, False

                # Mettre à jour l'inode connu
                self._file_inodes[rule_id] = current_inode

                waited = partial_since is not None and time.monotonic() - partial_since >= PARTIAL_LINE_WAIT_SECONDS
                new_lines, new_position, partial, skipping = read_lines(filepath, position, waited, skipping)
                partial_since = (partial_since if partial_since is not None and not new_lines and not waited else time.monotonic()) if partial else None

                if new_position != position:
                    if new_lines:
                        logger.debug("LogWatcher", f"{filepath} — {len(new_lines)} nouvelle(s) ligne(s) détectée(s)")
                    # Mettre à jour la position
                    db = SessionLocal()
                    try:
                        db_rule = db.query(Rule).filter(Rule.id == rule_id).first()
                        if db_rule:
                            db_rule.last_position = new_position
                            if new_lines:
                                db_rule.last_line_received_at = datetime.utcnow()
                                db_rule.inactivity_notified = False
                            db.commit()
                    finally:
                        db.close()

                    # Ces lignes sont consommées : une erreur de traitement ne doit ni les renvoyer en boucle, ni bloquer la suite
                    position = new_position
                    if new_lines and self.on_new_lines:
                        try:
                            await self._send(rule, new_lines)
                        except Exception as e:
                            logger.error("LogWatcher", f"Traitement de {len(new_lines)} ligne(s) de {filepath} : {e}")
                last_error = None

            except Exception as e:
                # Fichier illisible : prévenir une fois, pas chaque seconde
                if str(e) != last_error:
                    logger.error("LogWatcher", f"Erreur sur {filepath}: {e}")
                    last_error = str(e)

            await asyncio.sleep(POLL_SECONDS)

    def stop(self):
        """Arrête la surveillance."""
        self._running = False
        for task in self._tasks:
            task.cancel()
