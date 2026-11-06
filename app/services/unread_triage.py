"""Tri des événements non lus par l'IA, sur demande : elle propose, l'utilisateur valide, et le tri s'annule.
Un événement critique n'est jamais proposé, quoi que réponde l'IA. Un événement récent lui est soumis comme tel, avec une
règle plus stricte (pas d'historique pour juger). Marquer comme lu ne ferme aucun incident."""
import asyncio
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from app import logger
from app.database import SessionLocal
from app.models import Analysis, Rule
from app.services import analysis_events, event_notes
from app.services.alert_analysis import analysis_lines, short_summary
from app.services.app_config import load_config_dict
from app.services.prompts import TRIAGE_SCHEMA, TRIAGE_SYSTEM, triage_label
from app.services.rule_stats import iso
from app.services.log_guard import fence
from app.utils.log_utils import clean_log_line

BATCH_EVENTS = 15
MAX_EVENTS = 150
NEW_EVENT_HOURS = 24
SEEN_ONCE_DAYS = 7
REUSE_MINUTES = 10
UNDO_DAYS = 7
REASON_CHARS = 240

_job: Optional[dict] = None
_task: Optional[asyncio.Task] = None


def protection(event: dict, now: Optional[datetime] = None) -> Optional[str]:
    """Pourquoi cet événement reste à lire quoi qu'il arrive : critique."""
    return "critical" if event["severity"] == "critical" else None


def is_recent(event: dict, now: Optional[datetime] = None) -> bool:
    """Pas encore d'historique pour juger : tout neuf, ou vu une seule fois il y a peu. L'IA le sait et l'applique."""
    age = (now or datetime.utcnow()) - datetime.fromisoformat(event["first_at"].rstrip("Z"))
    return age < timedelta(hours=NEW_EVENT_HOURS) or (event["count"] < 2 and age < timedelta(days=SEEN_ONCE_DAYS))


def _item(db, event: dict, reason: str = "", kind: str = "") -> dict:
    a = db.get(Analysis, event["analysis_id"])
    lines = analysis_lines(a) if a else []
    summary = short_summary(a, 220) if a and (a.summary or a.ollama_response) else ""
    return {
        "rule_id": event["rule_id"], "rule_name": event["rule_name"], "key": event["key"], "analysis_id": event["analysis_id"],
        "severity": event["severity"], "count": event["count"], "first_at": event["first_at"], "last_at": event["last_at"],
        "open_count": event["open_count"], "summary": summary or (clean_log_line(lines[0])[:220] if lines else ""),
        "reason": reason, "kind": kind, "recent": bool(event.get("recent")),
    }


def build_messages(db, lang: str, events: List[dict]) -> list:
    L = lambda key, **kw: triage_label(lang, key, **kw)
    parts = []
    contexts = {}
    for n, e in enumerate(events, 1):
        a = db.get(Analysis, e["analysis_id"])
        rule = db.get(Rule, e["rule_id"])
        if rule and rule.application_context and rule.id not in contexts:
            contexts[rule.id] = f"{rule.name} : {' '.join(rule.application_context.split())[:300]}"
        facts = [L("event", n=n, rule=e["rule_name"], severity=e["severity"], count=e["count"],
                   first=e["first_at"][:10], last=e["last_at"][:10])]
        if e["open_count"]:
            facts.append(L("open", count=e["open_count"]))
        if e["flagged"]:
            facts.append(L("flagged"))
        if e.get("recent"):
            facts.append(L("recent"))
        block = [" — ".join(facts), L("summary", text=short_summary(a, 300))]
        block += [L("line", text=fence(clean_log_line(line)[:300])) for line in analysis_lines(a)[:3]]
        # Une note sur toute la source n'a pas été écrite pour cet événement : l'IA doit le savoir pour ne pas l'étendre
        block += [L("note_source" if note["scope"] == "source" else "note", text=" ".join(note["text"].split())[:300])
                  for note in event_notes.for_analysis(db, a, limit=3)]
        parts.append("\n".join(block))
    head = [L("rule_context", text=text) for text in contexts.values()]
    return [{"role": "system", "content": TRIAGE_SYSTEM.get(lang, TRIAGE_SYSTEM["fr"])},
            {"role": "user", "content": "\n".join(head + ["", L("intro")]) + "\n\n" + "\n\n".join(parts)}]


def start(rule_id: Optional[int], tuning) -> dict:
    """Démarre un tri, ou retrouve celui en cours ou tout juste terminé : fermer la fenêtre n'oblige pas à recommencer."""
    global _job, _task
    now = datetime.utcnow()
    if _job and _job["rule_id"] == rule_id and not _job["applied"]:
        recent = now - _job["started_at"] < timedelta(minutes=REUSE_MINUTES)
        if _job["status"] == "running" or (_job["status"] == "done" and recent and _job["total"]):
            return _job
    if _job and _job["status"] == "running":
        cancel(_job["id"])

    db = SessionLocal()
    try:
        unread = analysis_events.select(analysis_events.summarize(db, rule_id), unread=True)
        kept, eligible = [], []
        for e in unread:
            why = protection(e, now)
            if why:
                kept.append(_item(db, e, kind=why))
            else:
                e["recent"] = is_recent(e, now)
                eligible.append(e)
    finally:
        db.close()
    eligible.sort(key=lambda e: -e["count"])  # les plus répétés d'abord : c'est là que le tri fait gagner le plus
    _job = {"id": uuid.uuid4().hex, "rule_id": rule_id, "status": "running", "error": None, "started_at": now,
            "at": iso(now), "applied": False, "unread": len(unread), "total": len(eligible[:MAX_EVENTS]), "done": 0,
            "not_reviewed": max(0, len(eligible) - MAX_EVENTS), "proposals": [], "kept": kept}
    if not eligible:
        _job["status"] = "done"
        return _job
    _task = asyncio.create_task(_run(_job, eligible[:MAX_EVENTS], tuning))
    return _job


