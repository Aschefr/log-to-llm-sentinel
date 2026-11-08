"""Fichiers de Sentinel que l'interface ne doit jamais lire : sa clé SSH de diagnostic (tout le dossier ssh/) et ses
bases de données.

L'interface n'a pas d'authentification : quiconque l'atteint sur le réseau pouvait lire la clé privée par l'explorateur
de fichiers (/api/files/tail), ou par une règle « fichier local » qui la désigne (constaté le 2026-11-08). Chaque lecture
d'un fichier choisi depuis l'interface passe donc par is_protected().
"""
import os
import re
from pathlib import Path
from typing import Union

_DB_FILE = re.compile(r"\.(db|sqlite3?)(-wal|-shm|-journal)?$", re.I)

PROTECTED_DETAIL = "Ce fichier appartient à Sentinel (clé SSH ou base de données) : il ne peut être ni lu ni surveillé."


def _ssh_dir() -> Path:
    return (Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "ssh").resolve()


def is_protected(path: Union[str, Path]) -> bool:
    """True pour le dossier ssh/ de Sentinel et ce qu'il contient, et pour toute base SQLite (sentinel.db, ses fichiers
    -wal/-shm, ses copies sentinel.db.*). Le chemin est résolu : un lien ou un « .. » ne contourne pas le contrôle."""
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return True
    ssh = _ssh_dir()
    if p == ssh or ssh in p.parents:
        return True
    name = p.name.lower()
    return bool(_DB_FILE.search(name)) or name.startswith("sentinel.db")
