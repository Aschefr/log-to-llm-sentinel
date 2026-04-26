from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
import json

from app.database import SessionLocal
from app.models import MetaAnalysisConfig, MetaAnalysisResult, GlobalConfig
from app.services.meta_service import MetaAnalysisService

router = APIRouter(prefix="/api/meta-analysis", tags=["meta-analysis"])

# Suivi des analyses en cours (config_id -> True)
_running_configs: set = set()
_cancel_requests: set = set()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---- CONFIGS ----

@router.get("/configs")
async def list_configs(db: Session = Depends(get_db)):
    configs = db.query(MetaAnalysisConfig).order_by(MetaAnalysisConfig.id.desc()).all()
    return [{
        "id": c.id,
        "name": c.name,
        "rule_ids_json": json.loads(c.rule_ids_json) if c.rule_ids_json else [],
        "schedule_type": c.schedule_type,
        "schedule_time": c.schedule_time,
        "schedule_day": c.schedule_day,
        "enabled": c.enabled,
        "notify_enabled": c.notify_enabled,
        "context_size": c.context_size,
        "system_prompt": c.system_prompt,
        "max_analyses": c.max_analyses,
        "last_run_at": c.last_run_at.isoformat() + 'Z' if c.last_run_at else None
    } for c in configs]

@router.post("/configs")
async def create_config(data: dict, db: Session = Depends(get_db)):
    try:
        rule_ids = data.get("rule_ids", [])
        from datetime import datetime
        config = MetaAnalysisConfig(
            name=data.get("name", "Nouvelle Méta-Analyse"),
            rule_ids_json=json.dumps(rule_ids),
            schedule_type=data.get("schedule_type", "daily"),
            schedule_time=data.get("schedule_time", "00:00"),
            schedule_day=int(data.get("schedule_day", 1)),
            enabled=data.get("enabled", True),
            notify_enabled=data.get("notify_enabled", True),
            context_size=int(data.get("context_size", 16384)),
            system_prompt=data.get("system_prompt", "Tu es un expert DevOps. Analyse ces événements et fais une synthèse globale de la situation du service."),
            max_analyses=int(data.get("max_analyses", 50)),
            last_run_at=None  # Pas de fenêtre vide - sera calculée selon schedule_type
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        return {"status": "ok", "id": config.id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/configs/{config_id}")
async def update_config(config_id: int, data: dict, db: Session = Depends(get_db)):
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config non trouvée")
    
    try:
        if "name" in data: config.name = data["name"]
        if "rule_ids" in data: config.rule_ids_json = json.dumps(data["rule_ids"])
        if "schedule_type" in data: config.schedule_type = data["schedule_type"]
        if "schedule_time" in data: config.schedule_time = data["schedule_time"]
        if "schedule_day" in data: config.schedule_day = int(data["schedule_day"])
        if "enabled" in data: config.enabled = bool(data["enabled"])
        if "notify_enabled" in data: config.notify_enabled = bool(data["notify_enabled"])
        if "context_size" in data: config.context_size = int(data["context_size"])
        if "system_prompt" in data: config.system_prompt = data["system_prompt"]
        if "max_analyses" in data: config.max_analyses = int(data["max_analyses"])
        if "last_run_at" in data and data["last_run_at"]:
            from datetime import datetime as dt
            config.last_run_at = dt.fromisoformat(data["last_run_at"].replace('Z', '+00:00')).replace(tzinfo=None)

        db.commit()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/configs/{config_id}")
async def delete_config(config_id: int, db: Session = Depends(get_db)):
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config non trouvée")
    # SQLite n'applique pas les FKs par défaut — on supprime manuellement les résultats liés
    db.query(MetaAnalysisResult).filter(MetaAnalysisResult.config_id == config_id).delete()
    db.delete(config)
    db.commit()
    return {"status": "ok"}


# ---- TRIGGER ----

@router.get("/trigger/preview/{config_id}")
async def preview_meta_analysis(config_id: int, db: Session = Depends(get_db)):
    """
    Retourne le contexte exact en attente d'envoi.
    """
    from app.main import meta_service
    result = await meta_service.get_pending_context(config_id)
    if result["status"] != "ok":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/trigger/{config_id}")
async def trigger_meta_analysis(config_id: int, background_tasks: BackgroundTasks, data: dict = None):
    """
    Déclenche une exécution manuelle d'une méta-analyse en arrière-plan.
    Accepte optionnellement un custom_context dans le payload JSON.
    """
    from app.main import meta_service # Import lazy pour éviter ImportError ciculaire
    
    custom_context = data.get("custom_context") if data else None

    # On exécute de manière asynchrone pour ne pas bloquer la requête
    async def task_runner():
        _running_configs.add(config_id)
        _cancel_requests.discard(config_id)
        try:
            await meta_service.execute_meta_analysis(config_id, custom_context=custom_context)
        finally:
            _running_configs.discard(config_id)
            _cancel_requests.discard(config_id)
        
    background_tasks.add_task(task_runner)
    return {"status": "ok", "message": "Méta-analyse lancée en arrière-plan"}


@router.get("/running")
async def get_running_configs():
    """Retourne la liste des config_id dont l'analyse est actuellement en cours."""
    return {"running": list(_running_configs)}


@router.post("/cancel/{config_id}")
async def cancel_meta_analysis(config_id: int):
    """Demande l'annulation d'une analyse en cours."""
    _cancel_requests.add(config_id)
    return {"status": "ok"}


# ---- RESULTS ----

@router.get("/results")
async def list_results(config_id: Optional[int] = None, limit: int = 20, db: Session = Depends(get_db)):
    query = db.query(MetaAnalysisResult, MetaAnalysisConfig.name).join(
        MetaAnalysisConfig, MetaAnalysisResult.config_id == MetaAnalysisConfig.id
    )
    if config_id:
        query = query.filter(MetaAnalysisResult.config_id == config_id)
        
    results = query.order_by(desc(MetaAnalysisResult.created_at)).limit(limit).all()
    
    return [{
        "id": r.MetaAnalysisResult.id,
        "config_id": r.MetaAnalysisResult.config_id,
        "config_name": r.name,
        "period_start": r.MetaAnalysisResult.period_start.isoformat() + 'Z',
        "period_end": r.MetaAnalysisResult.period_end.isoformat() + 'Z',
        "analyses_count": r.MetaAnalysisResult.analyses_count,
        "detection_ids": json.loads(r.MetaAnalysisResult.detection_ids_json) if getattr(r.MetaAnalysisResult, 'detection_ids_json', None) else [],
        "matched_keywords": json.loads(r.MetaAnalysisResult.matched_keywords_json) if getattr(r.MetaAnalysisResult, 'matched_keywords_json', None) else [],
        "context_sent": getattr(r.MetaAnalysisResult, 'context_sent', None),
        "ollama_response": r.MetaAnalysisResult.ollama_response,
        "created_at": r.MetaAnalysisResult.created_at.isoformat() + 'Z'
    } for r in results]

@router.delete("/results/{result_id}")
async def delete_result(result_id: int, db: Session = Depends(get_db)):
    result = db.query(MetaAnalysisResult).filter(MetaAnalysisResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Résultat introuvable")
    db.delete(result)
    db.commit()
    return {"status": "ok"}

@router.post("/results/{result_id}/notify")
async def notify_result(result_id: int, db: Session = Depends(get_db)):
    result = db.query(MetaAnalysisResult).filter(MetaAnalysisResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Résultat introuvable")
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == result.config_id).first()
    global_cfg = db.query(GlobalConfig).first()
    if not global_cfg:
        raise HTTPException(status_code=400, detail="Configuration globale introuvable")
    from app.main import meta_service
    await meta_service._send_notification(result, config, global_cfg)
    return {"status": "ok"}
