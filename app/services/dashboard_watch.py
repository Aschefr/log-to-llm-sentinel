"""
Poste de veille du dashboard : ce qui demande une action, tendance des alertes, activité du pilote automatique,
état d'Ollama.
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from app.models import Analysis, KeywordChange, Rule
from app.services import analysis_events
from app.services import autopilot as ap
from app.services import rule_stats

ACTIVITY_MAX = 6
OLLAMA_CACHE_SECONDS = 20
OLLAMA_TIMEOUT_SECONDS = 3
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

_ollama_cache = {"at": 0.0, "base": None, "models": None}


def _utc(value: Optional[str]) -> Optional[str]:
    """Date ISO sans fuseau enregistrée dans l'état du pilote (toujours UTC)."""
    return value + "Z" if value and not value.endswith("Z") else value


def _alerts(db, rules) -> list:
    alerts = []
    for r in rules:
        if (r.alert_status or "normal") == "normal":
            continue
        opened = db.query(Analysis).filter(Analysis.rule_id == r.id, Analysis.resolution_status == None)
        latest = opened.order_by(Analysis.analyzed_at.desc()).first() \
            or db.query(Analysis).filter(Analysis.rule_id == r.id).order_by(Analysis.analyzed_at.desc()).first()
        alerts.append({
            "rule_id": r.id,
            "rule_name": r.name,
            "status": r.alert_status,
            "since": rule_stats.iso(r.alert_started_at),
            "open_count": opened.count(),
            "analysis": rule_stats.analysis_brief(latest) if latest else None,
        })
    alerts.sort(key=lambda a: (_SEVERITY_RANK.get((a["analysis"] or {}).get("severity"), 3), a["since"] or ""))
    return alerts


def _trend(analyses, now: datetime) -> dict:
    days = rule_stats.daily_counts(analyses, 7, now)
    start = (now - timedelta(days=6)).date()
    previous = sum(1 for a in analyses if a.analyzed_at and start - timedelta(days=7) <= a.analyzed_at.date() < start)
    return {
        "days": days,
        "total": sum(d["critical"] + d["warning"] + d["info"] for d in days),
        "previous_total": previous,
        "mttr30_minutes": rule_stats.median_mttr(analyses),
    }


def _pilot(db, rules) -> dict:
    piloted = [r for r in rules if ap.enabled(r)]
    names = {r.id: r.name for r in piloted}
    activity = []
    items = []
    for r in piloted:
        state = ap.load(r)
        items.append({
            "rule_id": r.id,
            "rule_name": r.name,
            "status": state.get("status"),
            "no_trigger": bool(state.get("no_trigger")),
            "observe_until": _utc(state.get("observe_until")),
            "last_line_at": rule_stats.iso(r.last_line_received_at),
        })
        activity += [{"type": "event", "at": _utc(e.get("at")), "rule_id": r.id, "rule_name": r.name, "text": e.get("text", "")}
                     for e in state.get("events", [])]
    if names:
        changes = db.query(KeywordChange).filter(KeywordChange.rule_id.in_(names), KeywordChange.status.in_(("applied", "dismissed"))) \
            .order_by(KeywordChange.created_at.desc()).limit(ACTIVITY_MAX).all()
        activity += [{"type": "change", "at": rule_stats.iso(c.created_at), "rule_id": c.rule_id, "rule_name": names[c.rule_id],
                      "kind": c.kind, "action": c.action, "value": c.value, "status": c.status} for c in changes]
    activity.sort(key=lambda a: a["at"] or "", reverse=True)
    return {"rules": items, "activity": activity[:ACTIVITY_MAX]}


def build(db) -> dict:
    now = datetime.utcnow()
    rules = db.query(Rule).all()
    enabled = [r for r in rules if r.enabled]
    ids = [r.id for r in rules]
    # Comme la liste des analyses, on ne compte que celles des règles existantes
    analyses = db.query(Analysis).filter(Analysis.analyzed_at >= now - timedelta(days=30), Analysis.rule_id.in_(ids)).all()
    last_analysis = db.query(func.max(Analysis.analyzed_at)).filter(Analysis.rule_id.in_(ids)).scalar()
    return {
        "rules": {
            "total": len(rules),
            "enabled": len(enabled),
            "observing": sum(1 for r in enabled if ap.enabled(r) and ap.load(r).get("status") in ("learning", "observing")),
        },
        "last_analysis_at": rule_stats.iso(last_analysis),
        "attention": {
            "alerts": _alerts(db, enabled),
            "muted": [{"rule_id": r.id, "rule_name": r.name, "last_line_at": rule_stats.iso(r.last_line_received_at),
                       "limit_hours": r.inactivity_period_hours} for r in enabled if rule_stats.is_muted(r, now)],
            "pilot_errors": [{"rule_id": r.id, "rule_name": r.name} for r in enabled
                             if ap.enabled(r) and ap.load(r).get("status") == "error" and ap.load(r).get("error") != "cancelled"],
        },
        "counts": analysis_events.counts(analysis_events.summarize(db)),
        "trend": _trend(analyses, now),
        "pilot": _pilot(db, enabled),
    }


def _ollama_base(url: str) -> str:
    base = url.strip().rstrip("/")
    for suffix in ("/api/generate", "/api"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _ollama_models(base: str) -> Optional[list]:
    """Modèles installés, ou None si le serveur ne répond pas. Le résultat est gardé quelques secondes : la page
    interroge régulièrement et un serveur injoignable fait attendre jusqu'à la fin du délai."""
    if _ollama_cache["base"] == base and time.monotonic() - _ollama_cache["at"] < OLLAMA_CACHE_SECONDS:
        return _ollama_cache["models"]
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=OLLAMA_TIMEOUT_SECONDS) as r:
            models = [m.get("name") for m in json.loads(r.read().decode("utf-8", errors="ignore")).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        models = None
    _ollama_cache.update(at=time.monotonic(), base=base, models=models)
    return models


def ollama_status(config: dict, orchestrator) -> dict:
    url, model = (config.get("ollama_url") or "").strip(), config.get("ollama_model") or ""
    load = orchestrator.ollama_load() if orchestrator else {"running": 0, "waiting": 0}
    if not url:
        return {"state": "not_configured", "model": model, **load}
    models = _ollama_models(_ollama_base(url))
    if models is None:
        return {"state": "unreachable", "model": model, **load}
    return {"state": "ok" if model in models else "model_missing", "model": model, **load}
