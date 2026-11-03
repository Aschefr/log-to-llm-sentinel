"""Apprentissage des mots-clés d'une règle sur une période (KW-2).

Deux modes :
- history : relit toute la période passée (fichier courant et fichier tourné .1) ;
- live : observe la source pendant N heures, puis analyse ce qui est arrivé.

Toutes les lignes de la période sont regroupées en modèles (valeurs variables masquées) et comptées,
en un seul passage. L'arbitre IA reçoit les angles morts (modèles d'erreur qu'aucun mot-clé n'attrape)
et, pour chaque mot-clé, les modèles qu'il attrape : il ajoute, retire ou exclut. Ses décisions passent
les mêmes garde-fous que l'ajustement continu (keyword_tuning) et sont appliquées ou proposées.

Session du pilote automatique (summary["autopilot"]) : sans historique suffisant, la session devient une observation
de 24 h ; à la fin, l'IA définit aussi le nom et le contexte de la règle, et le rythme des lignes fixe la fenêtre de
regroupement et le délai de silence (voir app/services/autopilot.py).
"""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from app import logger as app_logger
from app.database import SessionLocal
from app.models import GlobalConfig, KeywordChange, KeywordLearningSession, Rule
from app.services import autopilot as ap
from app.services import event_notes
from app.services import keyword_tuning as kt
from app.services.app_config import config_to_dict
from app.services.prompts import AUTOPILOT_PROFILE_SCHEMA
from app.services.log_source import read_tail_lines, resolve_path
from app.utils import keyword_match

MODES = ("history", "live")
MAX_HOURS = {"history": 24 * 30, "live": 72}
MAX_SCAN_BYTES = 300 * 1024 * 1024
MAX_SIGNATURES = 20000
MAX_TIMES_PER_TEMPLATE = 300    # instants gardés par modèle d'erreur (rythme des alertes, pilote automatique)
BLIND_MIN_COUNT = 2
BLIND_MAX = 12
EVENT_MAX_TEMPLATES = 40        # au-delà, un journal sans événements nommés ne se résume plus en quelques lignes
NOISE_MAX = 8
ACTIVE = ("pending", "scanning", "refining")

_tuning: Optional[kt.KeywordTuningService] = None
_tasks = {}


def set_tuning_service(service: kt.KeywordTuningService):
    global _tuning
    _tuning = service


# ── Horodatages ────────────────────────────────────────────────────────────────────

_TS_PATTERNS = [
    # ISO 8601, avec fuseau éventuel : 2026-04-26T19:23:02.123+02:00
    (re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s?(Z|[+-]\d{2}:?\d{2})?"), "iso"),
    # Syslog / journald : Apr 26 19:23:02 (sans année)
    (re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})\b"), "syslog"),
    # Nginx / Apache : 26/Apr/2026:19:23:02 +0200
    (re.compile(r"(\d{2}/[A-Za-z]{3}/\d{4}):(\d{2}:\d{2}:\d{2})(?:\s([+-]\d{4}))?"), "clf"),
]


def _tz(offset: Optional[str]) -> Optional[timedelta]:
    if not offset:
        return None
    if offset == "Z":
        return timedelta(0)
    sign = -1 if offset[0] == "-" else 1
    digits = offset[1:].replace(":", "")
    return sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))


def parse_ts(line: str, now: datetime):
    """(datetime naïf, décalage UTC connu ou None). Le décalage connu permet de convertir en UTC."""
    for pattern, kind in _TS_PATTERNS:
        m = pattern.search(line[:200])
        if not m:
            continue
        try:
            if kind == "iso":
                return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S"), _tz(m.group(3))
            if kind == "syslog":
                dt = datetime.strptime(f"{now.year} {m.group(1)} {m.group(2)} {m.group(3)}", "%Y %b %d %H:%M:%S")
                if dt > now + timedelta(days=2):  # journal de décembre lu en janvier
                    dt = dt.replace(year=dt.year - 1)
                return dt, None
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%b/%Y %H:%M:%S")
            return dt, _tz(m.group(3)[:3] + ":" + m.group(3)[3:] if m.group(3) else None)
        except ValueError:
            continue
    return None, None


