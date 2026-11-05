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
import re
from datetime import datetime, timedelta
from typing import List, Optional

from app.models import Analysis, Rule
from app.utils.log_utils import line_signature
from app.services.alert_analysis import render_notes
from app.services.prompts import AUTOPILOT_PROFILE_SCHEMA, AUTOPILOT_PROFILE_SYSTEM, autopilot_label
from app.services.log_guard import fence
from app.services.log_source import source_type
from app.utils import keyword_match
from app.utils.log_utils import clean_log_line

MANAGED_FIELDS = ("name", "application_context", "anti_spam_delay", "inactivity_period_hours", "notify_severity_threshold")
RHYTHM_FIELDS = ("anti_spam_delay", "inactivity_period_hours", "notify_severity_threshold")  # toujours gérés à l'activation
STATE_VERSION = 3               # 2 : seuil de notification géré ; 3 : notification de silence active
NOTIFY_MIN_ALERTS = 10          # alertes sur 30 jours avant de juger le seuil de notification
NOTIFY_LOW_SHARE = 0.7          # part d'alertes « info » (ou signalées pas pertinentes) qui fait passer à « warning »
SEVERITIES = ("info", "warning", "critical")
PATTERN_MAX_SHARE = 0.05        # un motif de retour à la normale présent dans plus de 5 % des lignes ne signale rien
PATTERN_ERROR_DISTANCE = 3      # … ni un motif qui apparaît à 3 lignes ou moins d'une ligne d'alerte
EVENTS_MAX = 20                 # décisions gardées pour l'activité du dashboard
QUIET_MIN_ANALYSES = 3          # une ligne jugée sans gravité par ses 3 dernières analyses ne repart plus vers l'IA…
QUIET_RECHECK_HOURS = 6         # … sauf une revérification toutes les 6 heures
QUIET_LOOKBACK = 100            # analyses récentes consultées
QUIET_KEPT = 30                 # événements répétitifs déjà annoncés, gardés pour ne pas les annoncer deux fois
AUTOPILOT_PATTERNS_MAX = 8      # motifs appris actifs au plus, par règle
OBSERVE_HOURS = 24
OBSERVE_MAX_ROUNDS = 7          # une source toujours muette n'est pas observée indéfiniment
MIN_HISTORY_LINES = 50          # en dessous, l'historique ne suffit pas : observation
SILENCE_MIN_GAPS = 50           # écarts entre lignes en dessous desquels le rythme d'une source reste inconnu
MIN_HISTORY_SPAN_H = 6          # … ni un historique court et peu fourni (moins de RICH_HISTORY_LINES lignes)
RICH_HISTORY_LINES = 500
REFRESH_DAYS = 7
PROFILE_CHANGE_OVERLAP = 0.5    # contexte revu si moins de la moitié des modèles fréquents sont encore là
TOP_SIGNATURES = 40
OBSERVE_KEYWORD = "[error level]"   # mot-clé affiché pour les alertes de la phase d'observation
SERIOUS_KEYWORD = "[serious line]"  # mot-clé affiché pour une panne franche qu'aucun mot-clé n'attrape
# Pannes franches, rares par nature : une seule ligne vaut une analyse, sans attendre que l'arbitre en fasse un mot-clé
# (il lui faut plusieurs occurrences). Rejoué sur 4 mois de logs réels : quelques événements, aucune fausse piste.
SERIOUS_LINE = re.compile(
    r"out of memory|oom[-_ ]?kill|killed process|kernel panic|segfault|segmentation fault|general protection fault"
    r"|core dumped|i/o error|disk\S* (?:read|write) error|medium error|uncorrectable|bad sector|read-only file ?system"
    r"|remount(?:ing|ed)?.{0,40}read-only|ext4-fs error|xfs.{0,40}corrupt|btrfs.{0,40}(?:error|corrupt)"
    r"|no space left on device|disk full|nfs: server .{0,80}not responding|blocked for more than \d+ seconds"
    r"|soft lockup|hard lockup|machine check|hardware error|database disk image is malformed|data corruption"
    r"|filesystem corrupt", re.I)
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


def log_event(rule: Rule, text: str):
    """Garde une décision du pilote pour l'afficher sur le dashboard (les plus récentes seulement)."""
    data = load(rule)
    data["events"] = (data.get("events", []) + [{"at": datetime.utcnow().isoformat(), "text": text[:300]}])[-EVENTS_MAX:]
    save(rule, data)


