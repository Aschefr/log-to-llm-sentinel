"""
Notes écrites par l'administrateur pour aider l'IA : sur un événement, ou sur toute la source d'une règle.

Le lien avec une alerte est déterministe : une note d'événement s'applique aux alertes qui partagent une de ses lignes.
Une note ne fait taire aucune alerte : elle informe le LLM, qui reste tenu de dire si les lignes la contredisent.
Les notes survivent aux analyses qu'elles commentent (purge, suppression d'un événement) ; elles suivent la règle.
"""
import json
from datetime import datetime
from typing import List, Optional

from app.models import Analysis, EventNote
from app.services.alert_analysis import analysis_lines
from app.services.analysis_events import event_signatures, key_of
from app.services.rule_stats import iso

MAX_CHARS = 500
MAX_PER_RULE = 30
PROMPT_NOTES = 5   # par alerte : les plus proches de l'événement d'abord
RULE_NOTES = 8     # sans alerte précise (arbitre des mots-clés, description du journal)

_SCOPE_ORDER = {"event": 0, "related": 1, "source": 2}


class NoteError(ValueError):
    """Code d'erreur destiné à l'interface."""


def _clean(text) -> str:
    value = str(text or "").strip()
    if not value:
        raise NoteError("note_empty")
    if len(value) > MAX_CHARS:
        raise NoteError("note_too_long")
    return value


def _signatures(note: EventNote) -> set:
    try:
        return set(json.loads(note.signatures_json or "[]"))
    except ValueError:
        return set()


def to_dict(note: EventNote, scope: Optional[str] = None) -> dict:
    signatures = sorted(_signatures(note))
    return {"id": note.id, "rule_id": note.rule_id, "text": note.text, "event_key": note.event_key,
            "scope": scope or ("event" if note.event_key else "source"),
            "line": signatures[0][:160] if signatures else None,
            "created_at": iso(note.created_at), "updated_at": iso(note.updated_at or note.created_at)}


def rule_rows(db, rule_id) -> List[EventNote]:
    return (db.query(EventNote).filter(EventNote.rule_id == rule_id)
            .order_by(EventNote.updated_at.desc(), EventNote.id.desc()).all())


def applicable(rows: List[EventNote], lines: List[str], limit: Optional[int] = None) -> List[dict]:
    """Notes qui concernent ces lignes : le même événement, puis une ligne en commun, puis toute la source."""
    signatures = event_signatures(lines)
    key = key_of(signatures)
    found = []
    for note in rows:
        if not note.event_key:
            found.append(to_dict(note, "source"))
        elif note.event_key == key:
            found.append(to_dict(note, "event"))
        elif signatures & _signatures(note):
            found.append(to_dict(note, "related"))
    found.sort(key=lambda n: _SCOPE_ORDER[n["scope"]])
    return found[:limit] if limit else found


def for_lines(db, rule_id, lines: List[str], limit: Optional[int] = PROMPT_NOTES) -> List[dict]:
    return applicable(rule_rows(db, rule_id), lines, limit)


def for_analysis(db, analysis: Analysis, limit: Optional[int] = PROMPT_NOTES) -> List[dict]:
    return for_lines(db, analysis.rule_id, analysis_lines(analysis), limit)


def for_rule(db, rule_id, source_only: bool = False, limit: Optional[int] = RULE_NOTES) -> List[dict]:
    """Sans alerte précise : la source d'abord, les plus récentes ensuite."""
    notes = [to_dict(n) for n in rule_rows(db, rule_id) if not (source_only and n.event_key)]
    notes.sort(key=lambda n: n["scope"] != "source")
    return notes[:limit] if limit else notes


def create(db, rule_id, text, analysis: Optional[Analysis] = None) -> EventNote:
    value = _clean(text)
    if db.query(EventNote).filter(EventNote.rule_id == rule_id).count() >= MAX_PER_RULE:
        raise NoteError("notes_limit")
    note = EventNote(rule_id=rule_id, text=value)
    if analysis is not None:
        signatures = event_signatures(analysis_lines(analysis))
        note.event_key = analysis.event_key or key_of(signatures)
        note.signatures_json = json.dumps(sorted(signatures), ensure_ascii=False)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def update(db, note: EventNote, text) -> EventNote:
    note.text = _clean(text)
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return note