def estimate_local_offset(path: Path) -> timedelta:
    """Décalage des horodatages sans fuseau par rapport à l'UTC, déduit de la dernière ligne et de la date
    de modification du fichier (arrondi au quart d'heure ; 0 si incohérent)."""
    try:
        mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
    except OSError:
        return timedelta(0)
    now = datetime.utcnow()
    for line in reversed(read_tail_lines(path, max_bytes=64 * 1024)[-50:]):
        dt, off = parse_ts(line, now)
        if dt is None:
            continue
        if off is not None:
            return timedelta(0)
        delta = (dt - mtime).total_seconds()
        quarter = round(delta / 900) * 900
        return timedelta(seconds=quarter) if abs(quarter) <= 14 * 3600 else timedelta(0)
    return timedelta(0)


def _to_utc(dt: datetime, off: Optional[timedelta], local_offset: timedelta) -> datetime:
    return dt - (off if off is not None else local_offset)


# ── Lecture de la période ──────────────────────────────────────────────────────────

_ARCHIVE_SUFFIXES = (".gz", ".zip", ".bz2", ".xz", ".zst", ".7z")


def source_files(path: Path, since: Optional[datetime] = None) -> list:
    """Fichier courant et fichiers tournés (« app.log.1 », « app.log.20261102-170332.916 »…, archives exclues),
    du plus ancien au plus récent. since (UTC) : fichiers tournés modifiés après cette date seulement."""
    rotated = []
    try:
        siblings = list(path.parent.iterdir()) if path.parent.is_dir() else []
    except OSError:
        siblings = []
    for p in siblings:
        if not p.name.startswith(path.name + ".") or p.name.lower().endswith(_ARCHIVE_SUFFIXES):
            continue
        try:
            if not p.is_file():
                continue
            mtime = datetime.utcfromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if since is None or mtime >= since:
            rotated.append((mtime, p))
    files = [p for _, p in sorted(rotated)]
    return files + ([path] if path.is_file() else [])


def scan(files: list, start: datetime, end: datetime, keywords: list, excluded: list,
         progress: Callable[[int], None], start_offset: Optional[int] = None) -> dict:
    """Regroupe en modèles toutes les lignes de [start, end) (UTC). Une ligne sans horodatage hérite de la
    précédente (traces sur plusieurs lignes). start_offset : position de départ dans le dernier fichier (mode live,
    0 = tout le fichier) ; ces lignes sont prises sans filtre de date."""
    now = datetime.utcnow()
    local_offset = estimate_local_offset(files[-1]) if files else timedelta(0)
    templates = {}
    total = sum(f.stat().st_size for f in files) or 1
    done = 0
    lines_in, overflow = 0, 0
    first_ts = last_ts = None
    gaps = []  # écarts d'au moins une minute entre deux lignes (silences habituels de la source)
    for i, f in enumerate(files):
        live_file = start_offset is not None and i == len(files) - 1 and f.stat().st_size >= start_offset
        current_ts = None
        with open(f, "rb") as fh:
            if live_file:
                fh.seek(start_offset)
                done += start_offset
            for raw in fh:
                done += len(raw)
                if done > MAX_SCAN_BYTES:
                    break
                if done % (4 * 1024 * 1024) < len(raw):
                    progress(min(99, int(100 * done / total)))
                line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
                if not line.strip() or len(line) > 10_000:
                    continue
                dt, off = parse_ts(line, now)
                if dt is not None:
                    current_ts = _to_utc(dt, off, local_offset)
                if not live_file:
                    if current_ts is None or current_ts < start or current_ts >= end:
                        continue
                lines_in += 1
                if current_ts is not None:
                    if last_ts is not None and (current_ts - last_ts).total_seconds() >= 60:
                        gaps.append((current_ts - last_ts).total_seconds())
                    first_ts = first_ts or current_ts
                    last_ts = current_ts
                status, hits = keyword_match.classify(line, keywords, excluded)
                sig = kt.signature(line)
                t = templates.get(sig)
                if t is None:
                    if len(templates) >= MAX_SIGNATURES:
                        overflow += 1
                        continue
                    t = templates[sig] = {"count": 0, "examples": [], "status": status, "hits": set(hits),
                                          "error_level": kt.error_level(line), "error_like": kt.error_like(line),
                                          "times": []}
                t["count"] += 1
                if not t["error_like"] and kt.error_like(line):
                    t["error_like"], t["error_level"] = True, kt.error_level(line)
                if t["error_like"] and current_ts is not None and len(t["times"]) < MAX_TIMES_PER_TEMPLATE:
                    t["times"].append(current_ts)
                if len(t["examples"]) < 3 and line[:500] not in t["examples"]:
                    t["examples"].append(line[:500])
    progress(100)
    return {"templates": templates, "lines": lines_in, "overflow": overflow, "first": first_ts, "last": last_ts,
            "local_offset_h": local_offset.total_seconds() / 3600, "gaps": gaps}


