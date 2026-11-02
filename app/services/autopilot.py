"""Pilote automatique d'une règle (AP-1).

L'utilisateur ne donne que la source. Sentinel lit l'historique (ou observe 24 h s'il n'y en a pas), puis :
- l'IA définit le nom de la règle et le contexte de l'application (ce que contient ce journal, ce qui y est normal) ;
- l'arbitre des mots-clés (keyword_tuning) choisit les mots-clés et les exclusions ;
- la fenêtre de regroupement et le délai de silence sont calculés sur le rythme réel des lignes, sans IA ;
- le seuil de notification suit les alertes réelles (relevé à « warning » quand presque tout est « info ») ;
- les motifs de retour à la normale appris sont appliqués directement s'ils passent des garde-fous ;
- un changement de mot-clé que les garde-fous retiennent est écarté (journalisé), jamais laissé en attente.

Pendant l'observation, la règle alerte seulement sur les lignes de niveau erreur explicite.
Chaque semaine, l'historique est relu : le contexte est revu si le contenu du journal a nettement changé.
Un réglage modifié à la main n'est plus touché par le pilote.

État (Rule.autopilot_json) :
{"enabled": bool, "status": "learning" | "observing" | "active" | "error", "managed": [champs], "values": {champ: valeur},
 "observe_until": iso, "observe_rounds": n, "profiled_at": iso, "signatures": [...], "session_id": n, "error": str}
"""
import json
import math
from datetime import datetime, timedelta
from typing import List, Optional

from app.models import Analysis, Rule
from app.services.prompts import AUTOPILOT_PROFILE_SCHEMA, AUTOPILOT_PROFILE_SYSTEM, autopilot_label
from app.services.log_source import source_type
from app.utils import keyword_match
from app.utils.log_utils import clean_log_line

MANAGED_FIELDS = ("name", "application_context", "anti_spam_delay", "inactivity_period_hours", "notify_severity_threshold")
RHYTHM_FIELDS = ("anti_spam_delay", "inactivity_period_hours", "notify_severity_threshold")  # toujours gérés à l'activation
STATE_VERSION = 2               # 2 : seuil de notification géré
NOTIFY_MIN_ALERTS = 10          # alertes sur 30 jours avant de juger le seuil de notification
NOTIFY_LOW_SHARE = 0.7          # part d'alertes « info » (ou signalées pas pertinentes) qui fait passer à « warning »
SEVERITIES = ("info", "warning", "critical")
PATTERN_MAX_SHARE = 0.05        # un motif de retour à la normale présent dans plus de 5 % des lignes ne signale rien
PATTERN_ERROR_DISTANCE = 3      # … ni un motif qui apparaît à 3 lignes ou moins d'une ligne d'alerte
AUTOPILOT_PATTERNS_MAX = 8      # motifs appris actifs au plus, par règle
OBSERVE_HOURS = 24
OBSERVE_MAX_ROUNDS = 7          # une source toujours muette n'est pas observée indéfiniment
MIN_HISTORY_LINES = 50          # en dessous, l'historique ne suffit pas : observation
MIN_HISTORY_SPAN_H = 6          # … ni un historique court et peu fourni (moins de RICH_HISTORY_LINES lignes)
RICH_HISTORY_LINES = 500
REFRESH_DAYS = 7
PROFILE_CHANGE_OVERLAP = 0.5    # contexte revu si moins de la moitié des modèles fréquents sont encore là
TOP_SIGNATURES = 40
OBSERVE_KEYWORD = "[error level]"   # mot-clé affiché pour les alertes de la phase d'observation
NAME_MAX = 60
CONTEXT_MAX = 800


