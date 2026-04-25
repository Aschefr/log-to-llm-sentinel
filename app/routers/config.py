from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal
from app.models import GlobalConfig
from app.services.notification_service import NotificationService
from app.services.ollama_service import OllamaService

import json
import urllib.request
import urllib.error
import asyncio
from fastapi.responses import StreamingResponse
import httpx 
from app import logger

router = APIRouter(prefix="/api/config", tags=["config"])
_orchestrator = None

def set_orchestrator(o):
    global _orchestrator
    _orchestrator = o

@router.post("/pull-model")
async def pull_model(data: dict):
    model_name = data.get("model")
    if not model_name:
        raise HTTPException(status_code=400, detail="Nom du modèle requis")
    
    db = SessionLocal()
    config = db.query(GlobalConfig).first()
    db.close()
    
    ollama_url = (config.ollama_url or "http://ollama:11434").rstrip("/")
    pull_url = f"{ollama_url}/api/pull"

    async def event_generator():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", pull_url, json={"name": model_name}) as response:
                    async for line in response.aiter_lines():
                        if line:
                            yield f"data: {line}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class ConfigUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_recipient: Optional[str] = None
    smtp_tls: Optional[bool] = None
    smtp_ssl_mode: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_temp: Optional[float] = None
    ollama_ctx: Optional[int] = None
    ollama_think: Optional[bool] = None
    system_prompt: Optional[str] = None
    notification_method: Optional[str] = None
    apprise_url: Optional[str] = None
    apprise_tags: Optional[str] = None
    apprise_max_chars: Optional[int] = None
    max_log_chars: Optional[int] = None
    monitor_log_lines: Optional[int] = None
    debug_mode: Optional[bool] = None


@router.get("")
def get_config():
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            config = GlobalConfig()
            db.add(config)
            db.commit()
            db.refresh(config)
        return {
            "smtp_host": config.smtp_host,
            "smtp_port": config.smtp_port,
            "smtp_user": config.smtp_user,
            "smtp_recipient": config.smtp_recipient,
            "smtp_tls": config.smtp_tls,
            "smtp_ssl_mode": config.smtp_ssl_mode,
            "ollama_url": config.ollama_url,
            "ollama_model": config.ollama_model,
            "ollama_temp": config.ollama_temp,
            "ollama_ctx": config.ollama_ctx,
            "ollama_think": config.ollama_think,
            "system_prompt": config.system_prompt,
            "notification_method": config.notification_method,
            "apprise_url": config.apprise_url,
            "apprise_tags": config.apprise_tags,
            "apprise_max_chars": config.apprise_max_chars,
            "max_log_chars": config.max_log_chars,
            "monitor_log_lines": config.monitor_log_lines,
            "debug_mode": config.debug_mode,
        }
    finally:
        db.close()


@router.put("")
def update_config(config_data: ConfigUpdate):
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config:
            config = GlobalConfig()
            db.add(config)

        from app import logger
        logger.debug("ConfigRouter", f"Données reçues: {config_data.dict(exclude={'smtp_password'})}")

        if config_data.smtp_host is not None:
            config.smtp_host = config_data.smtp_host
        if config_data.smtp_port is not None:
            config.smtp_port = config_data.smtp_port
        if config_data.smtp_user is not None:
            config.smtp_user = config_data.smtp_user
        if config_data.smtp_password is not None:
            config.smtp_password = config_data.smtp_password
        if config_data.smtp_recipient is not None:
            config.smtp_recipient = config_data.smtp_recipient
        if config_data.smtp_tls is not None:
            config.smtp_tls = config_data.smtp_tls
        if config_data.smtp_ssl_mode is not None:
            config.smtp_ssl_mode = config_data.smtp_ssl_mode
        if config_data.ollama_url is not None:
            config.ollama_url = config_data.ollama_url
        if config_data.ollama_model is not None:
            config.ollama_model = config_data.ollama_model
        if config_data.ollama_temp is not None:
            config.ollama_temp = config_data.ollama_temp
        if config_data.ollama_ctx is not None:
            config.ollama_ctx = config_data.ollama_ctx
        if config_data.ollama_think is not None:
            config.ollama_think = config_data.ollama_think
        if config_data.system_prompt is not None:
            config.system_prompt = config_data.system_prompt
        if config_data.notification_method is not None:
            config.notification_method = config_data.notification_method
        if config_data.apprise_url is not None:
            config.apprise_url = config_data.apprise_url
        if config_data.apprise_tags is not None:
            config.apprise_tags = config_data.apprise_tags
        if config_data.apprise_max_chars is not None:
            config.apprise_max_chars = config_data.apprise_max_chars
        if config_data.max_log_chars is not None:
            config.max_log_chars = config_data.max_log_chars
        if config_data.monitor_log_lines is not None:
            config.monitor_log_lines = config_data.monitor_log_lines
        if config_data.debug_mode is not None:
            config.debug_mode = config_data.debug_mode

        db.commit()
        return {"message": "Configuration mise à jour"}
    finally:
        db.close()


