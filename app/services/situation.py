"""
Point de situation du dashboard : l'état des surveillances en quelques phrases.

Deux couches :
- les faits, calculés depuis la base sans IA, donc toujours à jour (sources, activité des dernières 24 h, ce qui
  reste ouvert) ;
- un court paragraphe écrit par l'IA qui relie ces faits. Il est réécrit en entier (jamais complété, donc il ne
  dérive pas), seulement quand la situation change : une alerte s'ouvre ou se ferme, une source se tait. L'IA ne
  reçoit que les faits et les résumés déjà écrits par les analyses, jamais une ligne de log brute.
"""
import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from app.database import SessionLocal
from app.models import Analysis, GlobalConfig, Rule
from app.services import analysis_events, monitoring, rule_stats
from app.services.alert_analysis import short_summary
from app.services.app_config import config_to_dict
from app.services.prompts import SITUATION_SCHEMA, SITUATION_SYSTEM
from app import logger

WINDOW_HOURS = 24
TOP_EVENTS = 5
MIN_GAP = timedelta(minutes=2)       # deux rédactions de suite au plus tôt à cet intervalle
REFRESH_AFTER = timedelta(hours=6)   # une situation inchangée mais vieillissante est réécrite (fenêtre de 24 h)
TICK_SECONDS = 60
MAX_TEXT = 700
_CITE = re.compile(r"\[#(\d+)\]")


def _new_keys(db, events: list, since: datetime) -> set:
    """Événements apparus pour la première fois dans la fenêtre : aucune occurrence plus ancienne (30 jours)."""
    keys = {(e["rule_id"], e["key"]) for e in events}
    if not keys:
        return set()
    older = db.query(Analysis.rule_id, Analysis.event_key).filter(
        Analysis.analyzed_at < since, Analysis.analyzed_at >= since - timedelta(days=30),
        Analysis.rule_id.in_({k[0] for k in keys})).distinct().all()
    return keys - {(r, k) for r, k in older}


def _manipulated(a: Analysis) -> bool:
    try:
        return json.loads(a.analysis_json or "{}").get("log_manipulation") is True
    except ValueError:
        return False


def facts(db, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    since = now - timedelta(hours=WINDOW_HOURS)
    rules = [r for r in db.query(Rule).all() if r.enabled]
    names = {r.id: r.name for r in rules}
    muted = [r for r in rules if rule_stats.is_muted(r, now)]
    heard = [r for r in rules if r not in muted and r.last_line_received_at]
    quietest = min(heard, key=lambda r: r.last_line_received_at) if heard else None

    events = [e for e in analysis_events.summarize(db, days=1) if e["rule_id"] in names]
    new = _new_keys(db, events, since)
    # Ce qui est encore ouvert d'abord, puis le nouveau, puis le plus fréquent ; deux événements par règle au plus,
    # sinon une seule panne qui se répète cache tout le reste
    ranked = sorted(events, key=lambda e: (not (e["open_count"] and e["rule_in_alert"]), (e["rule_id"], e["key"]) not in new,
                                           -e["count"], e["last_at"] or ""))
    picked, per_rule = [], {}
    for e in ranked:
        if per_rule.get(e["rule_id"], 0) < 2:
            picked.append(e)
            per_rule[e["rule_id"]] = per_rule.get(e["rule_id"], 0) + 1
        if len(picked) == TOP_EVENTS:
            break
    top = []
    for e in picked:
        a = db.query(Analysis).filter(Analysis.id == e["analysis_id"]).first()
        top.append({
            "rule_id": e["rule_id"], "rule_name": e["rule_name"], "analysis_id": e["analysis_id"], "key": e["key"],
            "count": e["count"], "severity": e["severity"], "first_at": e["first_at"], "last_at": e["last_at"],
            "open": e["open_count"] > 0 and e["rule_in_alert"], "new": (e["rule_id"], e["key"]) in new,
            # Un résumé dont l'analyse a vu une tentative de manipulation n'est pas montré à l'IA
            "summary": short_summary(a, 200) if a and not _manipulated(a) else "",
        })
    return {
        "window_hours": WINDOW_HOURS,
        "paused": monitoring.is_paused(db),
        "rules": {
            "watched": len(rules),
            "in_alert": [{"rule_id": r.id, "rule_name": r.name} for r in rules if (r.alert_status or "normal") != "normal"],
            "silent": [{"rule_id": r.id, "rule_name": r.name, "last_line_at": rule_stats.iso(r.last_line_received_at),
                        "limit_hours": r.inactivity_period_hours} for r in muted],
            "quietest": {"rule_id": quietest.id, "rule_name": quietest.name,
                         "last_line_at": rule_stats.iso(quietest.last_line_received_at),
                         "limit_hours": quietest.inactivity_period_hours} if quietest else None,
        },
        "activity": {
            "events": len(events),
            "occurrences": sum(e["count"] for e in events),
            "new": sum(1 for e in events if (e["rule_id"], e["key"]) in new),
            "open": sum(1 for e in events if e["open_count"] > 0 and e["rule_in_alert"]),
            "top": top,
        },
    }


def fingerprint(f: dict) -> str:
    """Ce qui justifie de réécrire le paragraphe : alertes ouvertes, sources muettes, événements nouveaux."""
    parts = [
        sorted(r["rule_id"] for r in f["rules"]["in_alert"]),
        sorted(r["rule_id"] for r in f["rules"]["silent"]),
        sorted((t["rule_id"], t["key"] or "", t["open"]) for t in f["activity"]["top"] if t["open"] or t["new"]),
        f["activity"]["new"], f["paused"],
    ]
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()[:16]


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.rstrip("Z")) if value else None
    except ValueError:
        return None


