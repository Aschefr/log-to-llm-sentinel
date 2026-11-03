from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
import os
import time
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None

from app.database import SessionLocal
from app.models import GlobalConfig, Rule, Analysis
from app.services import analysis_events, dashboard_watch, rule_stats
from app.services.app_config import config_to_dict
from app.services.alert_analysis import short_summary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

START_TIME = time.time()
_orchestrator = None


def set_orchestrator(orchestrator):
    global _orchestrator
    _orchestrator = orchestrator


@router.get("/system-stats")
def get_system_stats():
    """Retourne les statistiques d'utilisation des ressources."""
    stats = {
        "app_cpu": 0,
        "app_ram": 0,
        "sys_cpu": 0,
        "sys_ram": 0,
        "uptime": int(time.time() - START_TIME)
    }
    
    if psutil:
        try:
            process = psutil.Process(os.getpid())
            stats["app_cpu"] = process.cpu_percent(interval=None) # Use None to not block
            stats["app_ram"] = round(process.memory_info().rss / (1024 * 1024), 1)
            stats["sys_cpu"] = psutil.cpu_percent()
            stats["sys_ram"] = psutil.virtual_memory().percent
        except:
            pass
            
    return stats


@router.get("/watch")
def get_watch():
    """Poste de veille : ce qui demande une action, tendance, activité du pilote automatique."""
    db = SessionLocal()
    try:
        return dashboard_watch.build(db)
    finally:
        db.close()


@router.get("/ollama")
def get_ollama_status():
    """État d'Ollama, à part : un serveur injoignable ne doit pas retarder le reste de la page."""
    db = SessionLocal()
    try:
        config = config_to_dict(db.query(GlobalConfig).first())
    finally:
        db.close()
    return dashboard_watch.ollama_status(config, _orchestrator)


@router.get("/stats")
def get_stats():
    db = SessionLocal()
    try:
        total_rules = db.query(Rule).count()
        active_rules = db.query(Rule).filter(Rule.enabled == True).count()
        total_analyses = db.query(Analysis).count()

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_analyses = db.query(Analysis).filter(Analysis.analyzed_at >= today).count()

        critical_count = db.query(Analysis).filter(Analysis.severity == "critical").count()
        warning_count = db.query(Analysis).filter(Analysis.severity == "warning").count()
        info_count = db.query(Analysis).filter(Analysis.severity == "info").count()

        # MTTR Calculation (MON-18)
        resolved_analyses = db.query(Analysis).filter(
            Analysis.resolution_status == "resolved",
            Analysis.resolved_at != None,
            Analysis.exclude_from_mttr != True
        ).all()
        mttr_minutes = 0.0
        if resolved_analyses:
            total_duration = 0.0
            count_resolved = 0
            for a in resolved_analyses:
                if a.resolved_at and a.analyzed_at:
                    duration = (a.resolved_at - a.analyzed_at).total_seconds()
                    total_duration += duration
                    count_resolved += 1
            if count_resolved > 0:
                mttr_minutes = round((total_duration / count_resolved) / 60.0, 1)

        return {
            "total_rules": total_rules,
            "active_rules": active_rules,
            "total_analyses": total_analyses,
            "today_analyses": today_analyses,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "mttr_minutes": mttr_minutes,
        }
    finally:
        db.close()


def _analysis_dict(a: Analysis, rule_name: str, recurrence: dict) -> dict:
    import json as _json
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "rule_name": rule_name or f"Règle #{a.rule_id}",
        "triggered_line": a.triggered_line,
        "ollama_response": a.ollama_response,
        "severity": a.severity,
        "detection_id": a.detection_id,
        "viewed": a.viewed,
        "analyzed_at": a.analyzed_at.isoformat() if a.analyzed_at else None,
        "matched_keywords": _json.loads(a.matched_keywords_json) if a.matched_keywords_json else [],
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "resolution_status": a.resolution_status,
        "feedback": a.feedback,
        "resolution_line": a.resolution_line,
        "resolution_patterns": _json.loads(a.resolution_patterns_json) if a.resolution_patterns_json else [],
        "resolution_ai_explanation": a.resolution_ai_explanation,
        "resolution_ai_confidence": a.resolution_ai_confidence,
        "summary": short_summary(a, 220) if (a.summary or a.ollama_response) else None,
        "recurrence_7d": recurrence.get(a.id, 0),
        "resolution_minutes": rule_stats.resolution_minutes(a),
    }


