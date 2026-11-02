from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import asyncio
import json
import os

from app.database import SessionLocal
from app.models import Rule, Analysis, GlobalConfig, ResolutionVerdict
from app.services.orchestrator import Orchestrator
from app.services.app_config import config_to_dict
from app.services import rule_stats
from app.services.probe_service import run_probe, PROBE_TYPES
from app.utils.log_utils import clean_log_line

router = APIRouter(prefix="/api/rules", tags=["rules"])
orchestrator = None
resolution_service = None

CALM_MODES = ("adaptive", "fixed")
AI_MODES = ("ambiguous", "always", "never")
PROBE_CHOICES = ("none",) + PROBE_TYPES


def set_orchestrator(o):
    global orchestrator
    orchestrator = o


def set_resolution_service(s):
    global resolution_service
    resolution_service = s


def _choice(value, allowed, field):
    if value not in allowed:
        raise HTTPException(status_code=422, detail=f"invalid_{field}")
    return value


class RuleCreate(BaseModel):
    name: str
    log_file_path: str
    keywords: List[str]
    application_context: str = ""
    enabled: bool = True
    notify_on_match: bool = True
    context_lines: int = 5
    anti_spam_delay: int = 60
    notify_severity_threshold: str = "info"
    excluded_patterns: List[str] = []
    inactivity_warning_enabled: bool = True
    inactivity_period_hours: int = 1
    inactivity_notify: bool = True
    # MON-18
    alert_status: str = "normal"
    resolution_mode: str = "timeout"
    resolution_timeout_minutes: int = 30
    resolution_patterns: List[str] = []
    resolution_ai_enabled: bool = False
    resolution_notify_search: bool = False
    resolution_notify_resolved: bool = True
    resolution_calm_mode: str = "adaptive"
    resolution_ai_mode: str = "ambiguous"
    resolution_learn: bool = True
    probe_type: str = "none"
    probe_target: str = ""
    probe_expect: str = ""


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    log_file_path: Optional[str] = None
    keywords: Optional[List[str]] = None
    application_context: Optional[str] = None
    enabled: Optional[bool] = None
    notify_on_match: Optional[bool] = None
    context_lines: Optional[int] = None
    anti_spam_delay: Optional[int] = None
    notify_severity_threshold: Optional[str] = None
    excluded_patterns: Optional[List[str]] = None
    last_learning_session_id: Optional[int] = None  # pass -1 to explicitly clear
    inactivity_warning_enabled: Optional[bool] = None
    inactivity_period_hours: Optional[int] = None
    inactivity_notify: Optional[bool] = None
    # MON-18
    alert_status: Optional[str] = None
    resolution_mode: Optional[str] = None
    resolution_timeout_minutes: Optional[int] = None
    resolution_patterns: Optional[List[str]] = None
    resolution_ai_enabled: Optional[bool] = None
    resolution_notify_search: Optional[bool] = None
    resolution_notify_resolved: Optional[bool] = None
    resolution_calm_mode: Optional[str] = None
    resolution_ai_mode: Optional[str] = None
    resolution_learn: Optional[bool] = None
    probe_type: Optional[str] = None
    probe_target: Optional[str] = None
    probe_expect: Optional[str] = None


class PatternBody(BaseModel):
    pattern: str


class ProbeBody(BaseModel):
    probe_type: str
    probe_target: str
    probe_expect: str = ""


