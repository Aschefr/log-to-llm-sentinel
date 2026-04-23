from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.database import SessionLocal
from app.models import Rule

router = APIRouter(prefix="/api/rules", tags=["rules"])


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