def event_candidates(result: dict, days: float, room: int) -> list:
    """Lignes sans niveau d'erreur d'une source qui journalise surtout des événements (audit, sécurité, réseau). Elle
    n'a peut-être aucune ligne d'erreur : l'arbitre doit quand même voir ce qu'elle raconte pour choisir ce qui mérite
    une analyse. Les événements nommés (CEF) sont regroupés par nom ; sans nom, seulement si le journal reste lisible."""
    others = [t for t in result["templates"].values() if t["status"] is None and not t["error_like"]]
    groups = {}
    for t in others:
        cef = kt.cef_event(t["examples"][0]) if t["examples"] else None
        if cef:
            g = groups.setdefault(cef[0], {"count": 0, "examples": [], "signature": cef[0]})
            g["count"] += t["count"]
            g["examples"] = (g["examples"] + t["examples"])[:3]
    if not groups and len(result["templates"]) <= EVENT_MAX_TEMPLATES:
        for sig, t in result["templates"].items():
            if t["status"] is None and not t["error_like"]:
                groups[sig] = {"count": t["count"], "examples": t["examples"], "signature": sig}
    ordered = sorted(groups.values(), key=lambda g: g["count"])  # les plus rares d'abord : la routine est la plus fréquente
    return [{"template_id": None, "kind": "event", "signature": g["signature"], "count": g["count"], "examples": g["examples"],
             "first": result["first"], "last": result["last"], "per_day": round(g["count"] / days, 1)}
            for g in ordered[:room]]


def scan_candidates(db, rule: Rule, result: dict, days: float):
    """Angles morts et mots-clés à examiner, au format de keyword_tuning.build_arbiter_messages."""
    templates = result["templates"]
    blind = [{"template_id": None, "signature": sig, "count": t["count"], "examples": t["examples"],
              "first": result["first"], "last": result["last"], "per_day": round(t["count"] / days, 1)}
             for sig, t in templates.items()
             if t["status"] is None and t["error_like"] and t["count"] >= BLIND_MIN_COUNT]
    blind.sort(key=lambda c: c["count"], reverse=True)
    blind = blind[:BLIND_MAX]
    # Une règle du pilote sans mot-clé ne peut rien détecter : c'est à lui de trouver ce qui mérite une analyse
    if ap.enabled(rule) and not rule.get_keywords() and len(blind) < BLIND_MAX:
        blind += event_candidates(result, days, BLIND_MAX - len(blind))

    alerts = kt.alert_stats(db, rule)
    noise = []
    for kw in rule.get_keywords():
        matched = [t for t in templates.values() if t["status"] == "matched" and kw in t["hits"]]
        if not matched:
            continue
        hits = sum(t["count"] for t in matched)
        a = alerts.get(kw) or {"alerts": 0, "severities": {}, "irrelevant": [], "important_lines": []}
        lines = [{"count": t["count"], "example": t["examples"][0],
                  "severities": {"error-level" if t["error_level"] else "no-error-level": t["count"]}}
                 for t in sorted(matched, key=lambda t: t["count"], reverse=True)[:6]]
        important = list(a["important_lines"]) + [ex for t in matched if t["error_level"] for ex in t["examples"]]
        noise.append({"keyword": kw, "alerts": a["alerts"], "severities": dict(a["severities"]),
                      "irrelevant": len(a["irrelevant"]), "irrelevant_ids": a["irrelevant"],
                      "per_day": round(hits / days, 1), "lines": lines, "important_lines": important[:300],
                      "focus": False, "hits": hits})
    noise.sort(key=lambda c: c["hits"], reverse=True)
    return blind, noise[:NOISE_MAX]


