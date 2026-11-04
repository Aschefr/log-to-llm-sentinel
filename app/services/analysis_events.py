"""
Regroupement des analyses par événement : les analyses d'une règle dont les lignes ont les mêmes signatures sont le
même événement. Rien n'est supprimé : les statistiques et l'apprentissage continuent de compter chaque analyse.
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import event

from app.models import Analysis, EventNote, GlobalConfig, Rule
from app.services.alert_analysis import analysis_lines
from app.services.rule_stats import SEVERITIES, iso
from app.utils.log_utils import line_signature

_RANK = {"info": 0, "warning": 1, "critical": 2}
# À incrémenter quand le calcul des signatures change : les analyses et les notes existantes sont recalculées une fois
EVENT_KEY_VERSION = 2
REKEY_BATCH = 1000
# Un événement qui continue ne notifie plus, mais ne se fait pas oublier non plus
NOTIFY_REMINDER_HOURS = 6
_CLOSED = ("resolved", "false_positive")
# Une répétition reprend le verdict précédent. L'IA relit l'événement après 5 min, puis 10, 20, 40 et 60 tant que son
# verdict ne change pas : un événement qui évolue reste suivi de près, un événement stable ne l'occupe plus.
REANALYZE_MIN_MINUTES = 5
REANALYZE_MAX_MINUTES = 60
REANALYZE_MEMORY_HOURS = 24
REANALYZE_LOOKBACK = 500


def event_signatures(lines: List[str]) -> set:
    """Signatures qui définissent un événement : ses lignes d'erreur, ou toutes ses lignes s'il n'y en a pas. Le bruit
    autour d'une erreur ne crée pas un autre événement, et une nouvelle ligne d'erreur ne peut pas se cacher dans un
    événement déjà lu."""
    from app.services.keyword_tuning import error_like
    return {line_signature(line) for line in lines if error_like(line)} or {line_signature(line) for line in lines}


def key_of(signatures: set) -> str:
    return hashlib.sha1("\n".join(sorted(signatures)).encode("utf-8", "ignore")).hexdigest()[:16]


def event_key(a: Analysis) -> str:
    return key_of(event_signatures(analysis_lines(a)))


def _set_key(_mapper, _connection, target: Analysis):
    target.event_key = event_key(target)


event.listen(Analysis, "before_insert", _set_key)


def backfill(db):
    """Clé des analyses enregistrées avant le regroupement."""
    rows = db.query(Analysis).filter(Analysis.event_key == None).all()
    for a in rows:
        a.event_key = event_key(a)
    if rows:
        db.commit()


def rekey(db):
    """Le calcul des signatures a changé : sans ça, un même événement aurait une clé avant et une autre après, et
    une note cesserait de s'appliquer. Une note dont toutes les analyses ont été purgées garde ses anciennes
    signatures : ses lignes n'existent plus, rien ne permet de les recalculer."""
    config = db.query(GlobalConfig).first()
    if not config or (config.event_keys_version or 0) >= EVENT_KEY_VERSION:
        return
    renamed = {}
    last_id = 0
    while True:
        rows = db.query(Analysis).filter(Analysis.id > last_id).order_by(Analysis.id).limit(REKEY_BATCH).all()
        if not rows:
            break
        for a in rows:
            signatures = event_signatures(analysis_lines(a))
            new_key = key_of(signatures)
            if new_key != a.event_key:
                renamed[(a.rule_id, a.event_key)] = (new_key, signatures)
                a.event_key = new_key
        last_id = rows[-1].id
        db.commit()
    for note in db.query(EventNote).filter(EventNote.event_key != None).all():
        found = renamed.get((note.rule_id, note.event_key))
        if found:
            note.event_key = found[0]
            note.signatures_json = json.dumps(sorted(found[1]), ensure_ascii=False)
    config.event_keys_version = EVENT_KEY_VERSION
    db.commit()


