"""
BackgroundTaskManager — Décorrélation des tâches Ollama des connexions HTTP.

Ce module permet de lancer des coroutines Ollama en arrière-plan, indépendamment
du cycle de vie de la connexion HTTP du client. Les clients peuvent se déconnecter
et se reconnecter sans interrompre la génération.

Types de tâches :
  - AnalysisTask : retry, analyse manuelle. Résultat = analysis_id en BDD.
  - ChatTask     : génération de chat. Les tokens sont buffurisés pour permettre
                   la reconnexion SSE transparente.

Usage (AnalysisTask) :
    task_id = task_manager.create_analysis_task()
    asyncio.create_task(my_analysis_coro(task_manager.get_task(task_id)))
    # retourner task_id au client, qui pollingera GET /task/{task_id}

Usage (ChatTask) :
    task_id, entry = task_manager.create_chat_task(conv_id)
    asyncio.create_task(my_chat_generator(entry))
    # retourner task_id au client, qui se connectera à GET /stream/{task_id}
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional


@dataclass
class AnalysisTaskEntry:
    """Entrée pour une tâche d'analyse (retry ou manuelle)."""
    task_id: str
    status: str = "running"          # "running" | "done" | "error"
    analysis_id: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ChatTaskEntry:
    """Entrée pour une tâche de chat. Les tokens sont accumulés dans token_buffer."""
    task_id: str
    conv_id: int
    status: str = "running"          # "running" | "done" | "error"
    token_buffer: list = field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    # Événement signalé à chaque nouveau token (pour réveiller les lecteurs SSE)
    _new_token: asyncio.Event = field(default_factory=asyncio.Event)


class BackgroundTaskManager:
    """
    Gestionnaire singleton de tâches Ollama en arrière-plan.
    Thread-safe via asyncio (pas de threads OS).
    """

    def __init__(self):
        self._analysis_tasks: dict[str, AnalysisTaskEntry] = {}
        self._chat_tasks: dict[str, ChatTaskEntry] = {}
        # Index conv_id → task_id pour retrouver rapidement les tâches chat actives
        self._chat_by_conv: dict[int, str] = {}

    # ── Analysis Tasks ─────────────────────────────────────────────────────────

    def create_analysis_task(self) -> "AnalysisTaskEntry":
        """Crée et enregistre une entrée de tâche d'analyse. Retourne l'entrée."""
        task_id = uuid.uuid4().hex[:10]
        entry = AnalysisTaskEntry(task_id=task_id)
        self._analysis_tasks[task_id] = entry
        return entry

    def get_analysis_task(self, task_id: str) -> Optional[AnalysisTaskEntry]:
        return self._analysis_tasks.get(task_id)

    # ── Chat Tasks ─────────────────────────────────────────────────────────────

    def create_chat_task(self, conv_id: int) -> "ChatTaskEntry":
        """
        Crée une entrée de tâche chat pour une conversation.
        Si une tâche est déjà en cours pour ce conv_id, elle est remplacée.
        """
        task_id = uuid.uuid4().hex[:10]
        entry = ChatTaskEntry(task_id=task_id, conv_id=conv_id)
        self._chat_tasks[task_id] = entry
        self._chat_by_conv[conv_id] = task_id
        return entry

    def get_chat_task(self, task_id: str) -> Optional[ChatTaskEntry]:
        return self._chat_tasks.get(task_id)

    def get_pending_chat_for_conv(self, conv_id: int) -> Optional[ChatTaskEntry]:
        """Retourne la tâche chat en cours pour une conversation, si elle existe."""
        task_id = self._chat_by_conv.get(conv_id)
        if not task_id:
            return None
        entry = self._chat_tasks.get(task_id)
        # On retourne uniquement si elle est encore en cours
        if entry and entry.status == "running":
            return entry
        # Nettoyer l'index si terminé
        if entry and entry.status != "running":
            self._chat_by_conv.pop(conv_id, None)
        return None

    def append_chat_token(self, entry: ChatTaskEntry, token: str):
        """Ajoute un token au buffer et signale les lecteurs SSE en attente."""
        entry.token_buffer.append(token)
        # Signaler qu'un nouveau token est disponible
        entry._new_token.set()
        entry._new_token.clear()

    async def wait_for_token(self, entry: ChatTaskEntry, timeout: float = 5.0) -> bool:
        """
        Attend qu'un nouveau token soit disponible ou que la tâche soit terminée.
        Retourne True si un token est arrivé, False si timeout.
        """
        try:
            await asyncio.wait_for(asyncio.shield(entry._new_token.wait()), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def cleanup_old_tasks(self, max_age_hours: int = 2) -> int:
        """
        Supprime les tâches terminées (done/error) vieilles de plus de max_age_hours.
        Retourne le nombre de tâches supprimées.
        """
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        count = 0

        for tid in list(self._analysis_tasks.keys()):
            entry = self._analysis_tasks[tid]
            if entry.status in ("done", "error") and entry.created_at < cutoff:
                del self._analysis_tasks[tid]
                count += 1

        for tid in list(self._chat_tasks.keys()):
            entry = self._chat_tasks[tid]
            if entry.status in ("done", "error") and entry.created_at < cutoff:
                del self._chat_tasks[tid]
                count += 1

        return count


# ── Singleton global ────────────────────────────────────────────────────────
task_manager = BackgroundTaskManager()