# ── Sessions ───────────────────────────────────────────────────────────────────────

def _update(session_id: int, **fields):
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        if s:
            for k, v in fields.items():
                setattr(s, k, v)
            db.commit()
    finally:
        db.close()


def _status(session_id: int) -> Optional[str]:
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        return s.status if s else None
    finally:
        db.close()


def start_session(rule_id: int, mode: str = "history", hours: int = 168, refresh: bool = False) -> int:
    """Crée une session et lance la tâche. Lève ValueError si la demande est invalide.
    Pour une règle en pilote automatique pas encore prête, la session est celle du pilote (elle définit toute la règle) ;
    refresh : relecture hebdomadaire du pilote (le contexte n'est revu que si le journal a changé)."""
    if mode not in MODES:
        raise ValueError("invalid_mode")
    hours = max(1, min(int(hours), MAX_HOURS[mode]))
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if not rule:
            raise ValueError("rule_not_found")
        for running in db.query(KeywordLearningSession).filter(KeywordLearningSession.rule_id == rule_id,
                                                               KeywordLearningSession.status.in_(ACTIVE)).all():
            if running.log_file_path == rule.log_file_path:
                return running.id
            # La source de la règle a changé : cette session lirait l'ancienne et appliquerait le résultat à la règle
            running.status, running.error_message = "error", "Source de la règle modifiée"
        now = datetime.utcnow()
        start, end = (now - timedelta(hours=hours), now) if mode == "history" else (now, now + timedelta(hours=hours))
        summary = {}
        ap_state = ap.load(rule)
        if ap_state.get("enabled") and (refresh or ap_state.get("status") != "active"):
            summary.update({"autopilot": True, "refresh": refresh})
        if mode == "live":
            path = resolve_path(rule.log_file_path)
            summary["start_offset"] = path.stat().st_size if path.is_file() else 0
        s = KeywordLearningSession(
            rule_id=rule_id, log_file_path=rule.log_file_path, period_start=start, period_end=end,
            granularity_s=hours * 3600, mode=mode, status="pending", total_packets=100, completed_packets=0,
            previous_keywords_json=json.dumps(rule.get_keywords()),
            previous_exclusions_json=json.dumps(rule.get_excluded_patterns()),
            summary_json=json.dumps(summary), raw_keywords_json="[]", final_keywords_json="[]",
            final_exclusions_json="[]", raw_exclusions_json="[]", refine_rationale_json="{}", ollama_log_json="[]")
        db.add(s)
        db.commit()
        rule.last_learning_session_id = s.id
        if summary.get("autopilot"):
            ap.set_state(rule, session_id=s.id, attempt_at=now.isoformat(),
                         **({} if refresh else {"status": "observing" if mode == "live" else "learning",
                                                "observe_until": end.isoformat() if mode == "live" else None,
                                                "error": None}))
        db.commit()
        session_id = s.id
    finally:
        db.close()
    _launch(session_id)
    app_logger.info("KeywordLearning", f"Session {session_id} ({mode}, {hours} h) lancée pour la règle {rule_id}")
    return session_id


def start_setup_session(rule_id: int) -> Optional[int]:
    """Session demandée à la création ou à l'activation du pilote : un échec n'empêche pas d'enregistrer la règle,
    mais il doit se voir dans les logs."""
    try:
        return start_session(rule_id, "history", 168)
    except Exception as e:
        app_logger.error("KeywordLearning", f"Session impossible à lancer pour la règle {rule_id} : {e}")
        return None


