from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import os

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig
from app.services.orchestrator import Orchestrator

router = APIRouter(prefix="/api/rules", tags=["rules"])
orchestrator = Orchestrator()


class RuleCreate(BaseModel):
    name: str
    log_file_path: str
    keywords: List[str]
    application_context: str = ""
    enabled: bool = True
    notify_on_match: bool = True


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    log_file_path: Optional[str] = None
    keywords: Optional[List[str]] = None
    application_context: Optional[str] = None
    enabled: Optional[bool] = None
    notify_on_match: Optional[bool] = None


@router.get("")
def get_rules():
    db = SessionLocal()
    try:
        rules = db.query(Rule).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "log_file_path": r.log_file_path,
                "keywords": r.get_keywords(),
                "application_context": r.application_context,
                "enabled": r.enabled,
                "notify_on_match": r.notify_on_match,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ]
    finally:
        db.close()


@router.get("/{rule_id}")
def get_rule(rule_id: int):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")
        return {
            "id": rule.id,
            "name": rule.name,
            "log_file_path": rule.log_file_path,
            "keywords": rule.get_keywords(),
            "application_context": rule.application_context,
            "enabled": rule.enabled,
            "notify_on_match": rule.notify_on_match,
        }
    finally:
        db.close()


@router.post("")
def create_rule(rule_data: RuleCreate):
    db = SessionLocal()
    try:
        rule = Rule(
            name=rule_data.name,
            log_file_path=rule_data.log_file_path,
            application_context=rule_data.application_context,
            enabled=rule_data.enabled,
            notify_on_match=rule_data.notify_on_match,
        )
        rule.set_keywords(rule_data.keywords)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return {"id": rule.id, "message": "Règle créée"}
    finally:
        db.close()


@router.put("/{rule_id}")
def update_rule(rule_id: int, rule_data: RuleUpdate):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")

        if rule_data.name is not None:
            rule.name = rule_data.name
        if rule_data.log_file_path is not None:
            rule.log_file_path = rule_data.log_file_path
        if rule_data.keywords is not None:
            rule.set_keywords(rule_data.keywords)
        if rule_data.application_context is not None:
            rule.application_context = rule_data.application_context
        if rule_data.enabled is not None:
            rule.enabled = rule_data.enabled
        if rule_data.notify_on_match is not None:
            rule.notify_on_match = rule_data.notify_on_match

        db.commit()
        return {"id": rule.id, "message": "Règle mise à jour"}
    finally:
        db.close()


@router.delete("/{rule_id}")
def delete_rule(rule_id: int):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")
        db.delete(rule)
        db.commit()
        return {"message": "Règle supprimée"}
    finally:
        db.close()


def _read_last_non_empty_line(path: str, max_bytes: int = 65536) -> Optional[str]:
    """
    Lit la dernière ligne non vide d'un fichier sans charger tout le fichier en mémoire.
    Retourne None si aucune ligne utile.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return None

            to_read = min(max_bytes, end)
            f.seek(end - to_read)
            chunk = f.read(to_read)

        # Decode permissive
        text = chunk.decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return None
        return lines[-1]
    except Exception:
        return None


@router.post("/{rule_id}/test")
def test_rule(rule_id: int):
    """
    Envoie la dernière ligne du fichier log de la règle pour analyse,
    sauvegarde l'analyse en BDD, et renvoie le résultat.
    """
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")

        last_line = _read_last_non_empty_line(rule.log_file_path)
        if not last_line:
            raise HTTPException(
                status_code=400,
                detail="Impossible de lire une dernière ligne (fichier introuvable, vide, ou illisible).",
            )

        config = db.query(GlobalConfig).first()
        config_dict = {
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

        prompt = orchestrator._build_prompt(rule, last_line, config_dict.get("system_prompt", ""))
        response = orchestrator.ollama.analyze(
            prompt=prompt,
            url=config_dict.get("ollama_url"),
            model=config_dict.get("ollama_model"),
            timeout=90,
            retries=2,
        )
        severity = orchestrator._detect_severity(response)

        analysis = Analysis(
            rule_id=rule.id,
            triggered_line=last_line,
            context_before_json="[]",
            context_after_json="[]",
            ollama_response=response,
            severity=severity,
            notified=False,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        return {
            "id": analysis.id,
            "rule_id": analysis.rule_id,
            "triggered_line": analysis.triggered_line,
            "ollama_response": analysis.ollama_response,
            "severity": analysis.severity,
            "analyzed_at": analysis.analyzed_at.isoformat() if analysis.analyzed_at else None,
        }
    finally:
        db.close()
