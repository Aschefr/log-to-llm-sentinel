import json
import re

# Normalisation des parties variables d'une ligne de log (horodatages, IP, PID, identifiants…).
# Les petits nombres (< 4 chiffres) sont conservés : codes d'erreur / HTTP significatifs (error 13, 500).
_SIG_REPEATED = re.compile(r"message repeated \d+ times: \[\s*(.*?)\s*\]\s*$")
_SIG_RULES = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TS>"),
    (re.compile(r"^<\d+>"), ""),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<ID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"), "<HEX>"),
    (re.compile(r"\[\d+\]"), "[<N>]"),
    (re.compile(r"\b\d{4,}\b"), "<N>"),
    (re.compile(r"\s+"), " "),
]


def line_signature(line: str) -> str:
    """Gabarit d'une ligne : deux occurrences d'un même événement ont la même signature."""
    # "message repeated N times: [ X ]" → X, en gardant le préfixe (horodatage, hôte, processus)
    sig = _SIG_REPEATED.sub(r"\1", clean_log_line(line).strip())
    for pattern, repl in _SIG_RULES:
        sig = pattern.sub(repl, sig)
    return sig.strip()[:300]


def aggregate_lines(lines: list) -> list:
    """Regroupe des lignes par signature, dans l'ordre de première apparition.
    Retourne [{"signature", "count", "example"}] où example est l'occurrence la plus récente."""
    groups = {}
    for line in lines:
        sig = line_signature(line)
        if sig in groups:
            groups[sig]["count"] += 1
            groups[sig]["example"] = line
        else:
            groups[sig] = {"signature": sig, "count": 1, "example": line}
    return list(groups.values())


def clean_log_line(line: str) -> str:
    """Tente de nettoyer une ligne de log si elle est au format JSON (ex: Nextcloud)."""
    stripped = line.strip()
    if not (stripped.startswith('{') and stripped.endswith('}')):
        return line

    try:
        data = json.loads(stripped)
        # Pour Nextcloud : extraire message, app, et éventuellement exception
        msg = data.get("message", "")
        app = data.get("app", "")
        # Extraire l'exception si elle existe (peut être dans 'exception' ou 'data.exception')
        exc = data.get("exception", "") or data.get("data", {}).get("exception", "")
        
        if msg:
            cleaned = f"[{app}] {msg}"
            if exc:
                # On ne garde que le début de l'exception si elle est énorme
                exc_str = str(exc)
                if len(exc_str) > 1000:
                    exc_str = exc_str[:1000] + "... [EXCEPTION TRONQUÉE]"
                cleaned += f" | Exception: {exc_str}"
            return cleaned
        return line # Fallback si pas de champ message
    except:
        return line


def rotate_log_file(fp, max_bytes: int, backup_count: int = 5):
    """
    Standard shifted log rotation:
    - If fp does not exist or size < max_bytes, do nothing.
    - Otherwise, shift backups (xxx.log.4 -> xxx.log.5, ..., xxx.log -> xxx.log.1)
    """
    from pathlib import Path
    fp = Path(fp)
    if not fp.exists() or fp.stat().st_size < max_bytes:
        return
        
    for i in range(backup_count - 1, 0, -1):
        sfn = fp.with_suffix(f"{fp.suffix}.{i}")
        dfn = fp.with_suffix(f"{fp.suffix}.{i + 1}")
        if sfn.exists():
            try:
                if dfn.exists():
                    dfn.unlink()
                sfn.rename(dfn)
            except Exception:
                pass
                
    dfn = fp.with_suffix(f"{fp.suffix}.1")
    try:
        if dfn.exists():
            dfn.unlink()
        fp.rename(dfn)
    except Exception:
        pass