def stored(cfg: Optional[GlobalConfig], f: dict) -> dict:
    """Paragraphe enregistré, avec son état : à jour, en cours de réécriture, ou absent."""
    enabled = bool(cfg is None or cfg.situation_ai is not False)
    text = (cfg.situation_text if cfg else None) or ""
    state = "off" if not enabled else ("none" if not text else ("ok" if cfg.situation_key == fingerprint(f) else "stale"))
    return {"enabled": enabled, "state": state, "text": text if enabled else "",
            "at": rule_stats.iso(cfg.situation_at) if cfg and cfg.situation_at else None,
            "lang": cfg.situation_lang if cfg else None}


def payload(db) -> dict:
    """Ce que le dashboard affiche : les faits, et le paragraphe de l'IA avec son état."""
    f = facts(db)
    return {"facts": f, "ai": stored(db.query(GlobalConfig).first(), f)}


def _hm(value: Optional[str]) -> str:
    dt = _parse_dt(value)
    return f"{dt:%Y-%m-%d %H:%M}" if dt else "—"


def _prompt(f: dict, lang: str, now: datetime) -> str:
    """Les faits en texte, pour l'IA. Les heures sont en UTC, comme dans les analyses."""
    fr = lang == "fr"
    out = [f"{'Maintenant' if fr else 'Now'} : {now:%Y-%m-%d %H:%M} UTC"]
    r = f["rules"]
    out.append(f"{'Règles surveillées' if fr else 'Rules watched'} : {r['watched']}"
               + (f" ({'surveillance en pause' if fr else 'monitoring paused'})" if f["paused"] else ""))
    if r["in_alert"]:
        out.append(f"{'En alerte' if fr else 'In alert'} : " + ", ".join(x["rule_name"] for x in r["in_alert"]))
    for s in r["silent"]:
        out.append(f"{'Source muette' if fr else 'Silent source'} : {s['rule_name']}, "
                   f"{'dernière ligne' if fr else 'last line'} {_hm(s['last_line_at'])}, {'limite' if fr else 'limit'} {s['limit_hours']} h")
    q = r["quietest"]
    if q:
        out.append(f"{'Silence le plus long (sous sa limite)' if fr else 'Longest silence (under its limit)'} : {q['rule_name']}, "
                   f"{'dernière ligne' if fr else 'last line'} {_hm(q['last_line_at'])}")
    a = f["activity"]
    out.append(f"{'Dernières 24 h' if fr else 'Last 24 h'} : {a['events']} {'événements' if fr else 'events'}, "
               f"{a['occurrences']} {'occurrences' if fr else 'occurrences'}, {a['new']} {'nouveaux' if fr else 'new'}, "
               f"{a['open']} {'encore ouverts' if fr else 'still open'}")
    for t in a["top"]:
        state = ("ouvert" if t["open"] else "résolu") if fr else ("open" if t["open"] else "resolved")
        novelty = (" · nouveau" if fr else " · new") if t["new"] else ""
        out.append(f"[#{t['analysis_id']}] {t['rule_name']} · {t['severity']} · ×{t['count']} · {_hm(t['first_at'])} → {_hm(t['last_at'])} · "
                   f"{state}{novelty} · « {t['summary']} »")
    return "\n".join(out)