@router.get("/events")
def get_events(limit: int = 20, offset: int = 0, rule_id: int | None = None, severity: str | None = None,
               unread: bool = False, open_only: bool = False, days: int | None = None, focus: int | None = None):
    """Analyses regroupées par événement : une ligne par événement, avec le nombre de répétitions."""
    db = SessionLocal()
    try:
        events = analysis_events.summarize(db, rule_id=rule_id, days=days, open_only=open_only)
        analysis_events.apply_focus(db, events, focus)
        selected = analysis_events.select(events, severity=severity, unread=unread)
        page = selected[offset:offset + limit]
        by_id = {a.id: a for a in db.query(Analysis).filter(Analysis.id.in_([e["analysis_id"] for e in page])).all()}
        for e in page:
            e["analysis"] = _analysis_dict(by_id[e["analysis_id"]], e["rule_name"], {})
        return {"events": page, "has_more": offset + limit < len(selected), "total": len(selected),
                "counts": analysis_events.counts(events)}
    finally:
        db.close()


@router.delete("/events/{rule_id}/{key}")
def delete_event(rule_id: int, key: str):
    """Supprime toutes les occurrences d'un événement."""
    db = SessionLocal()
    try:
        deleted = db.query(Analysis).filter(Analysis.rule_id == rule_id, Analysis.event_key == key).delete()
        db.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="event_not_found")
        return {"status": "ok", "deleted": deleted}
    finally:
        db.close()


@router.get("/recent")
def get_recent_analyses(limit: int = 10, offset: int = 0, rule_id: int | None = None, severity: str | None = None,
                        unread: bool = False, open_only: bool = False, days: int | None = None, event_key: str | None = None):
    db = SessionLocal()
    try:
        # Jointure explicite pour récupérer l'objet Analysis et le nom de la Règle associée
        q = db.query(Analysis, Rule.name).join(Rule, Analysis.rule_id == Rule.id)
        
        if rule_id is not None:
            q = q.filter(Analysis.rule_id == rule_id)
        if event_key:
            q = q.filter(Analysis.event_key == event_key)
        if days:
            q = q.filter(Analysis.analyzed_at >= datetime.utcnow() - timedelta(days=days))
        if open_only:
            q = q.filter(Analysis.resolution_status == None)
        # Compteurs des filtres (avant filtre sévérité / non lues), pour l'onglet Analyses d'une règle
        counts = None
        if rule_id is not None:
            from sqlalchemy import func
            counts = {"total": q.count(), "unread": q.filter(Analysis.viewed == False).count()}
            for sev, n in q.with_entities(Analysis.severity, func.count(Analysis.id)).group_by(Analysis.severity).all():
                counts[sev or "info"] = counts.get(sev or "info", 0) + n
        if severity:
            q = q.filter(Analysis.severity == severity)
        if unread:
            q = q.filter(Analysis.viewed == False)

        total = q.count()
        results = q.order_by(Analysis.analyzed_at.desc()).offset(offset).limit(limit).all()
        
        recurrence = rule_stats.recurrence_counts(db, [a for a, _ in results])
        analyses = [_analysis_dict(a, rule_name, recurrence) for a, rule_name in results]
        return {"analyses": analyses, "has_more": (offset + limit) < total, "total": total, "counts": counts}
    finally:
        db.close()
@router.delete("/analyses/{analysis_id}")
def delete_analysis(analysis_id: int):
    db = SessionLocal()
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis:
            db.delete(analysis)
            db.commit()
            return {"status": "ok"}
        raise HTTPException(status_code=404, detail="Analysis not found")
    finally:
        db.close()


@router.delete("/analyses/rule/{rule_id}")
def delete_rule_analyses(rule_id: int):
    db = SessionLocal()
    try:
        db.query(Analysis).filter(Analysis.rule_id == rule_id).delete()
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/analyses/all/confirm")
def delete_all_analyses():
    db = SessionLocal()
    try:
        db.query(Analysis).delete()
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/reset-mttr")
def reset_mttr():
    db = SessionLocal()
    try:
        db.query(Analysis).filter(Analysis.resolution_status == "resolved").update(
            {Analysis.exclude_from_mttr: True},
            synchronize_session=False
        )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()

