import asyncio
import os
from typing import Callable, List, Optional

from app.database import SessionLocal
from app.models import Rule


class LogWatcher:
    """
    Watcher asynchrone qui surveille les fichiers de log définis dans les règles.
    Détecte les nouvelles lignes et les envoie au callback.
    """

    def __init__(self, on_new_lines: Optional[Callable] = None):
        self.on_new_lines = on_new_lines
        self._running = False
        self._tasks: List[asyncio.Task] = []

    async def start(self):
        """Démarre la surveillance de tous les fichiers de log."""
        self._running = True
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.enabled == True).all()
        finally:
            db.close()

        for rule in rules:
            task = asyncio.create_task(self._watch_file(rule))
            self._tasks.append(task)
            print(f"[LogWatcher] Surveillance de {rule.log_file_path} (règle: {rule.name})")

        if not rules:
            print("[LogWatcher] Aucune règle active. Attente...")

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
            current_paths = {r.log_file_path for r in rules}

            # Arrêter les watchers pour les fichiers supprimés/désactivés
            self._tasks = [
                t for t in self._tasks
                if not t.done() or t.get_name() in current_paths
            ]

            # Démarrer de nouveaux watchers si besoin
            watched_paths = {t.get_name() for t in self._tasks if not t.done()}
            for rule in rules:
                if rule.log_file_path not in watched_paths:
                    task = asyncio.create_task(
                        self._watch_file(rule), name=rule.log_file_path
                    )
                    self._tasks.append(task)
                    print(f"[LogWatcher] Nouveau watcher: {rule.log_file_path}")
        finally:
            db.close()

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

        while self._running:
            try:
                if not os.path.exists(filepath):
                    await asyncio.sleep(2)
                    continue

                with open(filepath, "r", errors="ignore") as f:
                    f.seek(position)
                    new_lines = f.readlines()
                    new_position = f.tell()

                if new_lines:
                    # Mettre à jour la position
                    db = SessionLocal()
                    try:
                        db_rule = db.query(Rule).filter(Rule.id == rule.id).first()
                        if db_rule:
                            db_rule.last_position = new_position
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
                print(f"[LogWatcher] Erreur sur {filepath}: {e}")

            await asyncio.sleep(1)

    def stop(self):
        """Arrête la surveillance."""
        self._running = False
        for task in self._tasks:
            task.cancel()
