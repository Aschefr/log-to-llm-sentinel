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

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigUpdate(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_tls: Optional[bool] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    system_prompt: Optional[str] = None
    notification_method: Optional[str] = None
    apprise_url: Optional[str] = None


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
            "smtp_tls": config.smtp_tls,
            "ollama_url": config.ollama_url,
            "ollama_model": config.ollama_model,
            "system_prompt": config.system_prompt,
            "notification_method": config.notification_method,
            "apprise_url": config.apprise_url,
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

        if config_data.smtp_host is not None:
            config.smtp_host = config_data.smtp_host
        if config_data.smtp_port is not None:
            config.smtp_port = config_data.smtp_port
        if config_data.smtp_user is not None:
            config.smtp_user = config_data.smtp_user
        if config_data.smtp_password is not None:
            config.smtp_password = config_data.smtp_password
        if config_data.smtp_tls is not None:
            config.smtp_tls = config_data.smtp_tls
        if config_data.ollama_url is not None:
            config.ollama_url = config_data.ollama_url
        if config_data.ollama_model is not None:
            config.ollama_model = config_data.ollama_model
        if config_data.system_prompt is not None:
            config.system_prompt = config_data.system_prompt
        if config_data.notification_method is not None:
            config.notification_method = config_data.notification_method
        if config_data.apprise_url is not None:
            config.apprise_url = config_data.apprise_url

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
        "smtp_tls": config.smtp_tls if config else True,
        "ollama_url": config.ollama_url if config else "http://host.docker.internal:11434",
        "ollama_model": config.ollama_model if config else "llama3",
        "system_prompt": config.system_prompt if config else "",
        "notification_method": config.notification_method if config else "smtp",
        "apprise_url": config.apprise_url if config else "",
    }


@router.post("/test/ollama")
def test_ollama():
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

        # Test minimal en 2 étapes:
        # 1) reachability via /api/tags (plus parlant qu'un 404 sur generate)
        base = url.strip().rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        try:
            req = urllib.request.Request(f"{base}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                _ = r.read()  # consume
        except urllib.error.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"[Erreur Ollama] /api/tags a répondu HTTP {e.code}")
        except urllib.error.URLError as e:
            raise HTTPException(status_code=502, detail=f"[Erreur Ollama] Impossible de joindre Ollama: {str(e)}")

        # 2) generation (courte)
        prompt = "Réponds uniquement par 'OK'."
        resp = OllamaService().analyze(prompt=prompt, url=base, model=model, timeout=10)

        if isinstance(resp, str) and resp.startswith("[Erreur Ollama]"):
            raise HTTPException(status_code=502, detail=resp)

        return {"ok": True, "detail": "Connexion Ollama OK", "sample": resp}
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
        subject = "[Sentinel] Test SMTP"
        body = "<p>Ceci est un email de test envoyé par Log-to-LLM-Sentinel.</p>"
        ok = notifier._send_smtp(subject, body, cfg, to_email=cfg.get("smtp_user") or None)

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

        # Test simple de reachability HTTP
        try:
            req = urllib.request.Request(apprise_url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as r:
                status = getattr(r, "status", 200)
                return {"ok": True, "detail": f"Apprise joignable (HTTP {status})"}
        except urllib.error.URLError as e:
            raise HTTPException(status_code=502, detail=f"Apprise injoignable: {str(e)}")
    finally:
        db.close()
