"""
Logger centralisé pour Log-to-LLM-Sentinel.
Utilise debug() pour les logs verbeux (actifs uniquement si debug_mode = True en BDD).
Utilise info() / warning() / error() pour les logs toujours affichés.
"""
import sys
from datetime import datetime


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_debug_mode() -> bool:
    """Lit le debug_mode depuis la BDD sans créer de dépendance circulaire."""
    try:
        from app.database import SessionLocal
        from app.models import GlobalConfig
        db = SessionLocal()
        try:
            cfg = db.query(GlobalConfig).first()
            return bool(cfg and cfg.debug_mode)
        finally:
            db.close()
    except Exception:
        return False


LOG_BUFFER = []
MAX_LOGS = 100

OLLAMA_BUFFER = []
MAX_OLLAMA_LOGS = 20


def _add_to_buffer(level: str, tag: str, message: str):
    entry = {
        "timestamp": _now(),
        "level": level,
        "tag": tag,
        "message": message
    }
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > MAX_LOGS:
        LOG_BUFFER.pop(0)


def get_logs():
    return LOG_BUFFER


def clear_logs():
    global LOG_BUFFER
    LOG_BUFFER.clear()


def add_ollama_log(prompt: str, response: str):
    """Enregistre un appel Ollama (Prompt complet / Réponse tronquée) pour le débug."""
    if not _get_debug_mode():
        return
    entry = {
        "timestamp": _now(),
        "prompt": prompt,
        "response": response[:250] + "..." if len(response) > 250 else response
    }
    OLLAMA_BUFFER.append(entry)
    if len(OLLAMA_BUFFER) > MAX_OLLAMA_LOGS:
        OLLAMA_BUFFER.pop(0)


def get_ollama_logs():
    return OLLAMA_BUFFER


def clear_ollama_logs():
    global OLLAMA_BUFFER
    OLLAMA_BUFFER.clear()


def debug(tag: str, message: str) -> None:
    if _get_debug_mode():
        _add_to_buffer("DEBUG", tag, message)
        print(f"[{_now()}] [DEBUG] [{tag}] {message}", file=sys.stdout, flush=True)


def info(tag: str, message: str) -> None:
    _add_to_buffer("INFO", tag, message)
    print(f"[{_now()}] [INFO]  [{tag}] {message}", file=sys.stdout, flush=True)


def warning(tag: str, message: str) -> None:
    _add_to_buffer("WARN", tag, message)
    print(f"[{_now()}] [WARN]  [{tag}] {message}", file=sys.stdout, flush=True)


def error(tag: str, message: str) -> None:
    _add_to_buffer("ERROR", tag, message)
    print(f"[{_now()}] [ERROR] [{tag}] {message}", file=sys.stderr, flush=True)