def delete_rule_sessions(db, rule_id: int):
    """Une règle supprimée emporte ses sessions : son identifiant peut resservir, et une session en attente se
    réveillerait alors sur la nouvelle règle. Sans sa ligne, la tâche en cours s'arrête à sa prochaine vérification."""
    db.query(KeywordLearningSession).filter(KeywordLearningSession.rule_id == rule_id).delete()


def _launch(session_id: int):
    task = asyncio.create_task(_run_session(session_id))
    _tasks[session_id] = task
    task.add_done_callback(lambda _: _tasks.pop(session_id, None))


async def resume_stuck_sessions():
    """Relance les sessions interrompues par un redémarrage (une session live reprend son attente)."""
    db = SessionLocal()
    try:
        ids = [s.id for s in db.query(KeywordLearningSession).filter(KeywordLearningSession.status.in_(ACTIVE)).all()]
    finally:
        db.close()
    for sid in ids:
        _launch(sid)


def _to_observation(session_id: int, rule_id: int, summary: dict, reason: str) -> bool:
    """Pilote sans historique suffisant : la session devient une observation de 24 h (lignes déjà présentes comprises).
    False si la source a déjà été observée trop longtemps sans résultat."""
    rounds = summary.get("observe_rounds", 0) + 1
    if rounds > ap.OBSERVE_MAX_ROUNDS:
        return False
    summary.setdefault("history_lines", summary.get("lines", 0))  # ce que l'historique contenait (parcours du pilote)
    now = datetime.utcnow()
    end = now + timedelta(hours=ap.OBSERVE_HOURS)
    summary.update({"observe_rounds": rounds, "start_offset": 0, "observe_reason": reason})
    _update(session_id, mode="live", status="pending", period_start=now, period_end=end, completed_packets=0,
            summary_json=json.dumps(summary))
    db = SessionLocal()
    try:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        if rule:
            ap.set_state(rule, status="observing", observe_until=end.isoformat(), observe_rounds=rounds)
            db.commit()
    finally:
        db.close()
    app_logger.info("Autopilot", f"Règle {rule_id} : {reason} — observation de {ap.OBSERVE_HOURS} h (tour {rounds})")
    return True


