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
from app.models import Rule, Analysis

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

START_TIME = time.time()


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


@router.get("/recent")
def get_recent_analyses(limit: int = 10, offset: int = 0, rule_id: int | None = None, severity: str | None = None):
    db = SessionLocal()
    try:
        # Jointure explicite pour récupérer l'objet Analysis et le nom de la Règle associée
        q = db.query(Analysis, Rule.name).join(Rule, Analysis.rule_id == Rule.id)
        
        if rule_id is not None:
            q = q.filter(Analysis.rule_id == rule_id)
        if severity:
            q = q.filter(Analysis.severity == severity)

        total = q.count()
        results = q.order_by(Analysis.analyzed_at.desc()).offset(offset).limit(limit).all()
        
        import json as _json
        analyses = [
            {
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
                "resolution_line": a.resolution_line,
                "resolution_patterns": _json.loads(a.resolution_patterns_json) if a.resolution_patterns_json else [],
                "resolution_ai_explanation": a.resolution_ai_explanation,
                "resolution_ai_confidence": a.resolution_ai_confidence,
            }
            for a, rule_name in results
        ]
        return {"analyses": analyses, "has_more": (offset + limit) < total, "total": total}
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