def _harmless(a: Analysis) -> bool:
    """Jugée sans gravité par l'IA ou signalée pas pertinente. Une analyse échouée (enregistrée en « info ») ne dit rien.
    Une analyse critique reste analysée même signalée : un signalement erroné ne doit pas cacher une vraie panne."""
    if (a.ollama_response or "").startswith("[Erreur"):
        return False
    return a.severity == "info" or (a.feedback == "irrelevant" and a.severity != "critical")


def quiet_lines(db, rule: Rule, lines: List[str], now: Optional[datetime] = None) -> tuple:
    """Sépare les lignes à analyser des événements répétitifs déjà jugés sans gravité : celles dont les 3 dernières
    analyses contenant le même événement sont sans gravité, la plus récente datant de moins de 6 heures. Une gravité
    plus élevée parmi elles, ou un événement nouveau, repart vers l'IA. Retourne (lignes à analyser, signatures tues)."""
    from app.services.alert_analysis import analysis_lines
    now = now or datetime.utcnow()
    recent = db.query(Analysis).filter(Analysis.rule_id == rule.id).order_by(Analysis.analyzed_at.desc()).limit(QUIET_LOOKBACK).all()
    history = {}
    for a in recent:
        for sig in {line_signature(l) for l in analysis_lines(a)}:
            history.setdefault(sig, []).append(a)
    kept, quiet = [], []
    for line in lines:
        sig = line_signature(line)
        past = history.get(sig, [])[:QUIET_MIN_ANALYSES]
        if len(past) == QUIET_MIN_ANALYSES and all(_harmless(a) for a in past) \
                and now - past[0].analyzed_at < timedelta(hours=QUIET_RECHECK_HOURS):
            if sig not in quiet:
                quiet.append(sig)
        else:
            kept.append(line)
    return kept, quiet


def note_quiet(rule: Rule, signatures: List[str], lang: str):
    """Annonce une seule fois chaque événement répétitif dans les décisions du pilote."""
    data = load(rule)
    known = data.get("quiet", [])
    fresh = [s for s in signatures if s not in known]
    if not fresh:
        return
    data["quiet"] = (known + fresh)[-QUIET_KEPT:]
    save(rule, data)
    for sig in fresh:
        log_event(rule, (f"Événement répétitif jugé sans gravité : analysé seulement toutes les {QUIET_RECHECK_HOURS} h : `{sig[:140]}`"
                         if lang == "fr" else
                         f"Repeated event rated harmless: analyzed only every {QUIET_RECHECK_HOURS} h: `{sig[:140]}`"))


def changes_text(rule: Rule, fields: List[str], lang: str) -> str:
    """Résumé des réglages que le pilote vient de définir."""
    fr = lang == "fr"
    parts = []
    if "name" in fields:
        parts.append(f"nom « {rule.name} »" if fr else f"name \"{rule.name}\"")
    if "application_context" in fields:
        parts.append("contexte" if fr else "context")
    if "anti_spam_delay" in fields:
        parts.append(f"regroupement {rule.anti_spam_delay} s" if fr else f"grouping {rule.anti_spam_delay} s")
    if "inactivity_period_hours" in fields:
        parts.append(f"silence {rule.inactivity_period_hours} h")
    if "notify_severity_threshold" in fields:
        parts.append(f"notifications à partir de {rule.notify_severity_threshold}" if fr else f"notifications from {rule.notify_severity_threshold}")
    return ("Réglages définis : " if fr else "Settings set: ") + ", ".join(parts)


def L_no_trigger(lang: str) -> str:
    return autopilot_label(lang, "no_trigger")


def enabled(rule: Rule) -> bool:
    return bool(load(rule).get("enabled"))


def serious_line(line: str) -> bool:
    return bool(SERIOUS_LINE.search(line))


def is_observing(rule: Rule) -> bool:
    """Règle pas encore prête (lecture de l'historique, observation, ou échec en attente d'un nouvel essai) :
    elle alerte sur les lignes de niveau erreur explicite."""
    data = load(rule)
    return bool(data.get("enabled")) and data.get("status") in ("learning", "observing", "error")


def public(rule: Rule) -> dict:
    data = load(rule)
    return {"enabled": bool(data.get("enabled")), "status": data.get("status"), "managed": data.get("managed", []),
            "observe_until": data.get("observe_until"), "session_id": data.get("session_id"),
            "profiled_at": data.get("profiled_at"), "error": data.get("error"), "no_trigger": bool(data.get("no_trigger"))}