def _get_config_dict(config: Optional[GlobalConfig]) -> dict:
    return {
        "smtp_host": config.smtp_host if config else "",
        "smtp_port": config.smtp_port if config else 587,
        "smtp_user": config.smtp_user if config else "",
        "smtp_password": config.smtp_password if config else "",
        "smtp_recipient": config.smtp_recipient if config else "",
        "smtp_tls": config.smtp_tls if config else True,
        "smtp_ssl_mode": config.smtp_ssl_mode if config else "starttls",
        "ollama_url": config.ollama_url if config and config.ollama_url else "http://ollama:11434",
        "ollama_model": config.ollama_model if config and config.ollama_model else "gemma4:e4b",
        "system_prompt": config.system_prompt if config else "",
        "notification_method": config.notification_method if config else "smtp",
        "apprise_url": config.apprise_url if config else "",
        "apprise_tags": config.apprise_tags if config else "",
        "apprise_max_chars": config.apprise_max_chars if config else 1900,
        "max_log_chars": config.max_log_chars if config else 5000,
        "monitor_log_lines": config.monitor_log_lines if config else 60,
        "debug_mode": config.debug_mode if config else False,
    }


@router.post("/test/ollama")
async def test_ollama():
    from app.services.ollama_service import OllamaService
    ollama = OllamaService()
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        cfg = _get_config_dict(config)

        url = (cfg.get("ollama_url") or "").strip()
        model = (cfg.get("ollama_model") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL Ollama manquante")
        if not model:
            raise HTTPException(status_code=400, detail="Modèle Ollama manquant")

        # 1) reachability via /api/tags
        base = url.strip().rstrip("/")
        if base.endswith("/api/generate"):
            base = base[: -len("/api/generate")]
        elif base.endswith("/api"):
            base = base[: -len("/api")]
            
        available_models = []
        try:
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                tags_raw = r.read().decode("utf-8", errors="ignore")
                tags = json.loads(tags_raw)
                for m in tags.get("models", []) or []:
                    name = m.get("name")
                    if name: available_models.append(name)
        except Exception as e:
            logger.warning("ConfigRouter", f"Impossible de joindre /api/tags : {e}")

        # 2) generation asynchrone (streaming) protégée par le sémaphore
        prompt = "Réponds uniquement par 'OK'."
        
        try:
            # On utilise le sémaphore global s'il est dispo
            sem = _orchestrator._ollama_semaphore if _orchestrator else asyncio.Semaphore(1)
            async with sem:
                try:
                    resp = await asyncio.wait_for(
                        ollama.analyze_async(
                            prompt=prompt, 
                            url=url, 
                            model=model
                        ),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    resp = "[Erreur Ollama] Délai d'attente dépassé (30s)"
            
            if isinstance(resp, str) and resp.startswith("[Erreur Ollama]"):
                raise HTTPException(status_code=502, detail=resp)

            return {
                "ok": True,
                "detail": "Connexion Ollama OK",
                "sample": resp,
                "available_models": available_models,
            }
        except Exception as e:
            if isinstance(e, HTTPException): raise e
            raise HTTPException(status_code=502, detail=f"Erreur génération : {str(e)}")
    finally:
        db.close()


@router.post("/test/smtp")
def test_smtp():
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        cfg = _get_config_dict(config)

        # Envoie un email de test vers smtp_user (comportement existant)
        notifier = NotificationService()
        subject = "[Log to LLM Sentinel] Test SMTP"
        body = "<p>Ceci est un email de test envoyé par Log-to-LLM-Sentinel.</p>"
        ok = notifier._send_smtp(subject, body, cfg, to_email=cfg.get("smtp_recipient") or cfg.get("smtp_user") or None)

        if not ok:
            raise HTTPException(status_code=502, detail="Échec de l'envoi SMTP (vérifie hôte/port/user/mdp/TLS)")

        return {"ok": True, "detail": "Email SMTP envoyé (si configuration correcte)"}
    finally:
        db.close()


@router.post("/test/apprise")
def test_apprise():
    """
    Test “connexion” Apprise : vérifie que l'URL est joignable.
    (La livraison d'une notification dépend de ta config Apprise.)
    """
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        cfg = _get_config_dict(config)

        apprise_url = (cfg.get("apprise_url") or "").strip()
        if not apprise_url:
            raise HTTPException(status_code=400, detail="URL Apprise manquante")

        from app.services.notification_service import NotificationService
        notifier = NotificationService()
        subject = "Test Apprise Log to LLM Sentinel"
        body = "Ceci est un test de configuration Log to LLM Sentinel"
        
        ok = notifier._send_apprise(subject, body, cfg)
        if not ok:
            raise HTTPException(status_code=502, detail="Échec de l'envoi Apprise (vérifie l'URL ou les logs debug)")

        return {"ok": True, "detail": "Notification Apprise envoyée avec succès"}
    finally:
        db.close()


@router.get("/apprise/tags")
def list_apprise_tags():
    """Tente de récupérer les tags configurés dans l'API Apprise."""
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        if not config or not config.apprise_url:
            return {"tags": []}

        url = config.apprise_url.strip()
        if "/notify/" in url:
            json_url = url.replace("/notify/", "/json/urls/")
        else:
            return {"tags": []}

        try:
            req = urllib.request.Request(json_url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            from app import logger
            logger.debug("ConfigRouter", f"Erreur fetch tags Apprise: {e}")
            return {"tags": []}

        tags = set()
        for item in data.get("urls", []):
            item_tags = item.get("tags", [])
            for t in item_tags:
                tags.add(t)

        return {"tags": sorted(list(tags))}
    finally:
        db.close()


@router.get("/ollama/models")
def list_ollama_models():
    """Retourne la liste des modèles disponibles sur le serveur Ollama configuré."""
    db = SessionLocal()
    try:
        config = db.query(GlobalConfig).first()
        cfg = _get_config_dict(config)

        url = (cfg.get("ollama_url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="URL Ollama manquante")

        base = url.rstrip("/")
        if base.endswith("/api/generate"):
            base = base[: -len("/api/generate")]
        elif base.endswith("/api"):
            base = base[: -len("/api")]

        try:
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                tags_raw = r.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"[Erreur Ollama] /api/tags a répondu HTTP {e.code}")
        except urllib.error.URLError as e:
            raise HTTPException(status_code=502, detail=f"[Erreur Ollama] Impossible de joindre Ollama: {str(e)}")

        try:
            tags = json.loads(tags_raw) if tags_raw else {}
        except Exception:
            tags = {}

        available_models = []
        try:
            for m in tags.get("models", []) or []:
                name = m.get("name")
                if name:
                    available_models.append(name)
        except Exception:
            available_models = []

        return {"models": available_models}
    finally:
        db.close()


@router.get("/logs")
def get_debug_logs():
    """Retourne les derniers logs en mémoire."""
    from app.logger import get_logs
    return {"logs": get_logs()}


@router.delete("/logs")
def clear_debug_logs():
    """Efface les logs en mémoire."""
    from app.logger import clear_logs
    clear_logs()
    return {"message": "Logs effacés"}


@router.get("/ollama/logs")
def get_ollama_debug_logs():
    """Retourne les derniers appels Ollama en mémoire."""
    from app.logger import get_ollama_logs
    return {"logs": get_ollama_logs()}


@router.delete("/ollama/logs")
def clear_ollama_debug_logs():
    """Efface les logs Ollama en mémoire."""
    from app.logger import clear_ollama_logs
    clear_ollama_logs()
    return {"message": "Logs Ollama effacés"}
