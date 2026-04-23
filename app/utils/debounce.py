import time
import threading
from typing import Callable, Dict, Tuple


class Debouncer:
    """
    Gestionnaire de debounce pour éviter les notifications en rafale.
    Chaque règle a son propre timer.
    """

    _lock = threading.Lock()
    _timers: Dict[int, Tuple[threading.Timer, Callable]] = {}

    @classmethod
    def register(cls, rule_id: int, callback: Callable, delay_seconds: int):
        """
        Enregistre ou ré-enregistre un callback avec debounce.
        Si un timer existe déjà pour cette règle, il est reset.
        """
        with cls._lock:
            # Annule le timer existant s'il y en a un
            if rule_id in cls._timers:
                old_timer, _ = cls._timers[rule_id]
                old_timer.cancel()

            def wrapped():
                callback()
                with cls._lock:
                    cls._timers.pop(rule_id, None)

            timer = threading.Timer(delay_seconds, wrapped)
            cls._timers[rule_id] = (timer, callback)
            timer.start()