async def _run_session(session_id: int):
    try:
        db = SessionLocal()
        try:
            s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
            if not s:
                return
            rule_id, mode = s.rule_id, s.mode or "history"
            start, end = s.period_start, s.period_end
            summary = json.loads(s.summary_json or "{}")
        finally:
            db.close()
        autopilot = bool(summary.get("autopilot"))

        # Mode live : attendre la fin de l'observation
        while mode == "live" and datetime.utcnow() < end:
            if _status(session_id) not in ACTIVE:
                return
            await asyncio.sleep(min(30.0, max(1.0, (end - datetime.utcnow()).total_seconds())))
        if _status(session_id) not in ACTIVE:
            return

        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            if not rule:
                raise RuntimeError("Règle supprimée")
            keywords, excluded = rule.get_keywords(), rule.get_excluded_patterns()
            path = resolve_path(rule.log_file_path)
        finally:
            db.close()
        files = source_files(path, since=start)
        if not files:
            if autopilot and not summary.get("refresh") and _to_observation(
                    session_id, rule_id, summary, "aucune ligne reçue pour l'instant"):
                return await _run_session(session_id)
            raise RuntimeError(f"Fichier introuvable : {path}")
        if not all(os.access(f, os.R_OK) for f in files):
            raise RuntimeError(f"Fichier non lisible (permissions) : {path}")

        _update(session_id, status="scanning", completed_packets=0)
        result = await asyncio.to_thread(
            scan, files, start, end, keywords, excluded,
            lambda pct: _update(session_id, completed_packets=pct), summary.get("start_offset", 0) if mode == "live" else None)
        if _status(session_id) not in ACTIVE:
            return
        days = max(1.0, (end - start).total_seconds() / 86400)
        span_h = (result["last"] - result["first"]).total_seconds() / 3600 if result["first"] and result["last"] else 0
        summary.update({"lines": result["lines"], "templates": len(result["templates"]), "overflow": result["overflow"],
                        "local_offset_h": result["local_offset_h"]})
        if autopilot and not summary.get("refresh"):
            short = mode == "history" and ap.history_too_short(result["lines"], span_h)
            summary["history_span_h"] = round(span_h, 1)
            if (short or not result["lines"]) and _to_observation(
                    session_id, rule_id, summary, "historique insuffisant" if result["lines"] else "aucune ligne reçue"):
                return await _run_session(session_id)
        if not result["lines"]:
            _update(session_id, status="error", summary_json=json.dumps(summary),
                    error_message="Aucune ligne sur cette période : vérifie la période et que les lignes sont horodatées.")
            _autopilot_error(session_id, "no_lines")
            return

        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            config = config_to_dict(db.query(GlobalConfig).first())
            lang = config.get("ollama_prompt_lang", "fr")
            period = f"{start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC, {result['lines']} lignes"
            signatures = ap.top_signatures(result["templates"])
            profile_messages = None
            if autopilot and (not summary.get("refresh") or ap.profile_changed(ap.load(rule).get("signatures", []), signatures)):
                profile_messages = ap.build_profile_messages(lang, rule, result, period,
                                                             notes=event_notes.for_rule(db, rule.id, source_only=True))
        finally:
            db.close()

        # Pilote : l'IA décrit d'abord le journal (nom, contexte), ce qui aide ensuite l'arbitre des mots-clés
        ap_changes = []
        if profile_messages:
            _update(session_id, status="refining", summary_json=json.dumps(summary))
            profile = await _tuning.ask_arbiter(profile_messages, config, label=f"profil session {session_id}",
                                                schema=AUTOPILOT_PROFILE_SCHEMA, kind="AUTOPILOT") if _tuning else None
            if _status(session_id) not in ACTIVE:
                return
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                ap_changes += ap.apply_profile(rule, profile)
                db.commit()
            finally:
                db.close()
            summary["profile"] = profile

        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            # Ajustement désactivé sur la règle : le pilote règle le reste, pas les mots-clés
            blind, noise = scan_candidates(db, rule, result, days) if (rule.keyword_tuning_mode or "auto") != "off" else ([], [])
            messages = kt.build_arbiter_messages(lang, rule, blind, noise, period, notes=event_notes.for_rule(db, rule.id))
        finally:
            db.close()
        summary.update({"blind_spots": len(blind), "keywords_reviewed": len(noise)})
        raw = [c["examples"][0][:200] for c in blind]

        decision = None
        if blind or noise:
            _update(session_id, status="refining", summary_json=json.dumps(summary, default=str),
                    raw_keywords_json=json.dumps(raw))
            decision = await _tuning.ask_arbiter(messages, config, label=f"session {session_id}") if _tuning else None
            if decision is None:
                raise RuntimeError("L'IA n'a pas répondu (voir les logs de l'application).")
            if _status(session_id) not in ACTIVE:
                return
        _update(session_id, ollama_log_json=json.dumps({
            "prompt": messages[-1]["content"][:60000] if decision else None, "response": decision,
            "profile_prompt": profile_messages[-1]["content"][:20000] if profile_messages else None,
            "profile": summary.get("profile")}, default=str))

        db = SessionLocal()
        try:
            rule = db.query(Rule).filter(Rule.id == rule_id).first()
            changes = kt.apply_decisions(db, rule, decision, blind, noise, origin_blind="learning",
                                         origin_noise="learning", session_id=session_id) if decision else []
            if autopilot:
                # Rythme réel : fenêtre de regroupement des lignes déclenchantes, délai de silence de la source
                window = ap.grouping_window(ap.matched_timestamps(result["templates"], rule.get_keywords(),
                                                                  rule.get_excluded_patterns()))
                if window and ap.set_value(rule, "anti_spam_delay", window):
                    ap_changes.append("anti_spam_delay")
                silence = ap.silence_hours(result["gaps"], span_h)
                if silence and ap.set_value(rule, "inactivity_period_hours", silence):
                    ap_changes.append("inactivity_period_hours")
                ap.upgrade_state(rule)
                if ap.apply_notification_threshold(db, rule):
                    ap_changes.append("notify_severity_threshold")
                # Source d'événements : chaque ligne retenue est analysée, seules les conclusions sérieuses notifient
                if rule.get_keywords() and any(c.get("kind") == "event" for c in blind) \
                        and ap.set_value(rule, "notify_severity_threshold", "warning"):
                    ap_changes.append("notify_severity_threshold")
                no_trigger = not rule.get_keywords()
                if no_trigger:
                    ap.log_event(rule, ap.L_no_trigger(lang))
                kt.dismiss_pending(db, rule)
                now_iso = datetime.utcnow().isoformat()
                ap.set_state(rule, status="active", observe_until=None, error=None, signatures=signatures, no_trigger=no_trigger,
                             profiled_at=now_iso if profile_messages else ap.load(rule).get("profiled_at"),
                             refreshed_at=now_iso)
                if ap_changes:
                    ap.log_event(rule, ap.changes_text(rule, ap_changes, lang))
                summary["autopilot_changes"] = ap_changes
                summary["autopilot_values"] = {f: getattr(rule, f) for f in ap_changes}
            db.commit()
            result_changes = [kt.change_dict(c) for c in changes]
            summary["changes"] = [c["id"] for c in result_changes]
            s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
            s.status, s.validated_at = "validated", datetime.utcnow()
            s.summary_json = json.dumps(summary, default=str)
            s.raw_keywords_json = json.dumps(raw)
            s.final_keywords_json = json.dumps(rule.get_keywords())
            s.final_exclusions_json = json.dumps(rule.get_excluded_patterns())
            s.refine_rationale_json = json.dumps({c["value"]: c["reason"] for c in result_changes})
            db.commit()
            name, notify = rule.name, rule.notify_on_match
            final_keywords = rule.get_keywords()
            ap_lines = _autopilot_lines(rule, ap_changes, lang)
        finally:
            db.close()
        fr = lang == "fr"
        if autopilot and not final_keywords:
            title = f"[Sentinel] 🧭 Pilote automatique — aucun événement à analyser : {name}" if fr else f"[Sentinel] 🧭 Autopilot — nothing to analyze: {name}"
            ap_lines = ap_lines + [ap.L_no_trigger(lang)]
        elif autopilot and not summary.get("refresh"):
            title = f"[Sentinel] 🧭 Pilote automatique — règle prête : {name}" if fr else f"[Sentinel] 🧭 Autopilot — rule ready: {name}"
        else:
            title = f"[Sentinel] 🎓 Apprentissage terminé — {name}" if fr else f"[Sentinel] 🎓 Learning done — {name}"
        await kt.notify_changes(name, notify, result_changes, config, title=title, extra=ap_lines)
        app_logger.info("KeywordLearning", f"Session {session_id} terminée : {len(result_changes)} changement(s)"
                        + (f", pilote : {', '.join(ap_changes) or 'rien'}" if autopilot else ""))
    except Exception as e:
        app_logger.error("KeywordLearning", f"Session {session_id} : {e}")
        _update(session_id, status="error", error_message=str(e)[:500])
        _autopilot_error(session_id, str(e))