def clean(text: str, allowed_ids: set) -> str:
    """Texte affichable : une seule ligne de paragraphe, longueur bornée, citations limitées aux analyses fournies."""
    text = " ".join((text or "").split())
    text = _CITE.sub(lambda m: m.group(0) if int(m.group(1)) in allowed_ids else "", text)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT].rsplit(" ", 1)[0] + "…"


async def write(orchestrator, force: bool = False) -> Optional[str]:
    """Réécrit le paragraphe si la situation a changé. Ne passe jamais devant une alerte : si l'IA est occupée,
    on réessaiera au prochain tour."""
    db = SessionLocal()
    try:
        cfg = db.query(GlobalConfig).first()
        if not cfg or cfg.situation_ai is False or monitoring.is_paused(db):
            return None
        config = config_to_dict(cfg)
        if not config.get("ollama_url"):
            return None
        now = datetime.utcnow()
        f = facts(db, now)
        key = fingerprint(f)
        lang = config.get("site_lang") or "fr"
        same = cfg.situation_key == key and cfg.situation_lang == lang
        fresh = cfg.situation_at and now - cfg.situation_at < REFRESH_AFTER
        if not force and same and (fresh or not f["activity"]["events"]):
            return None
        if not force and cfg.situation_at and now - cfg.situation_at < MIN_GAP:
            return None
        if not f["rules"]["watched"]:
            return None
        semaphore = orchestrator._ollama_semaphore
        if semaphore.locked() or semaphore._waiters:
            return None
        messages = [{"role": "system", "content": SITUATION_SYSTEM.get(lang, SITUATION_SYSTEM["fr"])},
                    {"role": "user", "content": _prompt(f, lang, now)}]
        async with semaphore:
            res = await asyncio.wait_for(orchestrator.ollama.chat(
                messages, url=config["ollama_url"], model=config["ollama_model"], think=config["ollama_think"],
                options={"temperature": config["ollama_temp"], "num_ctx": config["ollama_ctx"]}, format=SITUATION_SCHEMA),
                timeout=180)
        if res.get("error"):
            logger.warning("Situation", f"L'IA n'a pas répondu : {res['error']}")
            return None
        try:
            text = json.loads(res.get("content") or "{}").get("text", "")
        except ValueError:
            text = res.get("content") or ""
        text = clean(text, {t["analysis_id"] for t in f["activity"]["top"]})
        if not text:
            return None
        cfg = db.query(GlobalConfig).first()
        cfg.situation_text, cfg.situation_at, cfg.situation_key, cfg.situation_lang = text, now, key, lang
        db.commit()
        logger.info("Situation", "Point de situation réécrit")
        return text
    except asyncio.TimeoutError:
        logger.warning("Situation", "L'IA n'a pas répondu à temps")
        return None
    finally:
        db.close()


async def run_forever(orchestrator):
    await asyncio.sleep(90)  # laisser démarrer les sources et la reprise des analyses
    while True:
        try:
            await write(orchestrator)
        except Exception as e:
            logger.error("Situation", f"Point de situation : {e}")
        await asyncio.sleep(TICK_SECONDS)
