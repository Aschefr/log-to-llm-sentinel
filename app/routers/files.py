from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/files", tags=["files"])


def _default_roots() -> List[Path]:
    # Roots that make sense in this app + docker-compose defaults.
    return [Path("/logs"), Path("/app/data"), Path("./data")]


def _get_roots() -> List[Path]:
    """
    Comma-separated list of allowed browse roots.
    Example: SENTINEL_BROWSE_ROOTS=/logs,/var/log/myapp
    """
    raw = os.environ.get("SENTINEL_BROWSE_ROOTS", "").strip()
    roots = _default_roots() if not raw else [Path(p.strip()) for p in raw.split(",") if p.strip()]

    resolved: List[Path] = []
    for r in roots:
        try:
            resolved.append(r.expanduser().resolve())
        except Exception:
            # Ignore invalid roots
            continue

    # Deduplicate while preserving order
    uniq: List[Path] = []
    seen = set()
    for r in resolved:
        key = str(r)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _is_under_root(target: Path, root: Path) -> bool:
    root_s = str(root)
    target_s = str(target)
    if target_s == root_s:
        return True
    return target_s.startswith(root_s + os.sep)


def _resolve_under_roots(user_path: str) -> Path:
    if not user_path:
        raise HTTPException(status_code=400, detail="path requis")

    try:
        target = Path(user_path).expanduser().resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="path invalide")

    for root in _get_roots():
        if _is_under_root(target, root):
            return target

    raise HTTPException(status_code=403, detail="path en dehors des volumes autorisés")


class BrowseEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    modified: Optional[float] = None
    readable: bool


class BrowseResponse(BaseModel):
    path: str
    parent: Optional[str] = None
    entries: List[BrowseEntry]


@router.get("/roots")
def get_roots():
    """Liste les racines autorisées pour la navigation (volumes)."""
    return {"roots": [str(p) for p in _get_roots()]}


@router.get("/browse", response_model=BrowseResponse)
def browse(
    path: str = Query(..., description="Dossier à lister (doit être dans une racine autorisée)"),
    show_hidden: bool = Query(False, description="Afficher les fichiers/dossiers cachés (débutant par .)"),
):
    target = _resolve_under_roots(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Chemin introuvable")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Le chemin doit être un dossier")

    entries: List[BrowseEntry] = []
    try:
        for child in target.iterdir():
            name = child.name
            if not show_hidden and name.startswith("."):
                continue

            try:
                is_dir = child.is_dir()
            except Exception:
                is_dir = False

            try:
                stat = child.stat()
                size = int(stat.st_size)
                modified = float(stat.st_mtime)
            except Exception:
                size = 0
                modified = None

            readable = os.access(str(child), os.R_OK)

            entries.append(
                BrowseEntry(
                    name=name,
                    path=str(child),
                    is_dir=is_dir,
                    size=size,
                    modified=modified,
                    readable=readable,
                )
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Accès refusé à ce dossier")

    # Sort: directories first, then files; alphabetic.
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    parent: Optional[str] = None
    try:
        if target.parent and str(target.parent) != str(target):
            # Only provide parent if it stays within allowed roots.
            p = target.parent.resolve()
            for root in _get_roots():
                if _is_under_root(p, root):
                    parent = str(p)
                    break
    except Exception:
        parent = None

    return BrowseResponse(path=str(target), parent=parent, entries=entries)