@router.get("")
def get_rules():
    db = SessionLocal()
    try:
        rules = db.query(Rule).all()
        result = []
        for r in rules:
            last_lines = _read_last_lines(r.log_file_path, n=1)
            last_line = last_lines[0] if last_lines else None
            
            rule_item = {
                "id": r.id,
                "name": r.name,
                "log_file_path": r.log_file_path,
                "keywords": r.get_keywords(),
                "application_context": r.application_context,
                "enabled": r.enabled,
                "notify_on_match": r.notify_on_match,
                "context_lines": r.context_lines or 5,
                "anti_spam_delay": r.anti_spam_delay or 60,
                "notify_severity_threshold": r.notify_severity_threshold or "info",
                "excluded_patterns": r.get_excluded_patterns(),
                "last_log_line": last_line,
                "last_learning_session_id": r.last_learning_session_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "inactivity_warning_enabled": r.inactivity_warning_enabled,
                "inactivity_period_hours": r.inactivity_period_hours,
                "inactivity_notify": r.inactivity_notify,
                "last_line_received_at": r.last_line_received_at.isoformat() + "Z" if r.last_line_received_at else None,
                "last_detection_id": None,
                "last_analysis_severity": None,
                # MON-18
                "alert_status": r.alert_status or "normal",
                "alert_started_at": r.alert_started_at.isoformat() + "Z" if r.alert_started_at else None,
                "resolution_mode": r.resolution_mode or "timeout",
                "resolution_timeout_minutes": r.resolution_timeout_minutes or 30,
                "resolution_patterns": r.get_resolution_patterns(),
                "resolution_patterns_weighted": r.get_weighted_resolution_patterns(),
                "resolution_ai_enabled": r.resolution_ai_enabled or False,
                "resolution_notify_search": r.resolution_notify_search or False,
                "resolution_notify_resolved": r.resolution_notify_resolved or False,
            }
            
            last_analysis = db.query(Analysis).filter(Analysis.rule_id == r.id).order_by(Analysis.analyzed_at.desc()).first()
            if last_analysis:
                rule_item["last_detection_id"] = last_analysis.detection_id
                rule_item["last_analysis_severity"] = last_analysis.severity
                rule_item["last_triggered_line"] = last_analysis.triggered_line
                rule_item["last_analysis_at"] = last_analysis.analyzed_at.strftime('%Y-%m-%dT%H:%M:%SZ') if last_analysis.analyzed_at else None

            result.append(rule_item)
        return result
    finally:
        db.close()


@router.get("/overview")
def rules_overview():
    """Vue d'ensemble : toutes les règles (désactivées comprises), état, alertes 7 j, dernier incident, MTTR, vitalité."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rules = db.query(Rule).all()
        since30 = now - timedelta(days=30)
        recent = db.query(Analysis).filter(Analysis.analyzed_at >= since30).all()
        by_rule = {}
        for a in recent:
            by_rule.setdefault(a.rule_id, []).append(a)
        items = []
        for r in rules:
            analyses = by_rule.get(r.id, [])
            last = db.query(Analysis).filter(Analysis.rule_id == r.id).order_by(Analysis.analyzed_at.desc()).first()
            open_count = db.query(Analysis).filter(Analysis.rule_id == r.id, Analysis.resolution_status == None).count() \
                if (r.alert_status or "normal") != "normal" else 0
            days7 = rule_stats.daily_counts(analyses, 7, now)
            items.append({
                "id": r.id,
                "name": r.name,
                "enabled": bool(r.enabled),
                "source": rule_stats.source_info(r.log_file_path),
                "alert_status": r.alert_status or "normal",
                "alert_started_at": rule_stats.iso(r.alert_started_at),
                "open_count": open_count,
                "days7": days7,
                "total7": sum(d["critical"] + d["warning"] + d["info"] for d in days7),
                "last": rule_stats.analysis_brief(last) if last else None,
                "mttr30_minutes": rule_stats.median_mttr(analyses),
                "last_line_received_at": rule_stats.iso(r.last_line_received_at),
                "inactivity_period_hours": r.inactivity_period_hours,
                "muted": rule_stats.is_muted(r, now),
            })
        # En alerte d'abord, puis règles actives par dernière alerte, désactivées à la fin
        items.sort(key=lambda i: (not i["enabled"], i["alert_status"] == "normal",
                                  -(datetime.fromisoformat(i["last"]["analyzed_at"][:-1]).timestamp() if i["last"] else 0)))
        return {
            "rules": items,
            "kpis": {
                "alert_count": sum(1 for i in items if i["enabled"] and i["alert_status"] != "normal"),
                "total7": sum(i["total7"] for i in items),
                "mttr30_minutes": rule_stats.median_mttr(recent),
                "muted_count": sum(1 for i in items if i["muted"]),
            },
        }
    finally:
        db.close()


@router.post("/probe-test")
async def probe_test(body: ProbeBody):
    """Teste une sonde de santé avant de l'enregistrer."""
    _choice(body.probe_type, PROBE_TYPES, "probe_type")
    if not body.probe_target.strip():
        raise HTTPException(status_code=422, detail="probe_target_required")
    res = await run_probe(body.probe_type, body.probe_target, body.probe_expect)
    return {**res, "at": rule_stats.iso(res["at"])}


