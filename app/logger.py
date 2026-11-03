"""
Logger centralisé pour Log-to-LLM-Sentinel.
Utilise debug() pour les logs verbeux (actifs uniquement si debug_mode = True en BDD).
Utilise info() / warning() / error() pour les logs toujours affichés.

Tout part sur la sortie standard (docker logs) et dans data/logs/sentinel.log, qui survit aux redémarrages.
"""
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
_DEBUG_TTL_SECONDS = 10

_file_logger = None
_file_unavailable = False
_debug_cache = {"value": False, "until": 0.0}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_path() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "logs" / "sentinel.log"


def log_files() -> list:
    """Le journal et ses anciens fichiers, le plus récent d'abord."""
    current = log_path()
    candidates = [current] + [current.with_name(f"{current.name}.{i}") for i in range(1, LOG_BACKUPS + 1)]
    return [p for p in candidates if p.is_file()]


def log_size() -> int:
    total = 0
    for path in log_files():
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _write_file(line: str):
    """Un disque plein ou un dossier en lecture seule ne doit jamais empêcher Sentinel de tourner."""
    global _file_logger, _file_unavailable
    if _file_unavailable:
        return
    if _file_logger is None:
        try:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            file_logger = logging.getLogger("sentinel.file")
            file_logger.handlers.clear()
            file_logger.propagate = False
            file_logger.setLevel(logging.INFO)
            file_logger.addHandler(handler)
            _file_logger = file_logger
        except OSError as e:
            _file_unavailable = True
            print(f"[{_now()}] [WARN]  [Logger] Journal {log_path()} indisponible : {e}", file=sys.stderr, flush=True)
            return
    _file_logger.info(line)


def _get_debug_mode() -> bool:
    """Lit le debug_mode depuis la BDD sans créer de dépendance circulaire, au plus toutes les 10 secondes :
    debug() est appelé partout, y compris mode désactivé."""
    now = time.monotonic()
    if now < _debug_cache["until"]:
        return _debug_cache["value"]
    value = False
    try:
        from app.database import SessionLocal
        from app.models import GlobalConfig
        db = SessionLocal()
        try:
            cfg = db.query(GlobalConfig).first()
            value = bool(cfg and cfg.debug_mode)
        finally:
            db.close()
    except Exception:
        value = False
    _debug_cache.update(value=value, until=now + _DEBUG_TTL_SECONDS)
    return value


def refresh_debug_mode():
    """À appeler quand le réglage change, pour qu'il s'applique aussitôt."""
    _debug_cache["until"] = 0.0


def add_ollama_log(prompt: str, response: str, detection_id: str = None):
    """Mode debug : garde un appel Ollama complet dans le journal, pas sur la sortie standard (trop volumineux)."""
    if not _get_debug_mode():
        return
    _write_file(f"[{_now()}] [DEBUG] [OllamaCall] {detection_id or '-'}\n--- Prompt ---\n{prompt}\n--- Réponse ---\n{response}\n--- Fin ---")


def _emit(level: str, tag: str, message: str, stream):
    line = f"[{_now()}] [{level.ljust(5)}] [{tag}] {message}"
    print(line, file=stream, flush=True)
    _write_file(line)


def debug(tag: str, message: str) -> None:
    if _get_debug_mode():
        _emit("DEBUG", tag, message, sys.stdout)


def info(tag: str, message: str) -> None:
    _emit("INFO", tag, message, sys.stdout)


def warning(tag: str, message: str) -> None:
    _emit("WARN", tag, message, sys.stdout)


def error(tag: str, message: str) -> None:
    _emit("ERROR", tag, message, sys.stderr)
