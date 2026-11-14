"""
Les lignes de log sont écrites par les programmes surveillés, et parfois par un attaquant (nom d'utilisateur SSH, URL).
Elles ne doivent pas pouvoir dicter son verdict à l'IA : elles lui sont présentées dans un bloc à part, et une ligne
qui s'adresse à l'IA ne peut plus faire baisser la gravité, quoi qu'en dise la réponse du modèle.
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

_READER = (r"(?:classifier|classifieur|analyst|analyste|analista|moderator|moderateur|llm|chatbot|ai model|modele d'ia|modele ia"
           r"|ai assistant|assistant ia|assistant d'ia|ai agent|agent ia|assistant|l'ia|ai|ia)")
# Noms des outils de Sentinel : un programme n'a aucune raison de les écrire dans son journal
_TOOL = r"(?:host_check|docker_list_containers|docker_container_\w+|propose_action|web_search|lookup_cve|tail_logs|search_logs)"
_CLASSIFY = r"(?:classify|classifiez|classer|classez|classe ceci|classe cet|mark as|marque comme|marquer comme|rate as)"
_REPLY = r"(?:answer|reply|respond|repondre|repondez|reponds|responde)"
_VERDICT = r"(?:info|informational|informationnel|benign|benin|harmless|inoffensi[a-z]+|no action|aucune action)"

_INSTRUCTION = re.compile("|".join((
    # « ignore les instructions précédentes », dans les langues usuelles
    r"\b(?:ignore[zr]?|ignora|disregard|forget|oublie[zr]?|override)\b[^.\n]{0,40}\b(?:instructions?|instrucciones|consignes?|prompts?)\b",
    # Faux message du système ou de l'administrateur
    rf"\[\s*(?:system|systeme|sys|assistant|admin[a-z]*)\s*\][^\n]{{0,120}}\b(?:{_CLASSIFY}|{_REPLY}|instructions?)\b",
    # Balises de conversation des modèles
    r"<\|?(?:im_start|im_end|endoftext)\|?>|<<\s*sys\s*>>|\[/?inst\]",
    # Ordre de verdict : « classify this as benign », « répondre info »
    rf"\b{_CLASSIFY}\b[^.\n]{{0,30}}\b{_VERDICT}\b",
    rf"\b{_REPLY}\s*(?:with |by |is |as |par |avec |en |: ?|= ?)?[\"']?{_VERDICT}\b",
    # Imitation de la structure du prompt : aucun programme n'écrit ça dans ses journaux
    r"</?log\b|##\s*(?:notes?|evenements|events|contexte|context|historique|history)\b|notes? de l'administrateur|administrator notes?",
    # Phrase adressée au lecteur automatique
    rf"\b(?:note|notice|message|instruction|aviso)\s+(?:au|aux|to|for|pour|al)\s+(?:the\s+|le\s+)?{_READER}\b",
    # Ordre d'appeler un outil de Sentinel (second regard, chat)
    rf"\b{_TOOL}\b",
)))


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def addresses_the_ai(lines: Iterable[str]) -> bool:
    """Vrai si une ligne de log contient une phrase destinée à l'IA plutôt qu'au journal."""
    return any(line and _INSTRUCTION.search(_plain(line)) for line in lines)


_fence_key: Optional[bytes] = None
_fence_lock = threading.Lock()


def _key() -> bytes:
    """Clé secrète de l'installation (data/fence.key, créée au premier usage). Sans dossier inscriptible : clé du processus."""
    global _fence_key
    with _fence_lock:
        if _fence_key is None:
            path = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "fence.key"
            try:
                key = path.read_bytes()
                if len(key) < 16:
                    raise ValueError
            except (OSError, ValueError):
                key = secrets.token_bytes(32)
                try:
                    path.write_bytes(key)
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            _fence_key = key
        return _fence_key


def fence(text: str) -> str:
    """Encadre des lignes de log. La balise dépend du texte et d'une clé secrète : une ligne ne peut pas deviner celle qui
    la refermerait, et les mêmes lignes donnent la même balise (réponses reproductibles de l'IA)."""
    tag = "LOG-" + hmac.new(_key(), text.encode("utf-8", "replace"), hashlib.sha256).hexdigest()[:6]
    return f"<{tag}>\n{text}\n</{tag}>"