@router.get("/{rule_id}")
def get_rule(rule_id: int):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")
        return {
            "id": rule.id,
            "name": rule.name,
            "log_file_path": rule.log_file_path,
            "keywords": rule.get_keywords(),
            "application_context": rule.application_context,
            "enabled": rule.enabled,
            "notify_on_match": rule.notify_on_match,
            "context_lines": rule.context_lines or 5,
            "anti_spam_delay": rule.anti_spam_delay or 60,
            "notify_severity_threshold": rule.notify_severity_threshold or "info",
            "excluded_patterns": rule.get_excluded_patterns(),
            "last_learning_session_id": rule.last_learning_session_id,
            "inactivity_warning_enabled": rule.inactivity_warning_enabled,
            "inactivity_period_hours": rule.inactivity_period_hours,
            "inactivity_notify": rule.inactivity_notify,
            "last_line_received_at": rule.last_line_received_at.isoformat() + "Z" if rule.last_line_received_at else None,
            # MON-18
            "alert_status": rule.alert_status or "normal",
            "alert_started_at": rule.alert_started_at.isoformat() + "Z" if rule.alert_started_at else None,
            "resolution_mode": rule.resolution_mode or "timeout",
            "resolution_timeout_minutes": rule.resolution_timeout_minutes or 30,
            "resolution_patterns": rule.get_resolution_patterns(),
            "resolution_patterns_weighted": rule.get_weighted_resolution_patterns(),
            "resolution_ai_enabled": rule.resolution_ai_enabled or False,
            "resolution_notify_search": rule.resolution_notify_search or False,
            "resolution_notify_resolved": rule.resolution_notify_resolved or False,
            "resolution_calm_mode": rule.resolution_calm_mode or "adaptive",
            "resolution_ai_mode": rule.resolution_ai_mode or "ambiguous",
            "resolution_learn": rule.resolution_learn is not False,
            "probe_type": rule.probe_type or "none",
            "probe_target": rule.probe_target or "",
            "probe_expect": rule.probe_expect or "",
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
            anti_spam_delay=rule_data.anti_spam_delay,
            notify_severity_threshold=rule_data.notify_severity_threshold,
            inactivity_warning_enabled=rule_data.inactivity_warning_enabled,
            inactivity_period_hours=rule_data.inactivity_period_hours,
            inactivity_notify=rule_data.inactivity_notify,
            # MON-18
            alert_status=rule_data.alert_status,
            resolution_mode=rule_data.resolution_mode,
            resolution_timeout_minutes=rule_data.resolution_timeout_minutes,
            resolution_notify_resolved=rule_data.resolution_notify_resolved,
            resolution_calm_mode=_choice(rule_data.resolution_calm_mode, CALM_MODES, "calm_mode"),
            resolution_ai_mode=_choice(rule_data.resolution_ai_mode, AI_MODES, "ai_mode"),
            resolution_ai_enabled=rule_data.resolution_ai_mode != "never",
            resolution_learn=rule_data.resolution_learn,
            probe_type=_choice(rule_data.probe_type, PROBE_CHOICES, "probe_type"),
            probe_target=rule_data.probe_target.strip(),
            probe_expect=rule_data.probe_expect.strip(),
        )
        rule.set_keywords(rule_data.keywords)
        rule.set_excluded_patterns(rule_data.excluded_patterns)
        rule.set_resolution_patterns(rule_data.resolution_patterns)
        db.add(rule)
        db.commit()
        db.refresh(rule)

        # Recharger les règles Syslog
        try:
            from app.services.syslog_receiver import syslog_receiver
            syslog_receiver.load_config()
        except Exception:
            pass

        return {"id": rule.id, "message": "Règle créée"}
    finally:
        db.close()


