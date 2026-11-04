from typing import Optional


class SourceError(Exception):
    """Un connecteur ne peut pas lire sa source. Le code devient un message traduit dans l'interface.
    state vaut « waiting » quand rien n'est en panne, par exemple un conteneur arrêté."""

    def __init__(self, code: str, detail: Optional[str] = None, retry_seconds: Optional[float] = None, state: str = "error"):
        super().__init__(code)
        self.code, self.detail, self.retry_seconds, self.state = code, detail, retry_seconds, state