def load(rule: Rule) -> dict:
    try:
        data = json.loads(rule.autopilot_json or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def save(rule: Rule, data: dict):
    rule.autopilot_json = json.dumps(data, default=str)


def enabled(rule: Rule) -> bool:
    return bool(load(rule).get("enabled"))


def is_observing(rule: Rule) -> bool:
    """Règle pas encore prête (lecture de l'historique, observation, ou échec en attente d'un nouvel essai) :
    elle alerte sur les lignes de niveau erreur explicite."""
    data = load(rule)
    return bool(data.get("enabled")) and data.get("status") in ("learning", "observing", "error")


def public(rule: Rule) -> dict:
    data = load(rule)
    return {"enabled": bool(data.get("enabled")), "status": data.get("status"), "managed": data.get("managed", []),
            "observe_until": data.get("observe_until"), "session_id": data.get("session_id"),
            "profiled_at": data.get("profiled_at"), "error": data.get("error")}


def default_name(log_file_path: str) -> str:
    kind = source_type(log_file_path)
    if kind == "syslog":
        return f"Syslog {log_file_path.split(':', 1)[1].strip() or '*'}"
    if kind == "webhook":
        return "Webhook"
    return log_file_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or log_file_path


def enable(rule: Rule, new_rule: bool, user_values: Optional[dict] = None):
    """Active le pilote. Nouvelle règle : il gère tout ce que l'utilisateur n'a pas rempli. Règle existante : il garde
    le nom et un contexte déjà écrit, et gère la fenêtre de regroupement et le délai de silence."""
    user_values = user_values or {}
    if new_rule:
        managed = [f for f in MANAGED_FIELDS if f in RHYTHM_FIELDS or not user_values.get(f)]
    else:
        managed = list(RHYTHM_FIELDS) + ([] if (rule.application_context or "").strip() else ["application_context"])
    data = load(rule)
    data.update({"enabled": True, "status": "learning", "managed": managed, "v": STATE_VERSION,
                 "values": {f: getattr(rule, f) for f in managed}, "error": None})
    save(rule, data)


def disable(rule: Rule):
    data = load(rule)
    data.update({"enabled": False, "status": None, "observe_until": None})
    save(rule, data)


def release_manual_edits(rule: Rule):
    """Après une modification dans les réglages : un champ dont la valeur n'est plus celle du pilote lui est repris."""
    data = load(rule)
    if not data.get("enabled"):
        return
    values = data.get("values", {})
    kept = [f for f in data.get("managed", []) if str(getattr(rule, f)) == str(values.get(f, getattr(rule, f)))]
    if kept != data.get("managed", []):
        data["managed"] = kept
        save(rule, data)


def set_value(rule: Rule, field: str, value) -> bool:
    """Écrit un réglage si le pilote le gère encore. True si la valeur a changé."""
    data = load(rule)
    if field not in data.get("managed", []):
        return False
    changed = str(getattr(rule, field)) != str(value)
    setattr(rule, field, value)
    data.setdefault("values", {})[field] = value
    save(rule, data)
    return changed


def set_state(rule: Rule, **fields):
    data = load(rule)
    data.update(fields)
    save(rule, data)


# ── Rythme du journal (sans IA) ────────────────────────────────────────────────────

def _quantile(values: List[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def grouping_window(timestamps: List[datetime]) -> Optional[int]:
    """Fenêtre de regroupement (s) : assez longue pour réunir les lignes d'un même événement. Calculée sur les écarts
    entre lignes déclenchantes proches (moins de 10 min) ; None si trop peu de données."""
    ts = sorted(timestamps)
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
    burst = [g for g in gaps if 0 <= g < 600]
    if len(burst) < 5:
        return None
    return int(min(300, max(30, math.ceil(_quantile(burst, 0.9) * 2 / 30) * 30)))


def silence_hours(gaps_s: List[float], span_h: float) -> Optional[int]:
    """Délai (h) avant d'alerter sur une source muette : le double du plus long silence habituel (99e centile des
    écarts entre lignes), entre 1 h et 7 jours. None si la période observée est trop courte pour en juger."""
    if span_h < MIN_HISTORY_SPAN_H:
        return None
    longest = _quantile(gaps_s, 0.99) if len(gaps_s) >= 100 else (max(gaps_s) if gaps_s else 0)
    return int(min(168, max(1, math.ceil(longest * 2 / 3600))))


def top_signatures(templates: dict) -> List[str]:
    return [sig for sig, _ in sorted(templates.items(), key=lambda kv: kv[1]["count"], reverse=True)[:TOP_SIGNATURES]]


def profile_changed(old: List[str], new: List[str]) -> bool:
    if not old:
        return True
    return len(set(old) & set(new)) / max(1, min(len(old), len(new))) < PROFILE_CHANGE_OVERLAP


def matched_timestamps(templates: dict, keywords: List[str], excluded: List[str]) -> List[datetime]:
    """Horodatages des lignes que les mots-clés retenus déclenchent (d'après les exemples de chaque modèle)."""
    out = []
    for t in templates.values():
        if t.get("times") and t["examples"] and keyword_match.classify(t["examples"][0], keywords, excluded)[0] == "matched":
            out.extend(t["times"])
    return out


# ── Profil par l'IA ────────────────────────────────────────────────────────────────

def source_label(log_file_path: str) -> str:
    kind = source_type(log_file_path)
    if kind == "syslog":
        return f"syslog, hôte {log_file_path.split(':', 1)[1].strip() or '*'}"
    if kind == "webhook":
        return "webhook (lignes envoyées par un script)"
    return f"fichier {log_file_path}"


def build_profile_messages(lang: str, rule: Rule, result: dict, period: str) -> list:
    L = lambda key, **kw: autopilot_label(lang, key, **kw)
    templates = result["templates"]
    frequent = sorted(templates.values(), key=lambda t: t["count"], reverse=True)[:25]
    errors = [t for t in sorted(templates.values(), key=lambda t: t["count"], reverse=True)
              if t["error_like"] and t not in frequent][:10]
    parts = [L("source", source=source_label(rule.log_file_path)), L("period", period=period),
             L("volume", lines=result["lines"], templates=len(templates))]
    if (rule.application_context or "").strip() and "application_context" not in load(rule).get("managed", []):
        parts.append(L("user_context", context=rule.application_context[:CONTEXT_MAX]))
    parts.append("\n" + L("frequent_title"))
    parts += [L("line", count=t["count"], example=clean_log_line(t["examples"][0])[:300]) for t in frequent]
    if errors:
        parts.append("\n" + L("errors_title"))
        parts += [L("line", count=t["count"], example=clean_log_line(t["examples"][0])[:300]) for t in errors]
    return [{"role": "system", "content": AUTOPILOT_PROFILE_SYSTEM.get(lang, AUTOPILOT_PROFILE_SYSTEM["fr"])},
            {"role": "user", "content": "\n".join(parts)}]


def apply_profile(rule: Rule, profile: Optional[dict]) -> List[str]:
    """Applique le nom et le contexte proposés aux champs encore gérés. Retourne les champs modifiés."""
    if not isinstance(profile, dict):
        return []
    changed = []
    name = " ".join(str(profile.get("name") or "").split())[:NAME_MAX]
    if len(name) >= 3 and set_value(rule, "name", name):
        changed.append("name")
    context = str(profile.get("application_context") or "").strip()[:CONTEXT_MAX]
    if len(context) >= 20 and set_value(rule, "application_context", context):
        changed.append("application_context")
    return changed


def history_too_short(lines: int, span_h: float) -> bool:
    """L'historique ne suffit pas pour définir la règle : trop peu de lignes, ou peu de lignes sur peu de temps.
    Un journal très actif (Docker Desktop tourne son fichier toutes les 2 h) suffit même sur quelques heures."""
    return lines < MIN_HISTORY_LINES or (span_h < MIN_HISTORY_SPAN_H and lines < RICH_HISTORY_LINES)


def upgrade_state(rule: Rule):
    """Règles mises sous pilote par une version précédente : le seuil de notification, resté à sa valeur par défaut,
    passe sous la gestion du pilote."""
    data = load(rule)
    if not data.get("enabled") or data.get("v", 1) >= STATE_VERSION:
        return
    if (rule.notify_severity_threshold or "info") == "info" and "notify_severity_threshold" not in data.get("managed", []):
        data.setdefault("managed", []).append("notify_severity_threshold")
        data.setdefault("values", {})["notify_severity_threshold"] = rule.notify_severity_threshold or "info"
    data["v"] = STATE_VERSION
    save(rule, data)


def notification_threshold(db, rule: Rule) -> Optional[tuple]:
    """Seuil de notification que justifient les alertes des 30 derniers jours : « warning » quand au moins 70 % d'au
    moins 10 alertes sont « info » ou signalées pas pertinentes. Jamais plus bas que le seuil actuel. None sinon."""
    since = datetime.utcnow() - timedelta(days=30)
    rows = db.query(Analysis.severity, Analysis.feedback).filter(Analysis.rule_id == rule.id,
                                                                 Analysis.analyzed_at >= since).all()
    if len(rows) < NOTIFY_MIN_ALERTS:
        return None
    low = sum(1 for sev, fb in rows if (sev or "info").lower() == "info" or fb == "irrelevant")
    current = rule.notify_severity_threshold or "info"
    if low / len(rows) >= NOTIFY_LOW_SHARE and SEVERITIES.index(current) < SEVERITIES.index("warning"):
        return "warning", {"alerts": len(rows), "low": low}
    return None


def apply_notification_threshold(db, rule: Rule) -> bool:
    found = notification_threshold(db, rule)
    if found and set_value(rule, "notify_severity_threshold", found[0]):
        set_state(rule, notify_basis=found[1])
        return True
    return False


def pattern_is_safe(db, rule: Rule, pattern: str, recent_lines: List[str]) -> bool:
    """Garde-fous d'un motif de retour à la normale appliqué sans validation :
    - spécifique : assez long, ni générique, ni mot d'échec, sans valeur variable (horodatage, identifiant…),
      sans recouvrir un mot-clé ;
    - absent de toutes les lignes ayant déclenché une alerte depuis 30 jours ;
    - rare dans le journal (au plus 5 % des lignes : sinon il ne signale rien) ;
    - jamais à 3 lignes ou moins d'une ligne d'alerte (il accompagnerait l'erreur au lieu de signaler sa fin)."""
    from app.services.alert_analysis import analysis_lines
    from app.services.keyword_tuning import _has_variable_part
    from app.services.resolution_service import FAILURE_WORDS, GENERIC_PATTERNS, LEARNED_PATTERN_MIN_LEN
    low = pattern.lower().strip()
    keywords, excluded = rule.get_keywords(), rule.get_excluded_patterns()
    if len(low) < LEARNED_PATTERN_MIN_LEN or low in GENERIC_PATTERNS or FAILURE_WORDS.search(low) \
            or _has_variable_part(pattern) or any(k.lower() in low or low in k.lower() for k in keywords):
        return False
    since = datetime.utcnow() - timedelta(days=30)
    for a in db.query(Analysis).filter(Analysis.rule_id == rule.id, Analysis.analyzed_at >= since).all():
        if any(low in l.lower() for l in analysis_lines(a)):
            return False
    if recent_lines:
        hits = [i for i, l in enumerate(recent_lines) if low in l.lower()]
        if len(hits) / len(recent_lines) > PATTERN_MAX_SHARE:
            return False
        errors = [i for i, l in enumerate(recent_lines) if keyword_match.classify(l, keywords, excluded)[0] == "matched"]
        if any(abs(h - e) <= PATTERN_ERROR_DISTANCE for h in hits for e in errors):
            return False
    return True


def observe_until(hours: int = OBSERVE_HOURS) -> str:
    return (datetime.utcnow() + timedelta(hours=hours)).isoformat()
