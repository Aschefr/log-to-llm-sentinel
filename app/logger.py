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


def debug(tag: str, message: str) -> None:
    if _get_debug_mode():
        print(f"[{_now()}] [DEBUG] [{tag}] {message}", file=sys.stdout, flush=True)


def info(tag: str, message: str) -> None:
    print(f"[{_now()}] [INFO]  [{tag}] {message}", file=sys.stdout, flush=True)


def warning(tag: str, message: str) -> None:
    print(f"[{_now()}] [WARN]  [{tag}] {message}", file=sys.stdout, flush=True)


def error(tag: str, message: str) -> None:
    print(f"[{_now()}] [ERROR] [{tag}] {message}", file=sys.stderr, flush=True)
