"""Chien de garde de la boucle asyncio : si elle ne répond plus pendant plusieurs secondes (calcul ou entrée/sortie
synchrone dans une coroutine), l'application ne répond plus non plus. Un thread vérifie un battement entretenu par
la boucle et, en cas de blocage, écrit dans les logs l'endroit où la boucle est arrêtée (une fois par blocage)."""
import asyncio
import sys
import threading
import time
import traceback

from app import logger

BEAT_S = 0.5
THRESHOLD_S = 5.0


def start(threshold_s: float = THRESHOLD_S):
    """À appeler depuis la boucle (lifespan)."""
    state = {"beat": time.monotonic()}
    loop_thread = threading.get_ident()

    async def heartbeat():
        while True:
            state["beat"] = time.monotonic()
            await asyncio.sleep(BEAT_S)

    def watch():
        reported = False
        while True:
            time.sleep(1.0)
            lag = time.monotonic() - state["beat"]
            if lag > threshold_s and not reported:
                frame = sys._current_frames().get(loop_thread)
                stack = "".join(traceback.format_stack(frame)[-14:]) if frame else "(pile indisponible)"
                logger.warning("Watchdog", f"Boucle principale bloquée depuis {lag:.0f} s, ici :\n{stack}")
                reported = True
            elif lag < BEAT_S * 3:
                if reported:
                    logger.warning("Watchdog", "Boucle principale débloquée.")
                reported = False

    asyncio.get_running_loop().create_task(heartbeat())
    threading.Thread(target=watch, daemon=True, name="loop-watchdog").start()