@router.put("/{rule_id}")
def update_rule(rule_id: int, rule_data: RuleUpdate):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")

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
        if rule_data.anti_spam_delay is not None:
            rule.anti_spam_delay = rule_data.anti_spam_delay
        if rule_data.notify_severity_threshold is not None:
            rule.notify_severity_threshold = rule_data.notify_severity_threshold
        if rule_data.excluded_patterns is not None:
            rule.set_excluded_patterns(rule_data.excluded_patterns)
        if rule_data.last_learning_session_id is not None:
            # -1 is the sentinel value meaning "clear the session link"
            rule.last_learning_session_id = None if rule_data.last_learning_session_id == -1 else rule_data.last_learning_session_id
        if rule_data.inactivity_warning_enabled is not None:
            rule.inactivity_warning_enabled = rule_data.inactivity_warning_enabled
        if rule_data.inactivity_period_hours is not None:
            rule.inactivity_period_hours = rule_data.inactivity_period_hours
        if rule_data.inactivity_notify is not None:
            rule.inactivity_notify = rule_data.inactivity_notify
        # MON-18
        if rule_data.alert_status is not None:
            rule.alert_status = rule_data.alert_status
        if rule_data.resolution_mode is not None:
            rule.resolution_mode = rule_data.resolution_mode
        if rule_data.resolution_timeout_minutes is not None:
            rule.resolution_timeout_minutes = rule_data.resolution_timeout_minutes
        if rule_data.resolution_patterns is not None:
            rule.set_resolution_patterns(rule_data.resolution_patterns)
        if rule_data.resolution_ai_enabled is not None:
            rule.resolution_ai_enabled = rule_data.resolution_ai_enabled
        if rule_data.resolution_notify_search is not None:
            rule.resolution_notify_search = rule_data.resolution_notify_search
        if rule_data.resolution_notify_resolved is not None:
            rule.resolution_notify_resolved = rule_data.resolution_notify_resolved
        if rule_data.resolution_calm_mode is not None:
            rule.resolution_calm_mode = _choice(rule_data.resolution_calm_mode, CALM_MODES, "calm_mode")
        if rule_data.resolution_ai_mode is not None:
            rule.resolution_ai_mode = _choice(rule_data.resolution_ai_mode, AI_MODES, "ai_mode")
            rule.resolution_ai_enabled = rule.resolution_ai_mode != "never"
        if rule_data.resolution_learn is not None:
            rule.resolution_learn = rule_data.resolution_learn
        if rule_data.probe_type is not None:
            rule.probe_type = _choice(rule_data.probe_type, PROBE_CHOICES, "probe_type")
        if rule_data.probe_target is not None:
            rule.probe_target = rule_data.probe_target.strip()
        if rule_data.probe_expect is not None:
            rule.probe_expect = rule_data.probe_expect.strip()

        db.commit()

        # Recharger les règles Syslog
        try:
            from app.services.syslog_receiver import syslog_receiver
            syslog_receiver.load_config()
        except Exception:
            pass

        return {"id": rule.id, "message": "Règle mise à jour"}
    finally:
        db.close()


@router.delete("/{rule_id}")
def delete_rule(rule_id: int):
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")
        db.delete(rule)
        db.commit()

        # Recharger les règles Syslog
        try:
            from app.services.syslog_receiver import syslog_receiver
            syslog_receiver.load_config()
        except Exception:
            pass

        return {"message": "Règle supprimée"}
    finally:
        db.close()


def _snapshot(rule: Rule) -> Optional[dict]:
    snap = resolution_service.incident_snapshot(rule) if resolution_service else None
    if not snap:
        return None
    for key in ("started_at", "last_error_at", "next_check_at"):
        snap[key] = rule_stats.iso(snap[key])
    if snap.get("probe"):
        snap["probe"] = {**snap["probe"], "at": rule_stats.iso(snap["probe"]["at"])}
    return snap