async def _run(job: dict, events: List[dict], tuning):
    config = load_config_dict()
    lang = config.get("ollama_prompt_lang", "fr")
    answered = 0
    try:
        for start_at in range(0, len(events), BATCH_EVENTS):
            batch = events[start_at:start_at + BATCH_EVENTS]
            db = SessionLocal()
            try:
                messages = build_messages(db, lang, batch)
            finally:
                db.close()
            data = await tuning.ask_arbiter(messages, config, label="triage", schema=TRIAGE_SCHEMA, kind="TRIAGE")
            answered += data is not None
            decisions = {}
            for d in (data or {}).get("decisions") or []:
                if isinstance(d, dict) and isinstance(d.get("id"), int):
                    decisions[d["id"]] = d
            db = SessionLocal()
            try:
                for n, e in enumerate(batch, 1):
                    d = decisions.get(n)
                    reason = " ".join(str((d or {}).get("reason") or "").split())[:REASON_CHARS]
                    # Sans décision claire et motivée, l'événement reste à lire
                    if d and d.get("mark_read") is True and reason:
                        job["proposals"].append(_item(db, e, reason=reason, kind="proposed"))
                    else:
                        job["kept"].append(_item(db, e, reason=reason, kind="ai" if d else "no_answer"))
            finally:
                db.close()
            job["done"] += len(batch)
        job["status"] = "done" if answered else "failed"
        job["error"] = None if answered else "ai_unavailable"
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        raise
    except Exception as e:
        logger.error("Triage", f"Tri des non lues : {e}")
        job["status"], job["error"] = "failed", "internal"


def get(job_id: str) -> Optional[dict]:
    return _job if _job and _job["id"] == job_id else None


def cancel(job_id: str) -> bool:
    if not get(job_id) or _job["status"] != "running":
        return False
    _job["status"] = "cancelled"
    if _task:
        _task.cancel()
    return True


def apply(db, job_id: str, keys: List[dict], now: Optional[datetime] = None) -> dict:
    """Marque comme lus les événements validés, parmi ceux que l'IA a proposés. Les garde-fous sont revérifiés : un
    événement devenu critique depuis la proposition reste à lire, comme ce qui est arrivé après elle."""
    job = get(job_id)
    if not job or job["status"] != "done":
        raise LookupError("triage_expired")
    now = now or datetime.utcnow()
    proposed = {(p["rule_id"], p["key"]) for p in job["proposals"]}
    wanted = {(k.get("rule_id"), k.get("key")) for k in keys if isinstance(k, dict)} & proposed
    current = {(e["rule_id"], e["key"]): e for e in analysis_events.summarize(db, job["rule_id"])}
    marked_events = marked = skipped = 0
    for pair in wanted:
        event = current.get(pair)
        if not event or protection(event, now):
            skipped += 1
            continue
        done = db.query(Analysis).filter(
            Analysis.rule_id == pair[0], Analysis.event_key == pair[1], Analysis.viewed == False,
            Analysis.severity != "critical", Analysis.analyzed_at <= job["started_at"],
        ).update({Analysis.viewed: True, Analysis.triaged_at: now}, synchronize_session=False)
        marked += done
        marked_events += bool(done)
    db.commit()
    job["applied"] = True
    logger.info("Triage", f"{marked_events} événement(s) marqué(s) comme lus par le tri, {skipped} écarté(s)")
    return {"events": marked_events, "occurrences": marked, "skipped": skipped}


def last(db, now: Optional[datetime] = None) -> Optional[dict]:
    """Le dernier tri appliqué, tant qu'on peut encore l'annuler."""
    from sqlalchemy import func
    at = db.query(func.max(Analysis.triaged_at)).scalar()
    if not at or (now or datetime.utcnow()) - at > timedelta(days=UNDO_DAYS):
        return None
    rows = db.query(Analysis.rule_id, Analysis.event_key).filter(Analysis.triaged_at == at).all()
    return {"at": iso(at), "events": len(set(rows)), "occurrences": len(rows)}


def undo(db) -> dict:
    """Remet en non lu tout ce que le dernier tri a marqué."""
    from sqlalchemy import func
    at = db.query(func.max(Analysis.triaged_at)).scalar()
    if not at:
        return {"occurrences": 0}
    done = db.query(Analysis).filter(Analysis.triaged_at == at).update(
        {Analysis.viewed: False, Analysis.triaged_at: None}, synchronize_session=False)
    db.commit()
    return {"occurrences": done}
