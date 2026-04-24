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
    context_lines: int = 5


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    log_file_path: Optional[str] = None
    keywords: Optional[List[str]] = None
    application_context: Optional[str] = None
    enabled: Optional[bool] = None
    notify_on_match: Optional[bool] = None
    context_lines: Optional[int] = None


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
                "context_lines": r.context_lines or 5,
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
            "context_lines": rule.context_lines or 5,
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
            context_lines=rule_data.context_lines,
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
        if rule_data.context_lines is not None:
            rule.context_lines = rule_data.context_lines

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


def _read_last_lines(path: str, n: int = 5, max_bytes: int = 65536) -> List[str]:
    """
    Lit les N dernières lignes non vides d'un fichier.
    Retourne une liste vide si aucune ligne utile.
    """
    if not os.path.exists(path):
        return []

    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end == 0:
                return []

            to_read = min(max_bytes, end)
            f.seek(end - to_read)
            chunk = f.read(to_read)

        text = chunk.decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return []
        return lines[-n:]
    except Exception:
        return []


@router.post("/{rule_id}/test")
def test_rule(rule_id: int):
    """
    Envoie les dernières lignes du fichier log de la règle pour analyse,
    sauvegarde l'analyse en BDD, envoie une notification, et renvoie le résultat.
    """
    from app import logger
    from app.services.notification_service import NotificationService

    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle non trouvée")

        ctx_lines = rule.context_lines or 5
        logger.debug("TestRule", f"Règle '{rule.name}' — lecture des {ctx_lines} dernières lignes de {rule.log_file_path}")

        last_lines = _read_last_lines(rule.log_file_path, n=ctx_lines)
        if not last_lines:
            raise HTTPException(
                status_code=400,
                detail="Impossible de lire les dernières lignes (fichier introuvable, vide, ou illisible).",
            )

        last_line = last_lines[-1]
        logger.debug("TestRule", f"Dernière ligne : {last_line[:120]}")

        config = db.query(GlobalConfig).first()
        config_dict = {
            "smtp_host": config.smtp_host if config else "",
            "smtp_port": config.smtp_port if config else 587,
            "smtp_user": config.smtp_user if config else "",
            "smtp_password": config.smtp_password if config else "",
            "smtp_recipient": config.smtp_recipient if config else "",
            "smtp_tls": config.smtp_tls if config else True,
            "smtp_ssl_mode": config.smtp_ssl_mode if config else "starttls",
            "ollama_url": config.ollama_url if config else "http://host.docker.internal:11434",
            "ollama_model": config.ollama_model if config else "llama3",
            "system_prompt": config.system_prompt if config else "",
            "notification_method": config.notification_method if config else "smtp",
            "apprise_url": config.apprise_url if config else "",
            "apprise_tags": config.apprise_tags if config else "",
            "debug_mode": config.debug_mode if config else False,
        }

        prompt = orchestrator._build_prompt(rule, last_line, config_dict.get("system_prompt", ""), context_lines=last_lines[:-1])
        logger.debug("TestRule", f"Envoi à Ollama — modèle={config_dict.get('ollama_model')}")

        response = orchestrator.ollama.analyze(
            prompt=prompt,
            url=config_dict.get("ollama_url"),
            model=config_dict.get("ollama_model"),
            timeout=90,
            retries=2,
        )
        logger.debug("TestRule", f"Réponse Ollama reçue ({len(response)} chars)")
        severity = orchestrator._detect_severity(response)
        logger.debug("TestRule", f"Sévérité détectée : {severity}")

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

        # Envoyer une notification (comme en production)
        if rule.notify_on_match:
            logger.debug("TestRule", f"Envoi notification via '{config_dict.get('notification_method')}'")
            notifier = NotificationService()
            subject = f"[Sentinel TEST] Alerte {severity.upper()} : {rule.name}"
            
            body = f"""
            <h2>🧪 Test Log Sentinel</h2>
            <p><strong>Règle:</strong> {rule.name}</p>
            <p><strong>Ligne déclenchante:</strong> <code>{last_line}</code></p>
            <p><strong>Analyse Ollama:</strong></p>
            <blockquote>{response}</blockquote>
            <p><strong>Sévérité:</strong> {severity}</p>
            <hr><p><em>Ceci est un test manuel depuis l'interface Log Sentinel.</em></p>
            """

            # Gestion du résumé IA si nécessaire
            max_chars = config_dict.get("apprise_max_chars", 1900)
            notify_body = body

            if config_dict.get("notification_method") == "apprise" and len(body) > max_chars:
                logger.debug("TestRule", f"Analyse trop longue ({len(body)} chars), demande de résumé simplifié à Ollama...")
                summary_prompt = f"Résume l'analyse suivante en moins de {max_chars - 400} caractères. Garde l'essentiel (Sévérité, Cause, Action). Format clair.\n\nAnalyse : {response}"
                summary = orchestrator.ollama.analyze(
                    prompt=summary_prompt,
                    url=config_dict.get("ollama_url"),
                    model=config_dict.get("ollama_model"),
                    timeout=60
                )
                if not (isinstance(summary, str) and summary.startswith("[Erreur Ollama]")):
                    notify_body = f"""
                    <h2>🧪 Test Log Sentinel (Résumé)</h2>
                    <p><strong>Règle:</strong> {rule.name}</p>
                    <p><strong>Résumé:</strong></p>
                    <blockquote>{summary}</blockquote>
                    <p><strong>Sévérité:</strong> {severity}</p>
                    <p><em>(Analyse complète disponible dans l'interface)</em></p>
                    """

            try:
                ok = notifier.send(subject, notify_body, config_dict)
                if ok:
                    logger.info("TestRule", "Notification de test envoyée avec succès")
                    analysis.notified = True
                    db.commit()
                else:
                    logger.warning("TestRule", "Échec de l'envoi de la notification de test")
            except Exception as e:
                logger.error("TestRule", f"Erreur lors de l'envoi de la notification : {e}")
        else:
            logger.debug("TestRule", "Notification désactivée pour cette règle")

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