def reanalyze_delay(real_analyses: List[Analysis]) -> timedelta:
    """Délai avant de solliciter à nouveau l'IA, selon le nombre de verdicts identiques d'affilée (le plus récent d'abord)."""
    stable = 0
    for a in real_analyses:
        if a.severity != real_analyses[0].severity:
            break
        stable += 1
    return timedelta(minutes=min(REANALYZE_MAX_MINUTES, REANALYZE_MIN_MINUTES * 2 ** max(stable - 1, 0)))


def reusable_analysis(db, rule_id: int, lines: List[str], now: Optional[datetime] = None) -> Optional[Analysis]:
    """L'analyse dont le verdict vaut encore pour ces lignes, ou rien s'il faut demander à l'IA : événement nouveau,
    incident clos depuis, analyse précédente échouée, note ajoutée depuis (l'IA doit en tenir compte), délai écoulé."""
    now = now or datetime.utcnow()
    key = key_of(event_signatures(lines))
    rows = db.query(Analysis).filter(Analysis.rule_id == rule_id, Analysis.event_key == key,
                                     Analysis.analyzed_at >= now - timedelta(hours=REANALYZE_MEMORY_HOURS)) \
        .order_by(Analysis.analyzed_at.desc(), Analysis.id.desc()).limit(REANALYZE_LOOKBACK).all()
    if not rows or rows[0].resolution_status in _CLOSED:
        return None
    real = [a for a in rows if a.reused_from is None]
    if not real or (real[0].ollama_response or "").startswith("[Erreur"):
        return None
    last = real[0]
    if now - last.analyzed_at >= reanalyze_delay(real):
        return None
    if db.query(EventNote).filter(EventNote.rule_id == rule_id, EventNote.updated_at > last.analyzed_at).first():
        return None
    return last


def notification_decision(db, analysis: Analysis, now: Optional[datetime] = None) -> Tuple[bool, int]:
    """(notifier ?, répétitions restées silencieuses depuis la dernière notification de cet événement).

    On notifie la première fois, quand c'est plus grave que tout ce qui a déjà été notifié, quand le précédent incident
    était clos (l'utilisateur a été prévenu du retour à la normale), et en rappel si l'événement continue. Une analyse
    dont l'envoi a échoué n'a pas été notifiée : la suivante retente. Un événement signalé « pas pertinent » n'a pas de
    rappel, sauf s'il devient plus grave."""
    now = now or datetime.utcnow()
    same = db.query(Analysis).filter(Analysis.rule_id == analysis.rule_id, Analysis.event_key == analysis.event_key, Analysis.id != analysis.id)
    last_sent = same.filter(Analysis.notified == True).order_by(Analysis.analyzed_at.desc(), Analysis.id.desc()).first()
    if not last_sent:
        return True, 0
    previous = same.order_by(Analysis.analyzed_at.desc(), Analysis.id.desc()).first()
    if previous.resolution_status in _CLOSED:
        return True, 0
    silent = same.filter(Analysis.analyzed_at > last_sent.analyzed_at).count()
    sent_rank = max(_rank(row[0]) for row in same.filter(Analysis.notified == True).with_entities(Analysis.severity).all())
    if _rank(analysis.severity) > sent_rank:
        return True, silent
    if previous.feedback == "irrelevant":
        return False, silent
    return now - last_sent.analyzed_at >= timedelta(hours=NOTIFY_REMINDER_HOURS), silent


def _rank(severity: Optional[str]) -> int:
    return _RANK.get(severity, 0)


