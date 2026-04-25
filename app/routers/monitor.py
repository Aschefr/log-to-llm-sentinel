from fastapi import APIRouter, Query
from typing import Optional
import json

from app.database import SessionLocal
from app.models import Rule, Analysis
from app.services.orchestrator import Orchestrator

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# Référence à l'orchestrateur partagé (accès aux buffers en mémoire)
_orchestrator: Optional[Orchestrator] = None

def set_orchestrator(orch: Orchestrator):
    global _orchestrator
    _orchestrator = orch


@router.get("/rules")
def get_monitored_rules():
    """Retourne toutes les règles actives avec leurs métadonnées pour le monitor."""
    db = SessionLocal()
    try:
        rules = db.query(Rule).filter(Rule.enabled == True).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "log_file_path": r.log_file_path,
                "keywords": r.get_keywords(),
                "application_context": r.application_context,
                "anti_spam_delay": r.anti_spam_delay or 60,
                "notify_severity_threshold": r.notify_severity_threshold or "info",
                "notify_on_match": r.notify_on_match,
            }
            for r in rules
        ]
    finally:
        db.close()


@router.get("/buffer/{rule_id}")
def get_buffer_status(rule_id: int):
    """Retourne l'état actuel du buffer anti-spam pour une règle."""
    if _orchestrator is None:
        return {"active": False, "lines": [], "detection_id": None, "matched_keywords": []}

    buf = _orchestrator._buffers.get(rule_id)
    if not buf or not buf.get("task"):
        return {"active": False, "lines": [], "detection_id": None, "matched_keywords": []}

    return {
        "active": True,
        "detection_id": buf.get("detection_id"),
        "line_count": len(buf.get("lines", [])),
        "matched_keywords": list(buf.get("matched_keywords", set())),
        "preview_lines": buf.get("lines", [])[-5:],  # 5 dernières lignes du buffer
    }


@router.get("/analyses/{rule_id}")
def get_rule_analyses(rule_id: int, limit: int = Query(20)):
    """Retourne les analyses récentes d'une règle, enrichies avec detection_id et keywords."""
    db = SessionLocal()
    try:
        analyses = (
            db.query(Analysis)
            .filter(Analysis.rule_id == rule_id)
            .order_by(Analysis.analyzed_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": a.id,
                "detection_id": a.detection_id,
                "triggered_line": a.triggered_line,
                "matched_keywords": json.loads(a.matched_keywords_json or "[]"),
                "severity": a.severity,
                "ollama_response": a.ollama_response,
                "notified": a.notified,
                "analyzed_at": a.analyzed_at.isoformat() if a.analyzed_at else None,
            }
            for a in analyses
        ]
    finally:
        db.close()


@router.get("/search")
def search_by_detection_id(id: str = Query(..., description="ID de détection à rechercher")):
    """Retrouve une analyse par son detection_id."""
    db = SessionLocal()
    try:
        analysis = db.query(Analysis).filter(Analysis.detection_id == id).first()
        if not analysis:
            return {"found": False}

        rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
        return {
            "found": True,
            "analysis": {
                "id": analysis.id,
                "detection_id": analysis.detection_id,
                "rule_id": analysis.rule_id,
                "rule_name": rule.name if rule else f"Règle #{analysis.rule_id}",
                "triggered_line": analysis.triggered_line,
                "matched_keywords": json.loads(analysis.matched_keywords_json or "[]"),
                "severity": analysis.severity,
                "ollama_response": analysis.ollama_response,
                "notified": analysis.notified,
                "analyzed_at": analysis.analyzed_at.isoformat() if analysis.analyzed_at else None,
            }
        }
    finally:
        db.close()
