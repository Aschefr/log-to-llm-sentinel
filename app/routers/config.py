from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal
from app.models import GlobalConfig

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
