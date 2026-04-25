from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import json
import asyncio
from datetime import datetime

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
        from app.models import GlobalConfig
        config = db.query(GlobalConfig).first()
        monitor_lines = config.monitor_log_lines if config else 60

        rules = db.query(Rule).filter(Rule.enabled == True).all()
        return {
            "monitor_log_lines": monitor_lines,
            "rules": [
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
        }
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


@router.post("/retry/{analysis_id}")
async def retry_analysis(analysis_id: int):
    """Relance une analyse Ollama échouée."""
    if _orchestrator is None:
        raise HTTPException(status_code=500, detail="Orchestrateur non initialisé")

    db = SessionLocal()
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if not analysis:
            raise HTTPException(status_code=404, detail="Analyse non trouvée")

        rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")

        from app.models import GlobalConfig
        config_obj = db.query(GlobalConfig).first()
        from app.routers.config import _get_config_dict
        config = _get_config_dict(config_obj)

        # On reconstruit le prompt
        prompt = _orchestrator._build_prompt(rule, analysis.triggered_line, config.get("system_prompt", ""))

        # Appel Ollama
        async with _orchestrator._ollama_semaphore:
            response = await asyncio.to_thread(
                _orchestrator.ollama.analyze,
                prompt=prompt,
                url=config.get("ollama_url"),
                model=config.get("ollama_model"),
            )

        from app import logger
        logger.add_ollama_log(prompt, response)

        severity = _orchestrator._detect_severity(response)

        # Mise à jour de l'analyse
        analysis.ollama_response = response
        analysis.severity = severity
        analysis.analyzed_at = datetime.utcnow()
        db.commit()
        db.refresh(analysis)

        return {
            "status": "ok",
            "analysis": {
                "id": analysis.id,
                "ollama_response": analysis.ollama_response,
                "severity": analysis.severity,
                "analyzed_at": analysis.analyzed_at.isoformat()
            }
        }
    finally:
        db.close()
