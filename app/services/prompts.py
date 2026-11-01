"""
Textes des prompts envoyés au LLM (FR / EN), centralisés.
"""

# Sortie structurée de l'analyse d'alerte (paramètre `format` d'Ollama)
ALERT_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "probable_cause": {"type": "string"},
        "explanation": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["severity", "confidence", "summary", "probable_cause", "explanation", "actions"],
}

ALERT_SYSTEM = {
    "fr": """Tu es un expert en exploitation de systèmes (Linux, Docker, réseau, applications) chargé d'analyser les alertes d'un outil de surveillance de logs.

Méthode :
- Appuie-toi uniquement sur les données fournies (événements, contexte, historique). Si une information manque pour conclure, dis-le au lieu de l'inventer.
- Tiens compte de l'historique : un événement récurrent, à horaire régulier et qui se résout seul est en général moins grave qu'un événement nouveau ; signale-le explicitement.
- Les lignes de contexte aident à comprendre ce qui s'est passé juste avant et juste après.

Sévérité :
- critical : service indisponible, perte ou corruption de données, sécurité compromise ; action immédiate requise.
- warning : dégradation, erreur ou risque qui mérite une vérification.
- info : comportement normal, transitoire ou bénin ; aucune action nécessaire.

Réponds en français, au format JSON demandé :
- summary : une ou deux phrases (300 caractères max), compréhensibles seules, utilisées telles quelles dans les notifications.
- probable_cause : la cause la plus probable, en une phrase.
- explanation : l'analyse détaillée en Markdown (pourquoi c'est arrivé, impact).
- actions : étapes concrètes et ordonnées (avec commandes si utile) ; liste vide si aucune action n'est nécessaire.
- confidence : ta confiance dans le diagnostic, de 0 à 100.""",
    "en": """You are a systems operations expert (Linux, Docker, networking, applications) analyzing alerts raised by a log monitoring tool.

Method:
- Rely only on the data provided (events, context, history). If information is missing to conclude, say so instead of inventing it.
- Take the history into account: a recurring event, at regular times, that resolves by itself is usually less severe than a new one; state it explicitly.
- Context lines help understand what happened right before and right after.

Severity:
- critical: service down, data loss or corruption, security compromised; immediate action required.
- warning: degradation, error or risk worth checking.
- info: normal, transient or benign behaviour; no action needed.

Answer in English, using the requested JSON format:
- summary: one or two sentences (300 characters max), self-contained, used as-is in notifications.
- probable_cause: the most likely cause, in one sentence.
- explanation: the detailed analysis in Markdown (why it happened, impact).
- actions: concrete, ordered steps (with commands when useful); empty list if no action is needed.
- confidence: your confidence in the diagnosis, from 0 to 100.""",
}

LABELS = {
    "fr": {
        "admin_instructions": "Consignes de l'administrateur",
        "rule": "Règle surveillée",
        "name": "Nom",
        "source": "Source",
        "source_file": "fichier",
        "source_webhook": "webhook (lignes reçues par HTTP)",
        "source_syslog": "syslog (hôte {host})",
        "app_context": "Contexte applicatif",
        "now": "Date actuelle (UTC)",
        "events": "Événements déclencheurs",
        "keywords": "Mots-clés détectés",
        "context": "Contexte dans la source ({n} lignes avant / après ; lignes déclenchantes marquées »)",
        "context_gap": "[… lignes déclenchantes : voir ci-dessus …]",
        "history": "Historique de cette règle ({days} derniers jours)",
        "history_total": "{total} alerte(s) sur cette règle, dont {similar} similaire(s) à celle-ci.",
        "history_first": "Aucune alerte similaire : premier signalement de cet événement sur la période.",
        "history_hours": "Heures (UTC) des occurrences similaires : {hours}",
        "history_recent": "Dernières occurrences similaires :",
        "resolved_after": "résolu après {duration}",
        "resolution_pending": "non résolu",
        "summary": "Résumé",
        "cause": "Cause probable",
        "actions": "Actions recommandées",
        "no_action": "Aucune action nécessaire.",
        "confidence": "Confiance",
        "group_header": "Ces {total} événements correspondants sont apparus dans les {delay} dernières secondes ({distinct} distincts). Dans l'ordre chronologique :",
        "group_truncated": "... [Tronqué : limite de {max_chars} caractères atteinte ({total} événements détectés)]",
    },
    "en": {
        "admin_instructions": "Administrator instructions",
        "rule": "Monitored rule",
        "name": "Name",
        "source": "Source",
        "source_file": "file",
        "source_webhook": "webhook (lines received over HTTP)",
        "source_syslog": "syslog (host {host})",
        "app_context": "Application context",
        "now": "Current date (UTC)",
        "events": "Triggering events",
        "keywords": "Matched keywords",
        "context": "Context in the source ({n} lines before / after; triggering lines marked »)",
        "context_gap": "[… triggering lines: see above …]",
        "history": "History of this rule (last {days} days)",
        "history_total": "{total} alert(s) on this rule, {similar} of them similar to this one.",
        "history_first": "No similar alert: first report of this event over the period.",
        "history_hours": "Hours (UTC) of similar occurrences: {hours}",
        "history_recent": "Latest similar occurrences:",
        "resolved_after": "resolved after {duration}",
        "resolution_pending": "not resolved",
        "summary": "Summary",
        "cause": "Probable cause",
        "actions": "Recommended actions",
        "no_action": "No action needed.",
        "confidence": "Confidence",
        "group_header": "These {total} matching events appeared in the last {delay} seconds ({distinct} distinct). In chronological order:",
        "group_truncated": "... [Truncated: {max_chars} character limit reached ({total} events detected)]",
    },
}


def label(lang: str, key: str, **kwargs) -> str:
    text = LABELS.get(lang, LABELS["fr"]).get(key) or LABELS["fr"][key]
    return text.format(**kwargs) if kwargs else text