def _autopilot_lines(rule: Rule, fields: list, lang: str) -> list:
    fr = lang == "fr"
    labels = {"name": "Nom" if fr else "Name", "application_context": "Contexte" if fr else "Context",
              "anti_spam_delay": "Fenêtre de regroupement" if fr else "Grouping window",
              "inactivity_period_hours": "Alerte si la source est muette depuis" if fr else "Alert when the source is silent for",
              "notify_severity_threshold": "Notifications à partir de" if fr else "Notifications from"}
    values = {"anti_spam_delay": f"{rule.anti_spam_delay} s", "inactivity_period_hours": f"{rule.inactivity_period_hours} h",
              "notify_severity_threshold": rule.notify_severity_threshold or "info",
              "name": rule.name, "application_context": (rule.application_context or "")[:300]}
    return [f"- {labels[f]} : {values[f]}" for f in fields]


def _autopilot_error(session_id: int, message: str):
    """Échec de la session du pilote : la règle reste en phase d'observation (alertes de niveau erreur) ;
    l'ajustement horaire relance le pilote plus tard."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        rule = db.query(Rule).filter(Rule.id == s.rule_id).first() if s else None
        if rule and ap.load(rule).get("session_id") == session_id and ap.load(rule).get("status") != "active":
            ap.set_state(rule, status="error", error=message[:300], observe_until=None)
            db.commit()
    finally:
        db.close()


def cancel_session(session_id: int) -> bool:
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        if not s:
            return False
        if s.status in ACTIVE:
            s.status, s.error_message = "error", "Annulé par l'utilisateur"
            rule = db.query(Rule).filter(Rule.id == s.rule_id).first()
            if rule and ap.load(rule).get("session_id") == s.id and ap.load(rule).get("status") != "active":
                ap.set_state(rule, status="error", error="cancelled", observe_until=None)
            db.commit()
        return True
    finally:
        db.close()


def revert_session(session_id: int) -> Optional[dict]:
    """Annule les changements appliqués par la session (les propositions en attente sont refusées)."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        if not s:
            return None
        for c in db.query(KeywordChange).filter(KeywordChange.session_id == session_id).all():
            if c.status == "applied":
                kt.decide(db, c, "revert")
            elif c.status == "pending":
                kt.decide(db, c, "reject")
        s.status = "reverted"
        db.commit()
        rule = db.query(Rule).filter(Rule.id == s.rule_id).first()
        return {"keywords": rule.get_keywords() if rule else [], "exclusions": rule.get_excluded_patterns() if rule else []}
    finally:
        db.close()


