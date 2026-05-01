import asyncio
import os
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.database import SessionLocal
from app.models import Rule
from app import logger

# Protects UI rendering + LLM context from oversized lines (e.g. Nextcloud JSON dumps on restart)
MAX_LINE_LENGTH = 10_000


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

    async def start(self):
        """Démarre la surveillance de tous les fichiers de log."""
        self._running = True
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.enabled == True).all()
        finally:
            db.close()

        for rule in rules:
            task = asyncio.create_task(self._watch_file(rule), name=f"rule_{rule.id}")
            self._tasks.append(task)
            logger.info("LogWatcher", f"Surveillance de {rule.log_file_path} (règle: {rule.name})")

        if not rules:
            logger.info("LogWatcher", "Aucune règle active. Attente...")

        # Task principale qui garde le watcher vivant
        while self._running:
            await asyncio.sleep(5)
            # Recharger les règles périodiquement
            await self._reload_rules()

    async def _reload_rules(self):
        """Recharge les règles et ajuste les tâches de surveillance."""
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.enabled == True).all()
            current_rule_ids = {r.id for r in rules}

            # Arrêter les watchers pour les règles supprimées/désactivées
            tasks_to_keep = []
            for t in self._tasks:
                task_name = t.get_name()
                if task_name.startswith("rule_"):
                    rule_id = int(task_name.split("_")[1])
                    if rule_id in current_rule_ids:
                        tasks_to_keep.append(t)
                        continue
                logger.info("LogWatcher", f"Arrêt du watcher pour la règle ID: {task_name}")
                t.cancel()
            self._tasks = tasks_to_keep

            # Démarrer de nouveaux watchers si besoin
            watched_rule_ids = {int(t.get_name().split("_")[1]) for t in self._tasks if t.get_name().startswith("rule_")}
            for rule in rules:
                if rule.id not in watched_rule_ids:
                    task = asyncio.create_task(
                        self._watch_file(rule), name=f"rule_{rule.id}"
                    )
                    self._tasks.append(task)
                    logger.info("LogWatcher", f"Nouveau watcher: {rule.log_file_path} (Règle ID: {rule.id})")
        finally:
            db.close()

    def _get_file_inode(self, filepath: str) -> int:
        """Retourne l'inode du fichier (0 si indisponible)."""
        try:
            return os.stat(filepath).st_ino
        except OSError:
            return 0

    async def _watch_file(self, rule: Rule):
        """Surveille un fichier de log spécifique."""
        filepath = rule.log_file_path

        # Récupérer la dernière position
        db = SessionLocal()
        try:
            db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
            position = db_rule.last_position if db_rule else 0
        finally:
            db.close()

        # Stocker l'inode initial (si le fichier existe déjà)
        if os.path.exists(filepath):
            self._file_inodes[rule.id] = self._get_file_inode(filepath)

        while self._running:
            try:
                if not os.path.exists(filepath):
                    await asyncio.sleep(2)
                    continue

                file_size = os.path.getsize(filepath)
                current_inode = self._get_file_inode(filepath)
                known_inode = self._file_inodes.get(rule.id, 0)

                # Détecter recréation du fichier (inode changé = nouveau fichier)
                if known_inode != 0 and current_inode != 0 and current_inode != known_inode:
                    logger.info("LogWatcher", f"Fichier {filepath} recréé (inode {known_inode} → {current_inode}), remise à zéro de la position.")
                    position = 0
                    self._file_inodes[rule.id] = current_inode

                # Fallback : vérifier si le fichier a été tronqué
                if file_size < position:
                    logger.info("LogWatcher", f"Fichier {filepath} tronqué, remise à zéro de la position.")
                    position = 0

                # Mettre à jour l'inode connu
                self._file_inodes[rule.id] = current_inode

                with open(filepath, "r", errors="ignore") as f:
                    f.seek(position)
                    # Lire au plus 1 Mo à la fois pour éviter de saturer la mémoire
                    chunk = f.read(1024 * 1024)
                    new_position = f.tell()

                if chunk:
                    # Diviser en lignes
                    new_lines = chunk.splitlines()
                    
                    # Si le chunk s'arrête au milieu d'une ligne, on recule pour la lire entière au prochain tour
                    # Sauf si on est à la fin du fichier
                    if not chunk.endswith(('\n', '\r')) and new_position < file_size:
                        last_line_len = len(new_lines[-1].encode('utf-8', errors='ignore'))
                        new_position -= last_line_len
                        new_lines = new_lines[:-1]

                    # Tronquer les lignes surdimensionnées (BUG-01b)
                    sanitized_lines = []
                    for l in new_lines:
                        if len(l) > MAX_LINE_LENGTH:
                            logger.warning("LogWatcher", f"Ligne surdimensionnée ({len(l)} chars) dans {filepath}, troncature à {MAX_LINE_LENGTH}")
                            sanitized_lines.append(l[:MAX_LINE_LENGTH] + f" …[TRONQUÉ : {len(l)} chars]")
                        else:
                            sanitized_lines.append(l)
                    new_lines = sanitized_lines

                    if new_lines:
                        logger.debug("LogWatcher", f"{filepath} — {len(new_lines)} nouvelle(s) ligne(s) détectée(s)")
                    # Mettre à jour la position
                    db = SessionLocal()
                    try:
                        db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
                        if db_rule:
                            db_rule.last_position = new_position
                            if new_lines:
                                db_rule.last_line_received_at = datetime.utcnow()
                                db_rule.inactivity_notified = False
                            db.commit()
                    finally:
                        db.close()

                    # Envoyer au callback
                    if self.on_new_lines:
                        if asyncio.iscoroutinefunction(self.on_new_lines):
                            await self.on_new_lines(rule, [line.strip() for line in new_lines])
                        else:
                            self.on_new_lines(rule, [line.strip() for line in new_lines])

                    # Mettre à jour la position locale pour le prochain tour de boucle
                    position = new_position

            except Exception as e:
                logger.error("LogWatcher", f"Erreur sur {filepath}: {e}")

            await asyncio.sleep(1)

    def stop(self):
        """Arrête la surveillance."""
        self._running = False
        for task in self._tasks:
            task.cancel()
