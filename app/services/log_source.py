"""
Accès en lecture aux sources de logs d'une règle, quel que soit leur type :
fichier local, [WEBHOOK]:<token> (data/webhooks/<token>.log) ou [SYSLOG]:<host> (data/syslog/<host>.log).
"""
import os
from pathlib import Path
from typing import List, Optional

from app import logger

MAX_CONTEXT_LINE_CHARS = 500


def _data_dir() -> Path:
    return Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data"))


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "-_")


def source_type(log_file_path: str) -> str:
    if log_file_path.startswith("[WEBHOOK]:"):
        return "webhook"
    if log_file_path.startswith("[SYSLOG]:"):
        return "syslog"
    return "file"


def resolve_path(log_file_path: str) -> Path:
    """Chemin physique du fichier contenant les lignes de la règle."""
    kind = source_type(log_file_path)
    if kind == "webhook":
        return _data_dir() / "webhooks" / f"{_safe(log_file_path.split(':', 1)[1])}.log"
    if kind == "syslog":
        return _data_dir() / "syslog" / f"{_safe(log_file_path.split(':', 1)[1].strip())}.log"
    return Path(log_file_path)


def count_lines(path: Path, max_bytes: int = 64 * 1024 * 1024) -> int:
    """Nombre de lignes du fichier (lecture bornée ; 0 si illisible)."""
    try:
        n, read = 0, 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += chunk.count(b"\n")
                read += len(chunk)
                if read >= max_bytes:
                    break
        return n
    except OSError:
        return 0


def read_tail_lines(path: Path, max_bytes: int = 2 * 1024 * 1024) -> List[str]:
    """Lignes de la fin du fichier (au plus max_bytes lus). La première ligne partielle est ignorée."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()
        if start > 0 and lines:
            lines = lines[1:]
        return lines
    except OSError as e:
        logger.debug("LogSource", f"Lecture impossible de {path} : {e}")
        return []


def _shorten(line: str) -> str:
    line = line.rstrip()
    return line if len(line) <= MAX_CONTEXT_LINE_CHARS else line[:MAX_CONTEXT_LINE_CHARS] + "…"


def find_context(log_file_path: str, matched_lines: List[str], before: int, after: int,
                 max_between: int = 40) -> Optional[dict]:
    """
    Retrouve les lignes déclenchantes dans la source et renvoie le contexte qui les entoure :
    {"before": [...], "between": [...] | None, "after": [...]}.
    `between` contient toutes les lignes (déclenchantes ou non) entre la première et la dernière ligne
    déclenchante si l'écart est raisonnable ; sinon None (le bloc d'événements suffit).
    Renvoie None si les lignes ne sont pas retrouvées (rotation, source illisible…).
    """
    if not matched_lines or (before <= 0 and after <= 0):
        return None
    lines = read_tail_lines(resolve_path(log_file_path))
    if not lines:
        return None

    first_raw, last_raw = matched_lines[0].strip(), matched_lines[-1].strip()
    last_idx = next((i for i in range(len(lines) - 1, -1, -1) if last_raw and last_raw in lines[i]), None)
    if last_idx is None:
        return None
    first_idx = next((i for i in range(last_idx, -1, -1) if first_raw and first_raw in lines[i]), last_idx)

    between = None
    if last_idx > first_idx and last_idx - first_idx + 1 <= max_between:
        raws = [m.strip() for m in matched_lines if m.strip()]
        between = [("» " if any(r in l for r in raws) else "  ") + _shorten(l)
                   for l in lines[first_idx:last_idx + 1]]
    return {
        "before": [_shorten(l) for l in lines[max(0, first_idx - before):first_idx]],
        "between": between,
        "after": [_shorten(l) for l in lines[last_idx + 1:last_idx + 1 + after]],
    }
