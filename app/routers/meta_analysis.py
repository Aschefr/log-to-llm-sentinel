from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
import json

from app.database import SessionLocal
from app.models import MetaAnalysisConfig, MetaAnalysisResult
from app.services.meta_service import MetaAnalysisService

router = APIRouter(prefix="/api/meta-analysis", tags=["meta-analysis"])

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
        "interval_hours": c.interval_hours,
        "enabled": c.enabled,
        "notify_enabled": c.notify_enabled,
        "context_size": c.context_size,
        "system_prompt": c.system_prompt,
        "max_analyses": c.max_analyses,
        "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None
    } for c in configs]

@router.post("/configs")
async def create_config(data: dict, db: Session = Depends(get_db)):
    try:
        rule_ids = data.get("rule_ids", [])
        config = MetaAnalysisConfig(
            name=data.get("name", "Nouvelle Méta-Analyse"),
            rule_ids_json=json.dumps(rule_ids),
            interval_hours=int(data.get("interval_hours", 24)),
            enabled=data.get("enabled", True),
            notify_enabled=data.get("notify_enabled", True),
            context_size=int(data.get("context_size", 16000)),
            system_prompt=data.get("system_prompt", "Tu es un expert DevOps. Analyse ces événements et fais une synthèse globale de la situation du service."),
            max_analyses=int(data.get("max_analyses", 50))
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
        if "interval_hours" in data: config.interval_hours = int(data["interval_hours"])
        if "enabled" in data: config.enabled = bool(data["enabled"])
        if "notify_enabled" in data: config.notify_enabled = bool(data["notify_enabled"])
        if "context_size" in data: config.context_size = int(data["context_size"])
        if "system_prompt" in data: config.system_prompt = data["system_prompt"]
        if "max_analyses" in data: config.max_analyses = int(data["max_analyses"])

        db.commit()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/configs/{config_id}")
async def delete_config(config_id: int, db: Session = Depends(get_db)):
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Config non trouvée")
    db.delete(config)
    db.commit()
    return {"status": "ok"}


# ---- TRIGGER ----

@router.post("/trigger/{config_id}")
async def trigger_meta_analysis(config_id: int, background_tasks: BackgroundTasks):
    """
    Déclenche une exécution manuelle d'une méta-analyse en arrière-plan.
    """
    from app.main import meta_service # Import lazy pour éviter ImportError ciculaire
    
    # On exécute de manière asynchrone pour ne pas bloquer la requête
    async def task_runner():
        await meta_service.execute_meta_analysis(config_id)
        
    background_tasks.add_task(task_runner)
    return {"status": "ok", "message": "Méta-analyse lancée en arrière-plan"}


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
        "period_start": r.MetaAnalysisResult.period_start.isoformat(),
        "period_end": r.MetaAnalysisResult.period_end.isoformat(),
        "analyses_count": r.MetaAnalysisResult.analyses_count,
        "detection_ids": json.loads(r.MetaAnalysisResult.detection_ids_json) if getattr(r.MetaAnalysisResult, 'detection_ids_json', None) else [],
        "matched_keywords": json.loads(r.MetaAnalysisResult.matched_keywords_json) if getattr(r.MetaAnalysisResult, 'matched_keywords_json', None) else [],
        "ollama_response": r.MetaAnalysisResult.ollama_response,
        "created_at": r.MetaAnalysisResult.created_at.isoformat()
    } for r in results]
