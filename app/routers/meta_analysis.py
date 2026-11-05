from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime
import json

from app.database import SessionLocal
from app.models import MetaAnalysisConfig, MetaAnalysisResult, GlobalConfig, Rule
from app.services import meta_overview
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

def _status_of(result) -> Optional[str]:
    """Les anciennes analyses échouées n'ont pas d'état : elles se reconnaissent à leur texte."""
    if not result.status and (result.ollama_response or "").startswith("[Erreur"):
        return "failed"
    return result.status


def _latest_result(db, config_id: int) -> Optional[dict]:
    r = db.query(MetaAnalysisResult).filter(MetaAnalysisResult.config_id == config_id) \
        .order_by(desc(MetaAnalysisResult.created_at), desc(MetaAnalysisResult.id)).first()
    if not r:
        return None
    return {"id": r.id, "status": _status_of(r), "tldr": r.tldr, "analyses_count": r.analyses_count,
            "created_at": r.created_at.isoformat() + 'Z'}


@router.get("/configs")
async def list_configs(db: Session = Depends(get_db)):
    configs = db.query(MetaAnalysisConfig).order_by(MetaAnalysisConfig.id.desc()).all()
    rule_names = {r.id: r.name for r in db.query(Rule.id, Rule.name).all()}
    return [{
        "latest": _latest_result(db, c.id),
        # Règles supprimées depuis : la synthèse ne doit pas laisser croire qu'elle les suit encore
        "missing_rules": sum(1 for i in (json.loads(c.rule_ids_json) if c.rule_ids_json else []) if i not in rule_names),
        "id": c.id,
        "name": c.name,
        "rule_ids_json": json.loads(c.rule_ids_json) if c.rule_ids_json else [],
        "schedule_type": c.schedule_type,
        "schedule_time": c.schedule_time,
        "schedule_day": c.schedule_day,
        "enabled": c.enabled,
        "notify_enabled": c.notify_enabled,
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
        raise HTTPException(status_code=404, detail="config_not_found")
    
    try:
        if "name" in data: config.name = data["name"]
        if "rule_ids" in data: config.rule_ids_json = json.dumps(data["rule_ids"])
        if "schedule_type" in data: config.schedule_type = data["schedule_type"]
        if "schedule_time" in data: config.schedule_time = data["schedule_time"]
        if "schedule_day" in data: config.schedule_day = int(data["schedule_day"])
        if "enabled" in data: config.enabled = bool(data["enabled"])
        if "notify_enabled" in data: config.notify_enabled = bool(data["notify_enabled"])
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
        raise HTTPException(status_code=404, detail="config_not_found")
    # SQLite n'applique pas les FKs par défaut — on supprime manuellement les résultats liés
    db.query(MetaAnalysisResult).filter(MetaAnalysisResult.config_id == config_id).delete()
    db.delete(config)
    db.commit()
    return {"status": "ok"}

@router.post("/configs/{config_id}/reset-last-run")
async def reset_last_run(config_id: int, db: Session = Depends(get_db)):
    """Remet last_run_at à None pour que la prochaine fenêtre soit recalculée depuis le schedule."""
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="config_not_found")
    config.last_run_at = None
    db.commit()
    return {"status": "ok"}


@router.get("/configs/{config_id}/overview")
async def config_overview(config_id: int, days: int = Query(30, ge=1, le=365), tz: int = Query(0, ge=-840, le=840), db: Session = Depends(get_db)):
    """Activité des règles dans le temps et ce qui change d'une période à l'autre. Aucun appel à l'IA."""
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="config_not_found")
    return meta_overview.build(db, config, days, tz)


# ---- TRIGGER ----