def default_name(log_file_path: str) -> str:
    kind = source_type(log_file_path)
    if kind == "syslog":
        return f"Syslog {log_file_path.split(':', 1)[1].strip() or '*'}"
    if kind == "webhook":
        return "Webhook"
    if kind == "docker":
        return log_file_path.split(":", 2)[2] or "Docker"
    if kind == "homeassistant":
        return "Home Assistant"
    if kind == "mqtt":
        return f"MQTT {log_file_path.split(':', 2)[2]}"
    return log_file_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or log_file_path


def enable(rule: Rule, new_rule: bool, user_values: Optional[dict] = None):
    """Active le pilote. Nouvelle règle : il gère tout ce que l'utilisateur n'a pas rempli. Règle existante : il garde
    le nom et un contexte déjà écrit, et gère la fenêtre de regroupement et le délai de silence. Dans les deux cas,
    une source muette notifie : le pilote calcule le délai, et le réglage n'est pas affiché dans ce mode."""
    user_values = user_values or {}
    if new_rule:
        managed = [f for f in MANAGED_FIELDS if f in RHYTHM_FIELDS or not user_values.get(f)]
    else:
        managed = list(RHYTHM_FIELDS) + ([] if (rule.application_context or "").strip() else ["application_context"])
    data = load(rule)
    data.update({"enabled": True, "status": "learning", "managed": managed, "v": STATE_VERSION,
                 "values": {f: getattr(rule, f) for f in managed}, "error": None})
    save(rule, data)
    rule.inactivity_warning_enabled = rule.inactivity_notify = True


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
    """Délai (h) avant d'alerter sur une source muette : le double du plus long silence observé, entre 1 h et 7 jours.
    Une source très bavarde en rafales se tait aussi longtemps qu'une autre : un centile masquerait ses vrais silences.
    Une source qui écrit peu n'a pas montré son rythme : au moins un jour. None si la période est trop courte pour en juger."""
    if span_h < MIN_HISTORY_SPAN_H:
        return None
    hours = math.ceil(max(gaps_s, default=0) * 2 / 3600)
    if len(gaps_s) < SILENCE_MIN_GAPS:
        hours = max(hours, 24)
    return int(min(168, max(1, hours)))


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
    if kind == "docker":
        return f"logs du conteneur Docker {log_file_path.split(':', 2)[2]}"
    if kind == "homeassistant":
        return "journal de Home Assistant (avertissements et erreurs seulement)"
    if kind == "mqtt":
        return f"messages MQTT du sujet {log_file_path.split(':', 2)[2]}, chaque ligne commence par son sujet"
    return f"fichier {log_file_path}"


def build_profile_messages(lang: str, rule: Rule, result: dict, period: str, notes: Optional[List[dict]] = None) -> list:
    L = lambda key, **kw: autopilot_label(lang, key, **kw)
    templates = result["templates"]
    frequent = sorted(templates.values(), key=lambda t: t["count"], reverse=True)[:25]
    errors = [t for t in sorted(templates.values(), key=lambda t: t["count"], reverse=True)
              if t["error_like"] and t not in frequent][:10]
    parts = [L("source", source=source_label(rule.log_file_path)), L("period", period=period),
             L("volume", lines=result["lines"], templates=len(templates))]
    if (rule.application_context or "").strip() and "application_context" not in load(rule).get("managed", []):
        parts.append(L("user_context", context=rule.application_context[:CONTEXT_MAX]))
    if notes:
        parts.append(render_notes(notes, lang, intro="notes_intro_facts"))
    parts.append("\n" + L("frequent_title"))
    parts.append(fence("\n".join(L("line", count=t["count"], example=clean_log_line(t["examples"][0])[:300]) for t in frequent)))
    if errors:
        parts.append("\n" + L("errors_title"))
        parts.append(fence("\n".join(L("line", count=t["count"], example=clean_log_line(t["examples"][0])[:300]) for t in errors)))
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
    passe sous la gestion du pilote, et une source muette notifie."""
    data = load(rule)
    if not data.get("enabled") or data.get("v", 1) >= STATE_VERSION:
        return
    if (rule.notify_severity_threshold or "info") == "info" and "notify_severity_threshold" not in data.get("managed", []):
        data.setdefault("managed", []).append("notify_severity_threshold")
        data.setdefault("values", {})["notify_severity_threshold"] = rule.notify_severity_threshold or "info"
    if data.get("v", 1) < 3:
        rule.inactivity_warning_enabled = rule.inactivity_notify = True
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
