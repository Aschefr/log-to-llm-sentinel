from fastapi import APIRouter
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Rule, Analysis

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


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

        return {
            "total_rules": total_rules,
            "active_rules": active_rules,
            "total_analyses": total_analyses,
            "today_analyses": today_analyses,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
        }
    finally:
        db.close()


@router.get("/recent")
def get_recent_analyses(limit: int = 10, rule_id: int | None = None):
    db = SessionLocal()
    try:
        q = db.query(Analysis)
        if rule_id is not None:
            q = q.filter(Analysis.rule_id == rule_id)

        analyses = q.order_by(Analysis.analyzed_at.desc()).limit(limit).all()
        return [
            {
                "id": a.id,
                "rule_id": a.rule_id,
                "triggered_line": a.triggered_line,
                "ollama_response": a.ollama_response,
                "severity": a.severity,
                "analyzed_at": a.analyzed_at.isoformat() if a.analyzed_at else None,
            }
            for a in analyses
        ]
    finally:
        db.close()