@router.get("/trigger/preview/{config_id}")
async def preview_meta_analysis(
    config_id: int,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Retourne le contexte exact en attente d'envoi.
    Accepte optionnellement period_start et period_end (ISO datetime) pour surcharger la fenêtre.
    """
    from app.main import meta_service
    forced_start = None
    forced_end = None
    if period_start:
        try:
            forced_start = datetime.fromisoformat(period_start.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            pass
    if period_end:
        try:
            forced_end = datetime.fromisoformat(period_end.replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            pass
    result = await meta_service.get_pending_context(
        config_id,
        forced_period_start=forced_start,
        forced_period_end=forced_end
    )
    if result["status"] != "ok":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result



@router.post("/trigger/{config_id}")
async def trigger_meta_analysis(config_id: int, background_tasks: BackgroundTasks, data: dict = None):
    """
    Déclenche une exécution manuelle d'une méta-analyse en arrière-plan.
    Accepte optionnellement excluded_ids / annotations (sélection faite dans l'aperçu, agrégée côté serveur)
    ou un custom_context libre (ancien format) dans le payload JSON.
    """
    from app.main import meta_service # Import lazy pour éviter ImportError ciculaire

    custom_context = data.get("custom_context") if data else None
    selection = None
    if data and ("excluded_ids" in data or "annotations" in data):
        selection = {"excluded_ids": data.get("excluded_ids") or [], "annotations": data.get("annotations") or {}}
    forced_period_start = None
    forced_period_end = None
    if data:
        try:
            if data.get("period_start"):
                forced_period_start = datetime.fromisoformat(data["period_start"].replace('Z', '+00:00')).replace(tzinfo=None)
            if data.get("period_end"):
                forced_period_end = datetime.fromisoformat(data["period_end"].replace('Z', '+00:00')).replace(tzinfo=None)
        except Exception:
            pass

    # On exécute de manière asynchrone pour ne pas bloquer la requête
    async def task_runner():
        _running_configs.add(config_id)
        _cancel_requests.discard(config_id)
        try:
            await meta_service.execute_meta_analysis(
                config_id,
                custom_context=custom_context,
                forced_period_start=forced_period_start,
                forced_period_end=forced_period_end,
                selection=selection,
            )
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

def _structured(result) -> dict:
    try:
        data = json.loads(result.result_json) if result.result_json else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    out = {key: [str(x) for x in (data.get(key) or []) if str(x).strip()] for key in ("highlights", "recommendations")}
    out["synthesis"] = str(data.get("synthesis") or "").strip() or None
    return out


def _result_brief(r, config_name) -> dict:
    return {
        "id": r.id,
        "config_id": r.config_id,
        "config_name": config_name,
        "period_start": r.period_start.isoformat() + 'Z',
        "period_end": r.period_end.isoformat() + 'Z',
        "analyses_count": r.analyses_count,
        "tldr": r.tldr,
        "status": _status_of(r),
        "created_at": r.created_at.isoformat() + 'Z',
    }


def _result_full(r, config_name) -> dict:
    return {
        **_structured(r),
        **_result_brief(r, config_name),
        "detection_ids": json.loads(r.detection_ids_json) if getattr(r, 'detection_ids_json', None) else [],
        "matched_keywords": json.loads(r.matched_keywords_json) if getattr(r, 'matched_keywords_json', None) else [],
        "context_sent": getattr(r, 'context_sent', None),
        "ollama_response": r.ollama_response,
    }


@router.get("/results")
async def list_results(config_id: Optional[int] = None, limit: int = 20, compact: bool = False, db: Session = Depends(get_db)):
    """compact : sans le contexte envoyé ni le texte, pour lister les rapports sans charger des mégaoctets."""
    query = db.query(MetaAnalysisResult, MetaAnalysisConfig.name).join(
        MetaAnalysisConfig, MetaAnalysisResult.config_id == MetaAnalysisConfig.id
    )
    if config_id:
        query = query.filter(MetaAnalysisResult.config_id == config_id)
    results = query.order_by(desc(MetaAnalysisResult.created_at), desc(MetaAnalysisResult.id)).limit(limit).all()
    view = _result_brief if compact else _result_full
    return [view(r.MetaAnalysisResult, r.name) for r in results]


@router.get("/results/{result_id}")
async def get_result(result_id: int, db: Session = Depends(get_db)):
    row = db.query(MetaAnalysisResult, MetaAnalysisConfig.name).join(
        MetaAnalysisConfig, MetaAnalysisResult.config_id == MetaAnalysisConfig.id
    ).filter(MetaAnalysisResult.id == result_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="result_not_found")
    return _result_full(row.MetaAnalysisResult, row.name)


@router.delete("/results/{result_id}")
async def delete_result(result_id: int, db: Session = Depends(get_db)):
    result = db.query(MetaAnalysisResult).filter(MetaAnalysisResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="result_not_found")
        
    # Restauration du pointeur de temps si on supprime la dernière analyse
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == result.config_id).first()
    if config and config.last_run_at and result.period_end and result.period_start:
        # Si le dernier run correspond à la fin de la période de l'analyse qu'on supprime
        # on recule le last_run_at au début de cette période pour remettre les logs dans la file d'attente
        if config.last_run_at == result.period_end:
            config.last_run_at = result.period_start

    db.delete(result)
    db.commit()
    return {"status": "ok"}

@router.post("/results/{result_id}/notify")
async def notify_result(result_id: int, db: Session = Depends(get_db)):
    result = db.query(MetaAnalysisResult).filter(MetaAnalysisResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="result_not_found")
    config = db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == result.config_id).first()
    global_cfg = db.query(GlobalConfig).first()
    if not global_cfg:
        raise HTTPException(status_code=400, detail="global_config_not_found")
    from app.main import meta_service
    await meta_service._send_notification(result, config, global_cfg, manual=True)
    return {"status": "ok"}