@router.get("/{rule_id}/summary")
def rule_summary(rule_id: int):
    """Onglet Vue d'ensemble d'une règle : incident en cours, activité, statistiques 30 j, événements récurrents."""
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")
        now = datetime.utcnow()
        analyses = db.query(Analysis).filter(Analysis.rule_id == rule_id,
                                             Analysis.analyzed_at >= now - timedelta(days=30)).all()
        open_analyses = sorted((a for a in db.query(Analysis).filter(Analysis.rule_id == rule_id,
                                                                    Analysis.resolution_status == None).all()
                                if a.analyzed_at), key=lambda a: a.analyzed_at)
        incident = _snapshot(rule)
        if incident:
            incident["open_count"] = len(open_analyses)
            incident["summary"] = rule_stats.analysis_brief(open_analyses[-1])["summary"] if open_analyses else None
        resolved = [a for a in analyses if a.resolution_status == "resolved"]
        auto_resolved = [a for a in resolved if (a.resolution_line or "") != "Resolution manuelle par l'utilisateur"]
        verdicts_ai = db.query(ResolutionVerdict).filter(ResolutionVerdict.rule_id == rule_id,
                                                         ResolutionVerdict.created_at >= now - timedelta(days=30),
                                                         ResolutionVerdict.ai_confidence != None).count()
        buf = (orchestrator._buffers.get(rule_id) if orchestrator else None) or {}
        last = db.query(Analysis).filter(Analysis.rule_id == rule_id).order_by(Analysis.analyzed_at.desc()).first()
        week = [a for a in analyses if a.analyzed_at >= now - timedelta(days=7)]
        latest = db.query(Analysis).filter(Analysis.rule_id == rule_id).order_by(Analysis.analyzed_at.desc()).limit(3).all()
        return {
            "incident": incident,
            "activity": {
                "buffer_active": bool(buf.get("task")),
                "buffer_lines": len(buf.get("lines", [])),
                "last_line_received_at": rule_stats.iso(rule.last_line_received_at),
                "muted": rule_stats.is_muted(rule, now),
                "inactivity_period_hours": rule.inactivity_period_hours,
                "last_analysis_at": rule_stats.iso(last.analyzed_at) if last else None,
                "last_analysis_severity": last.severity if last else None,
            },
            "stats30": {
                "days": rule_stats.daily_counts(analyses, 30, now),
                "total": len(analyses),
                "mttr_minutes": rule_stats.median_mttr(analyses),
                "auto_resolved": len(auto_resolved),
                "resolved": len(resolved),
                "notified": sum(1 for a in analyses if a.notified),
                "ai_calls": len(analyses) + verdicts_ai,
                "ai_checks": verdicts_ai,
            },
            "recurring": rule_stats.recurring_events(week),
            "latest": [rule_stats.analysis_brief(a) for a in latest],
        }
    finally:
        db.close()


@router.get("/{rule_id}/resolution")
def rule_resolution(rule_id: int):
    """Onglet Résolution : conditions de clôture, chronologie de l'incident, motifs, historique des vérifications."""
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")
        now = datetime.utcnow()
        verdicts = db.query(ResolutionVerdict).filter(
            ResolutionVerdict.rule_id == rule_id, ResolutionVerdict.created_at >= now - timedelta(days=30)
        ).order_by(ResolutionVerdict.created_at.desc()).all()
        incident = _snapshot(rule)
        timeline = []
        if incident:
            start = rule.alert_started_at or now
            timeline.append({"kind": "opened", "at": rule_stats.iso(start)})
            for a in db.query(Analysis).filter(Analysis.rule_id == rule_id, Analysis.resolution_status == None,
                                               Analysis.analyzed_at != None).order_by(Analysis.analyzed_at).all():
                timeline.append({"kind": "analysis", "at": rule_stats.iso(a.analyzed_at), "id": a.id,
                                 "severity": a.severity or "info", "summary": rule_stats.analysis_brief(a)["summary"]})
            for v in verdicts:
                if v.created_at and v.created_at >= start:
                    timeline.append({"kind": "verdict", "at": rule_stats.iso(v.created_at), "outcome": v.outcome,
                                     "trigger": v.trigger, "confidence": v.ai_confidence,
                                     "explanation": v.ai_explanation})
            if incident.get("probe"):
                timeline.append({"kind": "probe", "at": incident["probe"]["at"], "ok": incident["probe"]["ok"],
                                 "detail": incident["probe"]["detail"]})
            order = {"opened": 0, "analysis": 1, "probe": 2, "verdict": 3}
            timeline.sort(key=lambda e: ((e["at"] or "")[:19], order.get(e["kind"], 9)))
            if incident.get("next_check_at"):
                timeline.append({"kind": "next", "at": incident["next_check_at"]})
        counts = {}
        for v in verdicts:
            counts[v.outcome] = counts.get(v.outcome, 0) + 1
        patterns = rule_stats.pattern_rows(rule, verdicts)
        return {
            "enabled": (rule.resolution_mode or "timeout") != "disabled",
            "settings": {
                "calm_mode": rule.resolution_calm_mode or "adaptive",
                "timeout_minutes": rule.resolution_timeout_minutes or 30,
                "success_lines": (rule.resolution_mode or "timeout") in ("pattern", "both"),
                "ai_mode": rule.resolution_ai_mode or "ambiguous",
                "learn": rule.resolution_learn is not False,
                "probe_type": rule.probe_type or "none",
                "probe_target": rule.probe_target or "",
            },
            "incident": incident,
            "timeline": timeline,
            "patterns": [p for p in patterns if p["status"] == "active"],
            "suggestions": [p for p in patterns if p["status"] == "suggested"],
            "verdicts": [{
                "id": v.id, "at": rule_stats.iso(v.created_at), "trigger": v.trigger, "outcome": v.outcome,
                "confidence": v.ai_confidence, "explanation": v.ai_explanation,
                "resolution_line": v.resolution_line,
                "patterns": json.loads(v.resolution_patterns_json or "[]"),
            } for v in verdicts[:50]],
            "verdict_counts": counts,
        }
    finally:
        db.close()


