import json
import re

# Normalisation des parties variables d'une ligne de log (horodatages, IP, PID, identifiants…).
# Les petits nombres (< 4 chiffres) sont conservés : codes d'erreur / HTTP significatifs (error 13, 500).
_SIG_REPEATED = re.compile(r"message repeated \d+ times: \[\s*(.*?)\s*\]\s*$")
_SIG_RULES = [
    (re.compile(r"^\[×\d+\]\s*"), ""),  # préfixe des lignes regroupées (ancien format)
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<MAC>"),  # avant les heures : sa fin ressemble à 00:11:22
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{4}[/-]\d{2}[/-]\d{2}\b"), "<DATE>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:µs|us|ms)\b|\b\d+\.\d+\s?s\b|(?<=\()\d+s(?=\))"), "<DUR>"),
    (re.compile(r"\b[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TS>"),
    (re.compile(r"^<\d+>"), ""),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<ID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<IP>"),
    # Numéro de version (11.0.99, v2.4.1) : une mise à jour du programme ne fait pas un autre événement. Vu en prod
    # le 2026-11-13 : UniFi Network écrit sa version dans chaque ligne, et après sa mise à jour aucune alerte
    # n'avait plus d'historique. Après les adresses IP, qui ont quatre nombres.
    (re.compile(r"\bv?\d+(?:\.\d+){2,}\b"), "<VER>"),
    # Nom de domaine public (une requête DNS, un site visité). Pas com.docker.backend.exe ni java.lang.Exception, qui
    # distinguent un événement d'un autre : d'où la liste des terminaisons, et le refus d'une suite après la dernière.
    (re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+(?:com|net|org|fr|de|eu|uk|be|ch|io|co|app|dev|info|biz|tv|me|ai|cloud|arpa)\b(?!\.?[\w-])\.?"), "<HOST>"),
    (re.compile(r"(?:[a-zA-Z0-9-]+\.)+(?=\])"), "<HOST>"),  # nom DNS absolu en fin de champ : [QUERY: rpi5ssd.local.]
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"), "<HEX>"),
    (re.compile(r"\b((?:[A-Za-z0-9]+_)?(?:ID|id)[ :=]+)[0-9a-fA-F]{1,8}\b"), r"\1<N>"),  # valeur d'un identifiant, même sans chiffre (TXN_ID fdbd)
    (re.compile(r"\[\d+\]"), "[<N>]"),
    (re.compile(r"\b\d{4,}\b"), "<N>"),
    (re.compile(r"\s+"), " "),
]


# Dans un objet JSON, ces champs disent de quel événement il s'agit : leur valeur compte, même chiffrée (status 500)
_JSON_MEANINGFUL_KEY = re.compile(r"(?:^|_)(?:status|code|level|severity|type|event|action|state|reason|error|kind|category|result|outcome)$", re.I)
_JSON_PLAIN_VALUE = re.compile(r"[A-Za-z_-]{1,40}")
_JSON_INDEXED_KEY = re.compile(r"_\d+$")


def _json_shape(key, value):
    if isinstance(value, dict):
        return {_JSON_INDEXED_KEY.sub("_*", k): _json_shape(k, v) for k, v in value.items()}
    if isinstance(value, list):
        shapes = []
        for item in value:
            shape = _json_shape(key, item)
            if shape not in shapes:
                shapes.append(shape)
        return shapes
    if value is None or isinstance(value, bool) or _JSON_MEANINGFUL_KEY.search(str(key)):
        return value
    if isinstance(value, (int, float)):
        return "<N>"
    text = str(value)
    # Un message reste : ce sont les règles habituelles qui en retirent le variable. Le reste (domaine, chemin,
    # identifiant, nombre en texte…) change d'une occurrence à l'autre sans changer l'événement.
    if any(c.isspace() for c in text.strip()) or _JSON_PLAIN_VALUE.fullmatch(text):
        return text
    return "<V>"


def _mask_json_values(line: str) -> str:
    """Remplace un objet JSON dans la ligne par sa forme : ses champs, et seulement les valeurs qui distinguent
    un événement d'un autre. Une liste de valeurs numérotées (query_0, query_1…) compte pour un seul champ."""
    start = line.find('{"')
    end = line.rfind("}")
    if start < 0 or end < start:
        return line
    try:
        data = json.loads(line[start:end + 1])
    except ValueError:
        return line
    if not isinstance(data, dict):
        return line
    return line[:start] + json.dumps(_json_shape("", data), ensure_ascii=False, separators=(",", ":")) + line[end + 1:]


def line_signature(line: str) -> str:
    """Gabarit d'une ligne : deux occurrences d'un même événement ont la même signature."""
    # "message repeated N times: [ X ]" → X, en gardant le préfixe (horodatage, hôte, processus)
    sig = _mask_json_values(_SIG_REPEATED.sub(r"\1", clean_log_line(line).strip()))
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


# Composant qui a écrit une ligne, pour le montrer à côté du résumé : un résumé qui parle d'autre chose se voit alors.
# Préfixes retirés : ils sont les mêmes pour toutes les lignes de la source et n'apprennent rien.
_COMPONENT_PREFIXES = ("homeassistant.components.", "homeassistant.helpers.", "custom_components.", "homeassistant.",
                       "com.docker.backend.exe.", "com.docker.")
_COMPONENT_PATTERNS = [
    re.compile(r"\b(?:DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\s+\[([\w.-]+)\]"),  # Home Assistant, Python : ERROR [logger]
    re.compile(r"^\[[^\]]*\d{2}:\d{2}[^\]]*\]\[([\w.-]+)\]"),  # Docker Desktop : [horodatage][composant]
    re.compile(r"^\[([\w .-]{1,40})\] "),  # Nextcloud, ligne JSON nettoyée : [app] message
    # syslog : hôte, éventuel « modèle,firmware: » des équipements UniFi, puis programme[pid]:
    re.compile(r"(?<!\d)\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+\S+\s+(?:\S+,\S+:\s+)?([A-Za-z][\w./-]*?)(?:\[\d+\])?:\s"),
]
_SYSTEMD_UNIT = re.compile(r"^\s*([\w@.-]+\.(?:service|timer|socket|mount)):")


def line_component(line: str) -> str | None:
    """Le composant qui a écrit la ligne (logger, programme, application), en court, ou None si la forme est inconnue."""
    text = clean_log_line(line)
    for pattern in _COMPONENT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        name = m.group(1)
        if name == "systemd":  # systemd parle pour une unité : c'est elle qui compte
            unit = _SYSTEMD_UNIT.match(text[m.end():])
            if unit:
                name = unit.group(1)
        for prefix in _COMPONENT_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):]
                break
        return name[:48]
    return None


def line_components(lines: list, limit: int = 3) -> list:
    """Composants des lignes, du plus fréquent au moins fréquent, sans doublon."""
    counts = {}
    for line in lines:
        name = line_component(line)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda x: -x[1])][:limit]


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
