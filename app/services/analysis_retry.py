"""Alertes que l'IA n'a pas pu analyser (serveur arrêté, injoignable, modèle absent).

Sans réponse, une alerte n'a pas de vraie gravité : elle attend (Analysis.ai_failed_at) au lieu de passer pour une info.
Dès que l'IA répond, elle est analysée et notifiée si elle le mérite, avec la mention « analysée en différé ». Les
répétitions d'un même événement reprennent le verdict déjà obtenu, sans nouvel appel. Si des alertes attendent depuis
NOTICE_AFTER, un seul avis prévient que Sentinel est aveugle, et un autre annonce le retour de l'IA."""
import asyncio
import html
import json
from datetime import datetime, timedelta
from typing import Optional

from app import logger
from app.database import SessionLocal
from app.models import Analysis, GlobalConfig, Rule
from app.services import dashboard_watch, monitoring
from app.services.alert_analysis import analysis_lines, close_one_off, watch_needed
from app.services.app_config import config_to_dict
from app.utils.notification_i18n import nt

CHECK_EVERY_SECONDS = 60
BATCH = 3  # analyses par passage : l'IA qui revient n'est pas noyée sous les rattrapages
NOTICE_AFTER = timedelta(minutes=10)
GIVE_UP_AFTER = timedelta(hours=24)
REUSE_WINDOW = timedelta(hours=24)


async def ai_ready(config: dict) -> bool:
    status = await asyncio.to_thread(dashboard_watch.ollama_status, config, None)
    return status["state"] == "ok"


def _send(subject: str, paragraphs: list, config: dict, severity: str) -> bool:
    """Avis système : HTML pour l'e-mail, texte pour Discord et Apprise (comme l'avis de silence d'une source)."""
    from app.services.notification_service import NotificationService
    prefix = f"[{config.get('instance_name')}] " if config.get("instance_name") else ""
    if config.get("notification_method") in ("apprise", "discord"):
        body = "\n\n".join(paragraphs)
    else:
        body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    # Sentinel ne peut plus juger les alertes : l'avis passe comme une alerte critique, mode discret compris
    return NotificationService().send(prefix + subject, body, config, kind="alert", severity=severity)


def _when(dt: datetime) -> str:
    return dt.strftime("%d/%m %H:%M UTC")


def _reuse_verdict(orchestrator, db, analysis: Analysis, config: dict) -> Optional[dict]:
    """Même événement déjà analysé autour de cette date : son verdict vaut pour cette répétition."""
    if not analysis.event_key:
        return None
    prior = db.query(Analysis).filter(
        Analysis.rule_id == analysis.rule_id, Analysis.event_key == analysis.event_key, Analysis.id != analysis.id,
        Analysis.ai_failed_at == None, ~Analysis.ollama_response.like("[Erreur%"),
        Analysis.analyzed_at >= analysis.analyzed_at - REUSE_WINDOW, Analysis.analyzed_at <= analysis.analyzed_at + REUSE_WINDOW,
    ).order_by(Analysis.analyzed_at.desc()).first()
    if not prior:
        return None
    raw = json.loads(analysis.triggered_lines_json) if analysis.triggered_lines_json else {"total": 1, "window_s": 0, "lines": [analysis.triggered_line or ""]}
    result = orchestrator.reused_result(raw, prior, config)
    orchestrator.apply_result(analysis, result)
    analysis.reused_from = prior.id
    analysis.ai_failed_at = None
    return result


async def run_once(orchestrator, now: Optional[datetime] = None, ready: Optional[bool] = None) -> dict:
    """Un passage : abandon des trop vieilles, avis de panne, ou reprise d'un lot. `ready` force l'état de l'IA (tests)."""
    now = now or datetime.utcnow()
    out = {"retried": 0, "reused": 0, "failed": 0, "gave_up": 0, "notice": None}
    db = SessionLocal()
    try:
        if monitoring.is_paused(db):
            return out
        settings = db.query(GlobalConfig).first()
        config = config_to_dict(settings)
        pending = db.query(Analysis).filter(Analysis.ai_failed_at != None).order_by(Analysis.analyzed_at).all()
        for a in [a for a in pending if now - a.ai_failed_at > GIVE_UP_AFTER]:
            a.ai_failed_at = None
            out["gave_up"] += 1
            logger.warning("AnalysisRetry", f"Alerte #{a.id} : l'IA n'a pas répondu en {GIVE_UP_AFTER.total_seconds() / 3600:.0f} h, plus de nouvel essai.")
        pending = [a for a in pending if a.ai_failed_at is not None]
        db.commit()
        lang = config.get("site_lang") or "fr"

        if not pending:
            if settings and settings.ai_outage_notified_at:
                settings.ai_outage_notified_at = None
                db.commit()
                # Passe comme l'avis de panne, mode discret compris : sinon on apprendrait la panne sans jamais sa fin
                _send(nt("ai_back_subject", lang), [nt("ai_back_body", lang)], config, "critical")
                out["notice"] = "back"
            return out

        if not (ready if ready is not None else await ai_ready(config)):
            oldest = min(a.ai_failed_at for a in pending)
            if settings and not settings.ai_outage_notified_at and now - oldest >= NOTICE_AFTER:
                rules = sorted({r.name for r in db.query(Rule).filter(Rule.id.in_({a.rule_id for a in pending})).all()})
                settings.ai_outage_notified_at = now
                db.commit()
                form = "one" if len(pending) == 1 else "many"
                _send(nt(f"ai_outage_subject_{form}", lang).format(n=len(pending)),
                      [nt(f"ai_outage_body_{form}", lang).format(n=len(pending), since=_when(oldest), rules=", ".join(rules))], config, "critical")
                out["notice"] = "outage"
            return out

        done = 0
        for a in pending:
            if done >= BATCH:
                break
            rule = db.get(Rule, a.rule_id)
            if not rule:
                a.ai_failed_at = None
                continue
            a.ai_retries = (a.ai_retries or 0) + 1
            result = _reuse_verdict(orchestrator, db, a, config)
            if result:
                db.commit()
                out["reused"] += 1
            else:
                done += 1
                result = await orchestrator.reanalyze(a, rule, config, db)
                db.commit()
                if result.get("failed"):
                    out["failed"] += 1
                    break  # l'IA ne répond toujours pas : on réessaiera au prochain passage
                out["retried"] += 1
            # Analysée en retard : si l'événement était ponctuel, il n'y a rien à surveiller (la règle, elle, reste
            # en alerte jusqu'à son retour à la normale habituel, pour ses autres analyses éventuelles)
            if a.resolution_status is None and not watch_needed(result, analysis_lines(a)):
                close_one_off(a)
                db.commit()
            logger.info("AnalysisRetry", f"Alerte #{a.id} de « {rule.name} » analysée en différé : {a.severity}.")
            await orchestrator.notify_analysis(a, rule, result, config, db, note=nt("late_note", lang).format(time=_when(a.analyzed_at)))
        return out
    finally:
        db.close()


async def run_forever(orchestrator):
    while True:
        await asyncio.sleep(CHECK_EVERY_SECONDS)
        try:
            await run_once(orchestrator)
        except Exception as e:
            logger.error("AnalysisRetry", f"Reprise des alertes non analysées en échec : {e}")
