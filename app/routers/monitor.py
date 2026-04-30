from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional
import json
import asyncio
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services.orchestrator import Orchestrator
from app.services.task_manager import task_manager
from app.utils.log_utils import clean_log_line

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# Référence à l'orchestrateur partagé (accès aux buffers en mémoire)
_orchestrator: Optional[Orchestrator] = None

def set_orchestrator(orch: Orchestrator):
    global _orchestrator
    _orchestrator = orch


@router.post("/notify/{analysis_id}")
async def notify_analysis(analysis_id: int):
    """Envoie manuellement une notification pour une analyse."""
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
        from app.routers.config import _get_config_dict
        config_obj = db.query(GlobalConfig).first()
        if not config_obj:
            raise HTTPException(status_code=500, detail="Configuration globale non trouvée")
            
        config = _get_config_dict(config_obj)
        
        await _orchestrator.trigger_notification(analysis, rule, config, db)
        
        return {"status": "ok", "message": "Notification envoyée"}
    finally:
        db.close()


@router.get("/rules")
def get_monitored_rules():
    """Retourne toutes les règles actives avec leurs métadonnées pour le monitor."""
    db = SessionLocal()
    try:
        from app.models import GlobalConfig
        config = db.query(GlobalConfig).first()
        monitor_lines = config.monitor_log_lines if config else 60

        from sqlalchemy import func
        from app.models import Analysis
        
        # Récupération des statistiques par règle
        stats_query = db.query(
            Analysis.rule_id,
            Analysis.severity,
            func.count(Analysis.id).label("count")
        ).group_by(Analysis.rule_id, Analysis.severity).all()
        
        stats_dict = {}
        for rule_id, severity, count in stats_query:
            if rule_id not in stats_dict:
                stats_dict[rule_id] = {"total": 0, "info": 0, "warning": 0, "critical": 0}
            if severity in stats_dict[rule_id]:
                stats_dict[rule_id][severity] += count
            stats_dict[rule_id]["total"] += count

        # Récupération de la dernière analyse par règle
        last_analysis_query = db.query(
            Analysis.rule_id,
            func.max(Analysis.analyzed_at).label("last_analyzed_at")
        ).group_by(Analysis.rule_id).all()
        last_analysis_dict = {row.rule_id: row.last_analyzed_at for row in last_analysis_query}

        rules = db.query(Rule).filter(Rule.enabled == True).all()
        return {
            "monitor_log_lines": monitor_lines,
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "log_file_path": r.log_file_path,
                    "keywords": r.get_keywords(),
                    "excluded_patterns": r.get_excluded_patterns(),
                    "application_context": r.application_context,
                    "anti_spam_delay": r.anti_spam_delay or 60,
                    "notify_severity_threshold": r.notify_severity_threshold or "info",
                    "notify_on_match": r.notify_on_match,
                    "stats": stats_dict.get(r.id, {"total": 0, "info": 0, "warning": 0, "critical": 0}),
                    "last_learning_session_id": r.last_learning_session_id,
                    "last_line_received_at": r.last_line_received_at.isoformat() + "Z" if r.last_line_received_at else None,
                    "last_analysis_at": last_analysis_dict.get(r.id).isoformat() + "Z" if last_analysis_dict.get(r.id) else None,
                    "inactivity_warning_enabled": r.inactivity_warning_enabled,
                    "inactivity_period_hours": r.inactivity_period_hours,
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
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        last_line_received_at = rule.last_line_received_at.isoformat() + "Z" if rule and rule.last_line_received_at else None
        inactivity_warning_enabled = rule.inactivity_warning_enabled if rule else False
        inactivity_period_hours = rule.inactivity_period_hours if rule else 12
        inactivity_notify = rule.inactivity_notify if rule else True
        
        from sqlalchemy import func
        from app.models import Analysis
        last_analysis = db.query(func.max(Analysis.analyzed_at)).filter(Analysis.rule_id == rule_id).scalar()
        last_analysis_at = last_analysis.isoformat() + "Z" if last_analysis else None
    finally:
        db.close()

    if not buf or not buf.get("task"):
        return {
            "active": False, "lines": [], "detection_id": None, "matched_keywords": [],
            "last_line_received_at": last_line_received_at,
            "last_analysis_at": last_analysis_at,
            "inactivity_warning_enabled": inactivity_warning_enabled,
            "inactivity_period_hours": inactivity_period_hours,
            "inactivity_notify": inactivity_notify
        }

    return {
        "active": True,
        "detection_id": buf.get("detection_id"),
        "line_count": len(buf.get("lines", [])),
        "matched_keywords": list(buf.get("matched_keywords", set())),
        "preview_lines": buf.get("lines", [])[-5:],  # 5 dernières lignes du buffer
        "last_line_received_at": last_line_received_at,
        "last_analysis_at": last_analysis_at,
        "inactivity_warning_enabled": inactivity_warning_enabled,
        "inactivity_period_hours": inactivity_period_hours,
        "inactivity_notify": inactivity_notify
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
    """
    Relance une analyse Ollama en arrière-plan (fire & forget).
    Retourne immédiatement un task_id. Le client pollingue GET /task/{task_id}.
    """
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
        from app.routers.config import _get_config_dict
        config_obj = db.query(GlobalConfig).first()
        if not config_obj:
            raise HTTPException(status_code=500, detail="Configuration globale non trouvée")
            
        config = _get_config_dict(config_obj)
        cleaned_line = clean_log_line(analysis.triggered_line)
        prompt = _orchestrator._build_prompt(rule, cleaned_line, config.get("system_prompt", ""))

        # Capturer les IDs nécessaires pour la tâche en arrière-plan
        analysis_id_captured = analysis.id
        detection_id = analysis.detection_id

        # Créer l'entrée dans le gestionnaire de tâches
        entry = task_manager.create_analysis_task()

        async def _do_retry():
            try:
                async with _orchestrator._ollama_semaphore:
                    try:
                        response = await asyncio.wait_for(
                            _orchestrator.ollama.analyze_async(
                                prompt=prompt,
                                url=config.get("ollama_url"),
                                model=config.get("ollama_model"),
                                think=config.get("ollama_think", True),
                                options={
                                    "temperature": config.get("ollama_temp", 0.1),
                                    "num_ctx": config.get("ollama_ctx", 4096)
                                }
                            ),
                            timeout=300.0
                        )
                    except asyncio.TimeoutError:
                        response = "[Erreur Ollama] Délai d'attente dépassé (300s)"

                from app import logger
                logger.add_ollama_log(prompt, response, detection_id)
                severity = _orchestrator._detect_severity(response)

                retry_db = SessionLocal()
                try:
                    a = retry_db.query(Analysis).filter(Analysis.id == analysis_id_captured).first()
                    if a:
                        a.ollama_response = response
                        a.severity = severity
                        a.analyzed_at = datetime.utcnow()
                        retry_db.commit()
                        entry.analysis_id = a.id
                finally:
                    retry_db.close()

                entry.status = "done"
            except Exception as e:
                entry.error = str(e)
                entry.status = "error"

        asyncio.create_task(_do_retry())
        return {"status": "pending", "task_id": entry.task_id}
    finally:
        db.close()


@router.get("/task/{task_id}")
def get_task_status(task_id: str):
    """Retourne le statut d'une tâche d'analyse en arrière-plan."""
    entry = task_manager.get_analysis_task(task_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Tâche non trouvée")
    return {
        "status": entry.status,
        "analysis_id": entry.analysis_id,
        "error": entry.error,
    }
        
@router.post("/analyze-line")
async def analyze_line(data: dict):
    """
    Effectue une analyse manuelle d'une ligne spécifique en arrière-plan.
    Retourne immédiatement un task_id. Le client pollingue GET /task/{task_id}.
    """
    line = data.get("line")
    rule_id = data.get("rule_id")
    
    if not line or not rule_id:
        raise HTTPException(status_code=400, detail="Données manquantes")
        
    if _orchestrator is None:
        raise HTTPException(status_code=500, detail="Orchestrateur non initialisé")

    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            db.close()
            raise HTTPException(status_code=404, detail="Règle non trouvée")
            
        cfg = db.query(GlobalConfig).first()
        config = {
            "ollama_url":   cfg.ollama_url   if cfg else "http://ollama:11434",
            "ollama_model": cfg.ollama_model if cfg else "qwen3.5:0.8b",
            "ollama_temp":  cfg.ollama_temp  if cfg else 0.1,
            "ollama_ctx":   cfg.ollama_ctx   if cfg else 4096,
            "ollama_think": cfg.ollama_think if cfg else True,
            "system_prompt": cfg.system_prompt if cfg else ""
        }
        rule_id_cap = rule.id
        cleaned_line = clean_log_line(line)
        prompt = _orchestrator._build_prompt(rule, cleaned_line, config.get("system_prompt", ""))
    finally:
        db.close()

    entry = task_manager.create_analysis_task()

    async def _do_analyze():
        try:
            async with _orchestrator._ollama_semaphore:
                try:
                    response = await asyncio.wait_for(
                        _orchestrator.ollama.analyze_async(
                            prompt=prompt,
                            url=config.get("ollama_url"),
                            model=config.get("ollama_model"),
                            think=config.get("ollama_think", True),
                            options={
                                "temperature": config.get("ollama_temp", 0.1),
                                "num_ctx":    config.get("ollama_ctx", 4096)
                            }
                        ),
                        timeout=300.0
                    )
                except asyncio.TimeoutError:
                    response = "[Erreur Ollama] Délai d'attente dépassé (300s)"

            from app import logger
            import uuid
            logger.add_ollama_log(prompt, response, "MANUAL")
            severity = _orchestrator._detect_severity(response)
            det_id = f"MANUAL-{uuid.uuid4().hex[:8]}"

            save_db = SessionLocal()
            try:
                analysis = Analysis(
                    rule_id=rule_id_cap,
                    detection_id=det_id,
                    triggered_line=line,
                    ollama_response=response,
                    severity=severity,
                    analyzed_at=datetime.utcnow(),
                )
                save_db.add(analysis)
                save_db.commit()
                save_db.refresh(analysis)
                entry.analysis_id = analysis.id
            finally:
                save_db.close()

            entry.status = "done"
        except Exception as e:
            entry.error = str(e)
            entry.status = "error"

    asyncio.create_task(_do_analyze())
    return {"status": "pending", "task_id": entry.task_id}

@router.post("/chat")
async def chat_analysis(data: dict, request: Request):
    """Continue la conversation sur une analyse."""
    analysis_id = data.get("analysis_id")
    question = data.get("question")
    context_prompt = data.get("context_prompt")  # Pour les analyses manuelles non sauvegardées
    context_response = data.get("context_response")
    
    if not question:
        raise HTTPException(status_code=400, detail="Question manquante")
        
    db = SessionLocal()
    try:
        prompt = ""
        if analysis_id and str(analysis_id).isdigit():
            analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if not analysis:
                raise HTTPException(status_code=404, detail="Analyse non trouvée")
            rule = db.query(Rule).filter(Rule.id == analysis.rule_id).first()
            cfg = db.query(GlobalConfig).first()
            
            # Reconstruire le prompt original (ou le stocker en BDD ?)
            # Ici on va construire un prompt de chat
            base_prompt = _orchestrator._build_prompt(rule, analysis.triggered_line, cfg.system_prompt if cfg else "")
            prompt = f"{base_prompt}\n\nTa réponse précédente :\n{analysis.ollama_response}\n\nQuestion de l'utilisateur : {question}"
        else:
            # Mode manuel ou contextuel
            prompt = f"Contexte de l'analyse :\n{context_prompt}\n\nRéponse précédente :\n{context_response}\n\nQuestion de l'utilisateur : {question}"

        cfg = db.query(GlobalConfig).first()
        if not cfg:
            raise HTTPException(status_code=500, detail="Configuration non trouvée")

        from app.routers.utils import cancel_on_disconnect
        async with _orchestrator._ollama_semaphore:
            try:
                coro = _orchestrator.ollama.analyze_async(
                    prompt=prompt,
                    url=cfg.ollama_url,
                    model=cfg.ollama_model,
                    think=cfg.ollama_think if hasattr(cfg, 'ollama_think') else True,
                    options={
                        "temperature": cfg.ollama_temp if hasattr(cfg, 'ollama_temp') else 0.1,
                        "num_ctx": cfg.ollama_ctx if hasattr(cfg, 'ollama_ctx') else 4096
                    }
                )
                response = await cancel_on_disconnect(
                    request,
                    asyncio.wait_for(coro, timeout=300.0)
                )
            except asyncio.TimeoutError:
                response = "[Erreur Ollama] Délai d'attente dépassé (300s)"
            
        from app import logger
        logger.add_ollama_log(f"CHAT: {question}", response, "CHAT")
        
        return {"status": "ok", "response": response}
    finally:
        db.close()