def get_session_status(session_id: int) -> Optional[dict]:
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        if not s:
            return None
        summary = json.loads(s.summary_json or "{}")
        changes = []
        if summary.get("changes"):
            changes = [kt.change_dict(c) for c in db.query(KeywordChange).filter(
                KeywordChange.id.in_(summary["changes"])).all()]
        iso = lambda d: d.isoformat() + "Z" if d else None
        return {
            "id": s.id, "rule_id": s.rule_id, "mode": s.mode or "history", "status": s.status,
            "progress": s.completed_packets or 0, "period_start": iso(s.period_start), "period_end": iso(s.period_end),
            "summary": {k: v for k, v in summary.items() if k != "start_offset"}, "changes": changes,
            "previous_keywords": json.loads(s.previous_keywords_json or "[]"),
            "previous_exclusions": json.loads(s.previous_exclusions_json or "[]"),
            "final_keywords": json.loads(s.final_keywords_json or "[]"),
            "final_exclusions": json.loads(s.final_exclusions_json or "[]"),
            "error_message": s.error_message, "created_at": iso(s.created_at), "validated_at": iso(s.validated_at),
        }
    finally:
        db.close()


def session_exchange_text(session_id: int) -> Optional[str]:
    """Échange avec l'IA (prompt et décision) d'une session, en texte."""
    db = SessionLocal()
    try:
        s = db.query(KeywordLearningSession).filter(KeywordLearningSession.id == session_id).first()
        if not s:
            return None
        try:
            log = json.loads(s.ollama_log_json or "{}")
        except ValueError:
            log = {}
        if not isinstance(log, dict) or not (log.get("prompt") or log.get("profile_prompt")):
            return None
        text = (f"Sentinel — apprentissage des mots-clés, session #{s.id} (règle {s.rule_id})\n"
                f"Période : {s.period_start:%Y-%m-%d %H:%M} → {s.period_end:%Y-%m-%d %H:%M} UTC\n\n")
        if log.get("profile_prompt"):
            text += (f"=== Pilote automatique : extrait envoyé à l'IA ===\n{log['profile_prompt']}\n\n"
                     f"=== Nom et contexte proposés ===\n{json.dumps(log.get('profile'), ensure_ascii=False, indent=2)}\n\n")
        if log.get("prompt"):
            text += (f"=== Données envoyées à l'IA ===\n{log['prompt']}\n\n"
                     f"=== Décision de l'IA ===\n{json.dumps(log.get('response'), ensure_ascii=False, indent=2)}\n")
        return text
    finally:
        db.close()