def summarize(db, rule_id: Optional[int] = None, days: Optional[int] = None, open_only: bool = False) -> List[dict]:
    """Un résumé par événement, le plus récent d'abord.

    Un événement est non lu tant qu'aucune occurrence n'a été lue, ou si une occurrence non lue est plus grave que tout
    ce qui a été lu : une simple répétition d'un événement déjà vu ne le remet pas en non lu."""
    q = db.query(Analysis.id, Analysis.rule_id, Rule.name, Analysis.event_key, Analysis.severity, Analysis.viewed,
                 Analysis.analyzed_at, Analysis.resolution_status, Analysis.feedback).join(Rule, Analysis.rule_id == Rule.id)
    if rule_id is not None:
        q = q.filter(Analysis.rule_id == rule_id)
    if days:
        q = q.filter(Analysis.analyzed_at >= datetime.utcnow() - timedelta(days=days))
    if open_only:
        q = q.filter(Analysis.resolution_status == None)

    groups = {}
    for row in q.order_by(Analysis.analyzed_at.desc(), Analysis.id.desc()).all():
        key = (row.rule_id, row.event_key)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": row.event_key, "rule_id": row.rule_id, "rule_name": row.name, "count": 0,
                               "last_at": row.analyzed_at, "first_at": row.analyzed_at, "latest_id": row.id,
                               "severities": {}, "read_rank": -1, "unread_rank": -1, "unread_id": None, "open": 0,
                               "flag_rank": -1}
        severity = row.severity if row.severity in SEVERITIES else "info"
        g["count"] += 1
        g["first_at"] = row.analyzed_at
        g["severities"][severity] = g["severities"].get(severity, 0) + 1
        g["open"] += row.resolution_status is None
        if row.feedback == "irrelevant":
            g["flag_rank"] = max(g["flag_rank"], _rank(severity))
        if row.viewed:
            g["read_rank"] = max(g["read_rank"], _rank(severity))
        elif _rank(severity) > g["unread_rank"]:
            g["unread_rank"], g["unread_id"] = _rank(severity), row.id

    events = []
    for g in groups.values():
        unread = g["unread_rank"] > g["read_rank"]
        events.append({
            "key": g["key"], "rule_id": g["rule_id"], "rule_name": g["rule_name"], "count": g["count"],
            "first_at": iso(g["first_at"]), "last_at": iso(g["last_at"]), "severities": g["severities"],
            "severity": max(g["severities"], key=_rank), "unread": unread, "open_count": g["open"],
            # Signalé : reste vrai pour une répétition de même gravité, pas pour une occurrence plus grave
            "flagged": g["flag_rank"] >= 0 and g["flag_rank"] == max(_rank(s) for s in g["severities"]),
            # Ce qui justifie de rouvrir l'événement, sinon sa dernière occurrence
            "analysis_id": g["unread_id"] if unread else g["latest_id"],
        })
    return events


def counts(events: List[dict]) -> dict:
    result = {"total": len(events), "unread": sum(e["unread"] for e in events), "critical": 0, "warning": 0, "info": 0}
    for e in events:
        result[e["severity"]] += 1
    return result


def select(events: List[dict], severity: Optional[str] = None, unread: bool = False) -> List[dict]:
    return [e for e in events if (not severity or e["severity"] == severity) and (not unread or e["unread"])]


def apply_focus(db, events: List[dict], analysis_id: Optional[int]):
    """Un lien vers une occurrence précise (notification, historique) montre cette occurrence dans son événement."""
    if not analysis_id:
        return
    a = db.query(Analysis.rule_id, Analysis.event_key).filter(Analysis.id == analysis_id).first()
    if a:
        for e in events:
            if e["rule_id"] == a.rule_id and e["key"] == a.event_key:
                e["analysis_id"] = analysis_id


def set_feedback(db, analysis: Analysis, value: Optional[str]):
    """Signaler une occurrence, c'est signaler l'événement."""
    db.query(Analysis).filter(Analysis.rule_id == analysis.rule_id, Analysis.event_key == analysis.event_key
                              ).update({Analysis.feedback: value}, synchronize_session=False)


def mark_read(db, analysis: Analysis):
    """Lire une occurrence, c'est lire l'événement."""
    db.query(Analysis).filter(Analysis.rule_id == analysis.rule_id, Analysis.event_key == analysis.event_key,
                              Analysis.viewed == False).update({Analysis.viewed: True}, synchronize_session=False)
