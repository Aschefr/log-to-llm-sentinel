import json

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