def _pattern_action(rule_id: int, pattern: str, action):
    pattern = (pattern or "").strip()
    if not pattern:
        raise HTTPException(status_code=422, detail="pattern_required")
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="rule_not_found")
        action(rule, pattern)
        db.commit()
        return {"status": "ok", "pattern": pattern}
    finally:
        db.close()


@router.post("/{rule_id}/resolution-patterns")
def add_resolution_pattern(rule_id: int, body: PatternBody):
    """Ajoute un motif de résolution saisi par l'utilisateur."""
    return _pattern_action(rule_id, body.pattern, lambda r, p: r.set_pattern_status(p, "active", origin="manual"))


@router.post("/{rule_id}/resolution-patterns/accept")
def accept_resolution_pattern(rule_id: int, body: PatternBody):
    """Valide un motif suggéré par l'IA : il sert désormais à détecter le retour à la normale."""
    return _pattern_action(rule_id, body.pattern, lambda r, p: r.set_pattern_status(p, "active", origin="learned"))


@router.post("/{rule_id}/resolution-patterns/reject")
def reject_resolution_pattern(rule_id: int, body: PatternBody):
    """Refuse un motif suggéré : il ne sera plus proposé."""
    return _pattern_action(rule_id, body.pattern, lambda r, p: r.set_pattern_status(p, "rejected"))


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
async def test_rule(rule_id: int, request: Request):
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
            raise HTTPException(status_code=404, detail="rule_not_found")

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

        config_dict = config_to_dict(db.query(GlobalConfig).first())

        # Même pipeline qu'en production ; les lignes précédentes servent de contexte
        raw = {"total": 1, "window_s": 0, "lines": [last_line]}
        context = {"before": [clean_log_line(l) for l in last_lines[:-1]], "between": None, "after": []}
        logger.debug("TestRule", f"Envoi à Ollama (via stream) — modèle={config_dict.get('ollama_model')}")

        from app.routers.utils import cancel_on_disconnect
        result = await cancel_on_disconnect(
            request,
            orchestrator.analyze_alert(rule, raw, config_dict, db, context=context)
        )
        response = result["markdown"]
        severity = result["severity"]
        logger.debug("TestRule", f"Réponse Ollama reçue ({len(response)} chars), sévérité : {severity}")

        analysis = Analysis(
            rule_id=rule.id,
            triggered_lines_json=json.dumps(raw, ensure_ascii=False),
            notified=False,
        )
        orchestrator.apply_result(analysis, result)
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        # Envoyer une notification (comme en production)
        if rule.notify_on_match:
            severity_levels = {"info": 0, "warning": 1, "critical": 2}
            sev_val = severity_levels.get(severity, 0)
            threshold_val = severity_levels.get(rule.notify_severity_threshold, 0)
            
            if sev_val < threshold_val:
                logger.debug("TestRule", f"Notification de test ignorée: la sévérité '{severity}' est inférieure au seuil '{rule.notify_severity_threshold}'.")
                return {
                    "status": "ok",
                    "id": analysis.id,
                    "rule_id": analysis.rule_id,
                    "triggered_line": analysis.triggered_line,
                    "ollama_response": analysis.ollama_response,
                    "severity": analysis.severity,
                    "analyzed_at": analysis.analyzed_at.isoformat() if analysis.analyzed_at else None,
                    "notification_skipped": True
                }

            logger.debug("TestRule", f"Envoi notification via '{config_dict.get('notification_method')}'")
            notifier = NotificationService()
            subject = f"[Sentinel TEST] Alerte {severity.upper()} : {rule.name}"
            
            lang = config_dict.get("site_lang", "fr")
            # Si Apprise ou Discord, on prépare une version Markdown plus lisible
            if config_dict.get("notification_method") in ("apprise", "discord"):
                body = f"""### 🧪 Test Log to LLM Sentinel : {rule.name}
**{nt('severity', lang)}:** {severity.upper()}

**Ligne déclenchante:**
`{last_line}`

**Analyse Ollama:**
{response}

_Ceci est un test manuel depuis l'interface Log to LLM Sentinel._
"""
            else:
                body = f"""
                <h2>🧪 Test Log to LLM Sentinel</h2>
                <p><strong>Règle:</strong> {rule.name}</p>
                <p><strong>Ligne déclenchante:</strong> <code>{last_line}</code></p>
                <p><strong>Analyse Ollama:</strong></p>
                <blockquote>{response}</blockquote>
                <p><strong>Sévérité:</strong> {severity}</p>
                <hr><p><em>Ceci est un test manuel depuis l'interface Log to LLM Sentinel.</em></p>
                """

            # Gestion du résumé IA si nécessaire
            max_chars = config_dict.get("apprise_max_chars", 1900)
            notify_body = body

            if config_dict.get("notification_method") in ("apprise", "discord") and len(body) > max_chars and analysis.summary:
                summary = analysis.summary  # résumé structuré déjà produit par l'analyse
            elif config_dict.get("notification_method") in ("apprise", "discord") and len(body) > max_chars:
                logger.debug("TestRule", f"Analyse trop longue ({len(body)} chars), demande de résumé simplifié à Ollama...")
                summary_prompt = f"Résume cette analyse de log :\n{response}"
                async with orchestrator._ollama_semaphore:
                    try:
                        coro = orchestrator.ollama.analyze_async(
                            prompt=summary_prompt,
                            url=config_dict.get("ollama_url"),
                            model=config_dict.get("ollama_model")
                        )
                        summary = await cancel_on_disconnect(
                            request,
                            asyncio.wait_for(coro, timeout=60.0)
                        )
                    except asyncio.TimeoutError:
                        summary = "[Erreur Ollama] Délai d'attente dépassé pour le résumé (60s)"
            if config_dict.get("notification_method") in ("apprise", "discord") and len(body) > max_chars:
                if not (isinstance(summary, str) and summary.startswith("[Erreur Ollama]")):
                    notify_body = f"""### 🧪 Test Log to LLM Sentinel (Résumé) : {rule.name}
**{nt('severity', lang)}:** {severity.upper()}

**Résumé:**
{summary}

_(Analyse complète disponible dans l'interface)_
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
            "status": "ok",
            "id": analysis.id,
            "rule_id": analysis.rule_id,
            "triggered_line": analysis.triggered_line,
            "ollama_response": analysis.ollama_response,
            "severity": analysis.severity,
            "analyzed_at": analysis.analyzed_at.isoformat() if analysis.analyzed_at else None,
        }
    finally:
        db.close()


@router.get("/{rule_id}/download")
def download_rule_log(rule_id: int):
    from fastapi.responses import FileResponse
    from pathlib import Path
    from app.routers.files import _resolve_under_roots
    
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Règle introuvable")
        
        path_str = rule.log_file_path
        target_path = None
        
        if path_str.startswith("[WEBHOOK]:"):
            token = path_str.split(":", 1)[1]
            data_dir = os.environ.get("SENTINEL_DATA_DIR", "/app/data")
            target_path = Path(data_dir) / "webhooks" / f"{token}.log"
        elif path_str.startswith("[SYSLOG]:"):
            hostname = path_str.split(":", 1)[1]
            data_dir = os.environ.get("SENTINEL_DATA_DIR", "/app/data")
            target_path = Path(data_dir) / "syslog" / f"{hostname}.log"
        else:
            target_path = _resolve_under_roots(path_str)
            
        if not target_path or not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="Fichier log introuvable ou vide")
            
        # Clean file name for header
        safe_name = "".join(c for c in rule.name if c.isalnum() or c in "-_ ").strip().replace(" ", "_")
        if not safe_name:
            safe_name = f"rule_{rule.id}"
            
        return FileResponse(
            path=target_path,
            filename=f"{safe_name}.log",
            media_type="application/octet-stream"
        )
    finally:
        db.close()
