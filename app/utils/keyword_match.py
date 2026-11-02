"""Correspondance des mots-clés et motifs d'exclusion d'une règle avec une ligne de log.

Recherche insensible à la casse, par sous-chaîne. Exception : un motif numérique ou très court
(3 caractères au plus) doit correspondre à un mot entier, sinon « 502 » se déclencherait sur
un port « :50231 » ou une interface « vethb68c502 », et « oom » sur « zoom ».
"""
import re
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple

SHORT_PATTERN_MAX = 3


def _needs_boundary(pattern: str) -> bool:
    return pattern.isdigit() or len(pattern) <= SHORT_PATTERN_MAX


@lru_cache(maxsize=4096)
def _boundary_regex(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![0-9a-z])" + re.escape(pattern) + r"(?![0-9a-z])")


def contains(pattern: str, low_text: str) -> bool:
    """Le motif apparaît-il dans un texte déjà mis en minuscules ?"""
    p = (pattern or "").strip().lower()
    if not p:
        return False
    if _needs_boundary(p):
        return _boundary_regex(p).search(low_text) is not None
    return p in low_text


def matching(patterns: Iterable[str], text: str) -> List[str]:
    """Motifs (tels que saisis) présents dans le texte."""
    low = text.lower()
    return [p for p in patterns if contains(p, low)]


def classify(line: str, keywords: List[str], excluded: List[str]) -> Tuple[Optional[str], List[str]]:
    """('excluded', motifs) si une exclusion s'applique, ('matched', mots-clés) si la ligne déclenche
    la règle, (None, []) sinon. Les exclusions sont prioritaires, comme dans l'orchestrateur."""
    low = line.lower()
    hit = [p for p in excluded if contains(p, low)]
    if hit:
        return "excluded", hit
    hit = [k for k in keywords if contains(k, low)]
    return ("matched", hit) if hit else (None, [])


def neutralized(keywords: List[str], excluded: List[str]) -> dict:
    """Mots-clés qui ne peuvent jamais se déclencher : toute ligne qui les contient contient aussi
    une exclusion. Retourne {mot-clé: exclusion}."""
    out = {}
    for kw in keywords:
        low = kw.strip().lower()
        for exc in excluded:
            if low and contains(exc, low):
                out[kw] = exc
                break
    return out
