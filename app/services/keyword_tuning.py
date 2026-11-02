"""Ajustement automatique des mots-clés et exclusions d'une règle (KW-2).

Au fil de l'eau, l'orchestrateur transmet chaque ligne reçue :
- statistiques par jour : lignes reçues, correspondances de chaque mot-clé et de chaque exclusion ;
- angles morts : lignes d'erreur qu'aucun mot-clé n'attrape, regroupées par modèle (valeurs variables masquées).

Périodiquement, l'IA locale arbitre (sortie structurée) :
- les angles morts récurrents → ajouter un mot-clé, ou ignorer ;
- les mots-clés bruyants (surtout des alertes « info », ou signalées « pas pertinente ») → garder,
  ajouter une exclusion, ou retirer le mot-clé.

Chaque décision passe des garde-fous vérifiés sur les données (le mot-clé figure réellement dans les lignes,
une exclusion ne masque aucune alerte importante, la règle garde au moins un mot-clé…). Les décisions sûres
sont appliquées et journalisées (KeywordChange, annulables) ; les autres sont proposées à l'utilisateur.
Un changement refusé ou annulé n'est jamais reproposé.
"""
import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from app import logger
from app.database import SessionLocal
from app.models import Analysis, GlobalConfig, KeywordChange, KeywordTemplate, Rule
from app.services.alert_analysis import messages_to_text
from app.services.app_config import config_to_dict, llm_options
from app.services.prompts import KEYWORD_ARBITER_SCHEMA, KEYWORD_ARBITER_SYSTEM, keyword_label
from app.utils import keyword_match
from app.utils.log_utils import clean_log_line, line_signature

FLUSH_INTERVAL_S = 600
REVIEW_INTERVAL_S = 3600
STATS_DAYS = 30
BLIND_MIN_COUNT = 3            # occurrences d'un angle mort avant arbitrage
BLIND_PER_REVIEW = 8
BLIND_REREVIEW_FACTOR = 10     # un modèle ignoré est réexaminé s'il devient 10 × plus fréquent
TEMPLATES_PER_RULE = 300       # modèles gardés en mémoire entre deux écritures
NOISE_MIN_ALERTS = 10
NOISE_INFO_SHARE = 0.9
NOISE_REVIEW_DAYS = 7          # un mot-clé conservé n'est réexaminé qu'après ce délai (sauf nouveau signalement)
NOISE_PER_REVIEW = 4
AUTO_CONFIDENCE = 70
MAX_KEYWORD_PER_DAY = 200      # au-delà, un nouveau mot-clé risque de noyer la règle : proposé, pas appliqué
VALUE_MIN_LEN, VALUE_MAX_LEN = 3, 80
CHANGES_RETENTION_DAYS = 180
TEMPLATES_RETENTION_DAYS = 30

MODES = ("auto", "suggest", "off")

# Mots trop génériques pour constituer seuls un mot-clé ou une exclusion utile
GENERIC_VALUES = {
    "error", "errors", "err", "fail", "failed", "failure", "warning", "warn", "info", "debug", "notice",
    "critical", "fatal", "exception", "timeout", "denied", "invalid", "unable", "erreur", "échec",
}

# Niveau explicite : syslog <PRI>, champ JSON level, ou jeton de niveau dans la ligne
_SYSLOG_PRI = re.compile(r"^\s*<(\d{1,3})>")
_JSON_LEVEL = re.compile(r'"(?:level|severity|lvl|level_name)"\s*:\s*"?([A-Za-z]+|\d{1,3})"?', re.IGNORECASE)
_LEVEL_TOKEN = re.compile(r"(?:^|[\s\[(<|\"'=:])(error|err|fatal|crit|critical|emerg|emergency|severe|panic)(?=$|[\s\])>|\"':,])",
                          re.IGNORECASE)
_ERROR_LEVEL_NAMES = {"error", "err", "fatal", "critical", "crit", "emerg", "emergency", "alert", "severe", "panic"}
# Mots d'échec (sans « warning » : trop fréquent pour signaler à lui seul un angle mort)
_FAILURE_WORDS = re.compile(
    r"\b(fail(ed|ure|s|ing)?|errors?|denied|refused|time[ds]?[ -]?out|unable|cannot|can't|could not|exception|fatal|"
    r"critical|unreachable|unauthori[sz]ed|forbidden|panic|crash(ed)?|abort(ed)?|killed|segfault|oom|"
    r"out of memory|unhealthy|degraded|corrupt(ed)?|échec|erreur|refusé|impossible)\b", re.IGNORECASE)


def error_level(line: str) -> bool:
    """La ligne porte un niveau d'erreur explicite (syslog ≤ 3, JSON level, jeton ERROR/FATAL…)."""
    m = _SYSLOG_PRI.match(line)
    if m and int(m.group(1)) % 8 <= 3:
        return True
    m = _JSON_LEVEL.search(line)
    if m:
        v = m.group(1).lower()
        if v.isdigit():
            n = int(v)
            # Nextcloud 3-4, pino/bunyan 50-60, Monolog 400-600
            return n in (3, 4) or 50 <= n <= 60 or 400 <= n <= 600
        return v in _ERROR_LEVEL_NAMES
    return bool(_LEVEL_TOKEN.search(line))


def error_like(line: str) -> bool:
    """Ligne d'erreur probable : niveau explicite, ou mot d'échec."""
    return error_level(line) or bool(_FAILURE_WORDS.search(line))


def template_source(line: str) -> str:
    """Texte significatif d'une ligne : le message d'une ligne JSON (Nextcloud…), sinon la ligne nettoyée."""
    text = clean_log_line(line).strip()
    start = text.find("{")
    if 0 <= start < 40:
        try:
            obj = json.loads(text[start:])
            if isinstance(obj, dict):
                msg = obj.get("message") or obj.get("msg")
                if msg:
                    app = obj.get("app") or obj.get("logger") or ""
                    return f"{app}: {msg}" if app else str(msg)
        except (ValueError, TypeError):
            pass
    return text


_NUMBER = re.compile(r"(?<![A-Za-z0-9<])(\d+)(?![A-Za-z0-9>])")


def _mask_number(m: re.Match) -> str:
    n = m.group(1)
    # Les codes à 3 chiffres façon HTTP (100-599) restent : « 502 » et « 200 » ne doivent pas se confondre
    return n if len(n) == 3 and 100 <= int(n) <= 599 else "<N>"


def signature(line: str) -> str:
    """Modèle d'une ligne pour regrouper les occurrences d'un même événement : en plus de line_signature,
    les petits nombres (tentative 3, id=12…) sont masqués."""
    return _NUMBER.sub(_mask_number, line_signature(template_source(line)))


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _has_variable_part(value: str) -> bool:
    """Contient une valeur variable (horodatage, adresse, identifiant, grand nombre) : ne se généraliserait pas."""
    return "<" in line_signature(value) and "<" not in value


def load_stats(rule: Rule) -> dict:
    try:
        data = json.loads(rule.keyword_stats_json or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def pattern_daily_hits(stats: dict, prefix: str, pattern: str, days: int = 7) -> Optional[float]:
    """Correspondances par jour d'un motif ('k' mot-clé, 'x' exclusion) sur les derniers jours observés
    (None tant qu'aucune ligne n'a été observée)."""
    recent = sorted(stats.get("days", {}).items())[-days:]
    if not recent:
        return None
    return sum(d.get("hits", {}).get(f"{prefix}:{pattern}", 0) for _, d in recent) / len(recent)


class KeywordTuningService:
    def __init__(self, orchestrator=None):
        self.orchestrator = orchestrator
        self._stats: Dict[int, dict] = {}       # rule_id -> {date: {"lines": n, "hits": Counter}}
        self._templates: Dict[int, dict] = {}   # rule_id -> {signature: {count, first, last, examples}}
        self._reviewing = set()

    # ── Observation (appelée pour chaque ligne reçue, doit rester légère) ──────────
    def observe(self, rule_id: int, line: str, status: Optional[str], hits: List[str]):
        day = self._stats.setdefault(rule_id, {}).setdefault(_today(), {"lines": 0, "hits": Counter()})
        day["lines"] += 1
        prefix = "x" if status == "excluded" else "k"
        for h in hits:
            day["hits"][f"{prefix}:{h}"] += 1
        if status is None and error_like(line):
            self._remember_template(rule_id, line)

    def _remember_template(self, rule_id: int, line: str):
        sig = signature(line)
        if not sig:
            return
        now = datetime.utcnow()
        tpls = self._templates.setdefault(rule_id, {})
        t = tpls.get(sig)
        if t is None:
            if len(tpls) >= TEMPLATES_PER_RULE:
                return
            t = tpls[sig] = {"count": 0, "first": now, "last": now, "examples": []}
        t["count"] += 1
        t["last"] = now
        if len(t["examples"]) < 3 and line[:500] not in t["examples"]:
            t["examples"].append(line[:500])

    # ── Écriture périodique en base ────────────────────────────────────────────────
    async def flush(self):
        stats, self._stats = self._stats, {}
        templates, self._templates = self._templates, {}
        if stats or templates:
            await asyncio.to_thread(self._flush_sync, stats, templates)

    def _flush_sync(self, stats: dict, templates: dict):
        db = SessionLocal()
        try:
            for rule_id in set(stats) | set(templates):
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    continue
                if rule_id in stats:
                    data = load_stats(rule)
                    days = data.setdefault("days", {})
                    for date, d in stats[rule_id].items():
                        cur = days.setdefault(date, {"lines": 0, "hits": {}})
                        cur["lines"] = cur.get("lines", 0) + d["lines"]
                        for k, n in d["hits"].items():
                            cur["hits"][k] = cur["hits"].get(k, 0) + n
                    for date in sorted(days)[:-STATS_DAYS]:
                        del days[date]
                    rule.keyword_stats_json = json.dumps(data)
                for sig, t in templates.get(rule_id, {}).items():
                    row = db.query(KeywordTemplate).filter(KeywordTemplate.rule_id == rule_id,
                                                           KeywordTemplate.signature == sig).first()
                    if row is None:
                        row = KeywordTemplate(rule_id=rule_id, signature=sig, count=0, first_seen=t["first"],
                                              examples_json="[]", status="new", reviewed_count=0)
                        db.add(row)
                    row.count = (row.count or 0) + t["count"]
                    row.last_seen = t["last"]
                    examples = json.loads(row.examples_json or "[]")
                    for ex in t["examples"]:
                        if len(examples) < 3 and ex not in examples:
                            examples.append(ex)
                    row.examples_json = json.dumps(examples)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("KeywordTuning", f"Écriture des statistiques impossible : {e}")
        finally:
            db.close()

    # ── Boucle de fond ─────────────────────────────────────────────────────────────
    async def run_forever(self):
        await asyncio.sleep(120)
        last_review = datetime.utcnow()  # première revue une heure après le démarrage
        while True:
            try:
                await self.flush()
                if (datetime.utcnow() - last_review).total_seconds() >= REVIEW_INTERVAL_S:
                    last_review = datetime.utcnow()
                    await self.review_all()
                    await asyncio.to_thread(purge_old)
            except Exception as e:
                logger.error("KeywordTuning", f"Boucle d'ajustement : {e}")
            await asyncio.sleep(FLUSH_INTERVAL_S)

    async def review_all(self):
        db = SessionLocal()
        try:
            rules = db.query(Rule).filter(Rule.enabled == True).all()
            ids = [r.id for r in rules if (r.keyword_tuning_mode or "auto") != "off"]
            autopilot_due = [(r.id, refresh) for r in rules for refresh in [autopilot_due_refresh(r)] if refresh is not None]
        finally:
            db.close()
        # Pilote automatique : relecture hebdomadaire, ou nouvel essai après un échec
        from app.services import keyword_learning_service as kls
        for rule_id, refresh in autopilot_due:
            try:
                kls.start_session(rule_id, "history", 168, refresh=refresh)
            except Exception as e:
                logger.error("Autopilot", f"Relance du pilote pour la règle {rule_id} : {e}")
        for rule_id in ids:
            try:
                await self.review_rule(rule_id)
            except Exception as e:
                logger.error("KeywordTuning", f"Revue de la règle {rule_id} : {e}")

    # ── Revue d'une règle ──────────────────────────────────────────────────────────
    async def review_rule(self, rule_id: int, focus_analysis_id: Optional[int] = None) -> List[KeywordChange]:
        """Arbitre les angles morts et le bruit d'une règle. Retourne les changements créés."""
        if rule_id in self._reviewing:
            return []
        self._reviewing.add(rule_id)
        try:
            await self.flush()
            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule or (rule.keyword_tuning_mode or "auto") == "off":
                    return []
                config = config_to_dict(db.query(GlobalConfig).first())
                blind = blind_spot_candidates(db, rule)
                noise = noise_candidates(db, rule, focus_analysis_id)
                if not blind and not noise:
                    rule.keyword_tuned_at = datetime.utcnow()
                db.commit()  # statut « couvert » des modèles désormais attrapés
                if not blind and not noise:
                    return []
                messages = build_arbiter_messages(config.get("ollama_prompt_lang", "fr"), rule, blind, noise)
            finally:
                db.close()

            decision = await self.ask_arbiter(messages, config, label=f"rule {rule_id}")
            if decision is None:
                return []

            db = SessionLocal()
            try:
                rule = db.query(Rule).filter(Rule.id == rule_id).first()
                if not rule:
                    return []
                origin_noise = "feedback" if focus_analysis_id else "noise"
                changes = apply_decisions(db, rule, decision, blind, noise, origin_blind="blind_spot",
                                          origin_noise=origin_noise)
                rule.keyword_tuned_at = datetime.utcnow()
                db.commit()
                for c in changes:
                    db.refresh(c)
                result = [_detached(c) for c in changes]
                name, notify = rule.name, rule.notify_on_match
            finally:
                db.close()
            if result:
                logger.info("KeywordTuning", f"Règle {rule_id} : {len(result)} ajustement(s) — "
                            + ", ".join(f"{c['status']} {c['action']} {c['kind']} « {c['value']} »" for c in result))
                await notify_changes(name, notify, result, config)
            return result
        finally:
            self._reviewing.discard(rule_id)

    async def ask_arbiter(self, messages: list, config: dict, label: str = "", schema: dict = KEYWORD_ARBITER_SCHEMA,
                          kind: str = "KEYWORDS") -> Optional[dict]:
        if not self.orchestrator:
            return None
        from app.services.resolution_service import clean_ollama_json
        async with self.orchestrator._ollama_semaphore:
            try:
                res = await asyncio.wait_for(self.orchestrator.ollama.chat(
                    messages, url=config.get("ollama_url"), model=config.get("ollama_model"),
                    think=config.get("ollama_think", False), options=llm_options(config, temperature=0.1),
                    format=schema), timeout=300.0)
            except asyncio.TimeoutError:
                res = {"content": "", "error": "Timeout Ollama (300 s)"}
        content = res.get("content") or ""
        logger.add_ollama_log(messages_to_text(messages), content or res.get("error") or "", kind)
        if res.get("error") or not content:
            logger.warning("KeywordTuning", f"Arbitre indisponible ({label}) : {res.get('error') or 'réponse vide'}")
            return None
        try:
            data = json.loads(clean_ollama_json(content))
            return data if isinstance(data, dict) else None
        except ValueError:
            logger.warning("KeywordTuning", f"Réponse de l'arbitre illisible ({label}) : {content[:200]}")
            return None


def _detached(c: KeywordChange) -> dict:
    return change_dict(c)


AUTOPILOT_RETRY_H = 6


def autopilot_due_refresh(rule: Rule) -> Optional[bool]:
    """Session du pilote à lancer : True (relecture hebdomadaire), False (nouvel essai après un échec), None (rien)."""
    from app.services import autopilot as ap
    data = ap.load(rule)
    if not data.get("enabled"):
        return None
    now = datetime.utcnow()
    since = lambda key: (now - datetime.fromisoformat(data[key])).total_seconds() / 3600 if data.get(key) else 1e9
    if data.get("status") == "active" and since("refreshed_at") >= ap.REFRESH_DAYS * 24:
        return True
    if data.get("status") == "error" and data.get("error") != "cancelled" and since("attempt_at") >= AUTOPILOT_RETRY_H:
        return False
    return None


def change_dict(c: KeywordChange) -> dict:
    return {
        "id": c.id, "rule_id": c.rule_id, "kind": c.kind, "action": c.action, "value": c.value,
        "origin": c.origin, "status": c.status, "reason": c.reason or "", "confidence": c.confidence,
        "hold_reason": c.hold_reason, "evidence": json.loads(c.evidence_json or "{}"), "session_id": c.session_id,
        "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        "decided_at": c.decided_at.isoformat() + "Z" if c.decided_at else None,
    }


# ── Candidats ──────────────────────────────────────────────────────────────────────

def _per_day(count: int, first: datetime, last: datetime) -> float:
    days = max(1.0, (last - first).total_seconds() / 86400) if first and last else 1.0
    return round(count / days, 1)


def blind_spot_candidates(db, rule: Rule) -> List[dict]:
    rows = db.query(KeywordTemplate).filter(KeywordTemplate.rule_id == rule.id,
                                            KeywordTemplate.count >= BLIND_MIN_COUNT).all()
    keywords, excluded = rule.get_keywords(), rule.get_excluded_patterns()
    out = []
    for r in rows:
        if r.status == "covered":
            continue
        if r.status == "ignored" and r.count < max(BLIND_MIN_COUNT * BLIND_REREVIEW_FACTOR,
                                                   (r.reviewed_count or 0) * BLIND_REREVIEW_FACTOR):
            continue
        examples = json.loads(r.examples_json or "[]")
        # Couvert depuis (mot-clé ajouté à la main) : plus un angle mort
        if examples and all(keyword_match.classify(ex, keywords, excluded)[0] for ex in examples):
            r.status = "covered"
            continue
        out.append({"template_id": r.id, "signature": r.signature, "count": r.count, "examples": examples,
                    "first": r.first_seen, "last": r.last_seen, "per_day": _per_day(r.count, r.first_seen, r.last_seen)})
    out.sort(key=lambda c: c["count"], reverse=True)
    return out[:BLIND_PER_REVIEW]


def _analysis_lines(a: Analysis) -> List[str]:
    try:
        lines = json.loads(a.triggered_lines_json or "{}").get("lines") or []
    except ValueError:
        lines = []
    return [str(l) for l in lines] or ([a.triggered_line] if a.triggered_line else [])


def alert_stats(db, rule: Rule, days: int = 30) -> dict:
    """Par mot-clé : alertes, sévérités, signalements, lignes déclenchées regroupées par modèle."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(Analysis).filter(Analysis.rule_id == rule.id, Analysis.analyzed_at >= since).all()
    stats = defaultdict(lambda: {"alerts": 0, "severities": Counter(), "irrelevant": [], "lines": {},
                                 "important_lines": [], "last_at": None})
    for a in rows:
        try:
            kws = json.loads(a.matched_keywords_json or "[]")
        except ValueError:
            kws = []
        sev = (a.severity or "info").lower()
        lines = _analysis_lines(a)
        for kw in kws:
            s = stats[kw]
            s["alerts"] += 1
            s["severities"][sev] += 1
            if a.analyzed_at and (s["last_at"] is None or a.analyzed_at > s["last_at"]):
                s["last_at"] = a.analyzed_at
            if a.feedback == "irrelevant":
                s["irrelevant"].append(a.id)
            low_kw = [kw]
            for line in lines:
                if not keyword_match.matching(low_kw, line):
                    continue
                if sev in ("warning", "critical") and a.feedback != "irrelevant":
                    if len(s["important_lines"]) < 200:
                        s["important_lines"].append(line)
                sig = signature(line)
                g = s["lines"].setdefault(sig, {"count": 0, "severities": Counter(), "example": line[:400]})
                g["count"] += 1
                g["severities"]["irrelevant" if a.feedback == "irrelevant" else sev] += 1
    return stats


def noise_candidates(db, rule: Rule, focus_analysis_id: Optional[int] = None) -> List[dict]:
    keywords = rule.get_keywords()
    if not keywords:
        return []
    stats = alert_stats(db, rule)
    data = load_stats(rule)
    reviews = data.get("reviews", {})
    focus_kws = set()
    if focus_analysis_id:
        a = db.query(Analysis).filter(Analysis.id == focus_analysis_id).first()
        if a:
            focus_kws = set(json.loads(a.matched_keywords_json or "[]"))
    now = datetime.utcnow()
    out = []
    for kw in keywords:
        s = stats.get(kw)
        if not s or not s["alerts"]:
            continue
        reviewed = reviews.get(kw)
        reviewed_at = datetime.fromisoformat(reviewed["at"]) if reviewed else None
        new_flags = [i for i in s["irrelevant"] if not reviewed or i not in reviewed.get("irrelevant", [])]
        info_share = s["severities"].get("info", 0) / s["alerts"]
        noisy = (s["alerts"] >= NOISE_MIN_ALERTS and info_share >= NOISE_INFO_SHARE
                 and not s["severities"].get("critical"))
        recent = reviewed_at and (now - reviewed_at).days < NOISE_REVIEW_DAYS
        if kw in focus_kws or new_flags or (noisy and not recent):
            out.append({"keyword": kw, "alerts": s["alerts"], "severities": dict(s["severities"]),
                        "irrelevant": len(s["irrelevant"]), "irrelevant_ids": s["irrelevant"],
                        "per_day": round(pattern_daily_hits(data, "k", kw) or 0, 1),
                        "lines": sorted(s["lines"].values(), key=lambda g: g["count"], reverse=True)[:8],
                        "important_lines": s["important_lines"], "focus": kw in focus_kws})
    out.sort(key=lambda c: (not c["focus"], -c["irrelevant"], -c["alerts"]))
    return out[:NOISE_PER_REVIEW]


# ── Prompt ─────────────────────────────────────────────────────────────────────────

def _fmt_sev(sev: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(sev.items(), key=lambda kv: -kv[1])) or "—"


def build_arbiter_messages(lang: str, rule: Rule, blind: List[dict], noise: List[dict], period: str = "") -> list:
    L = lambda key, **kw: keyword_label(lang, key, **kw)
    none = L("none")
    parts = [L("rule", name=rule.name)]
    if rule.application_context:
        parts.append(L("app_context", context=rule.application_context[:800]))
    parts.append(L("keywords", keywords=", ".join(f"« {k} »" for k in rule.get_keywords()) or none))
    parts.append(L("exclusions", exclusions=", ".join(f"« {e} »" for e in rule.get_excluded_patterns()) or none))
    if period:
        parts.append(L("period", period=period))

    parts.append("\n" + L("blind_title"))
    if blind:
        for i, c in enumerate(blind, 1):
            parts.append(L("blind_item", n=i, count=c["count"], per_day=c["per_day"],
                           first=f"{c['first']:%Y-%m-%d}" if c.get("first") else "?",
                           last=f"{c['last']:%Y-%m-%d}" if c.get("last") else "?"))
            for ex in c["examples"][:2]:
                parts.append(L("example", example=clean_log_line(ex)[:400]))
    else:
        parts.append(L("nothing"))

    parts.append("\n" + L("noise_title"))
    if noise:
        for c in noise:
            parts.append(L("noise_item", keyword=c["keyword"], alerts=c["alerts"], severities=_fmt_sev(c["severities"]),
                           irrelevant=c["irrelevant"], per_day=c["per_day"]))
            if c["lines"]:
                parts.append(L("noise_lines"))
                for g in c["lines"]:
                    parts.append(L("noise_line", count=g["count"], severity=_fmt_sev(dict(g["severities"])),
                                   example=clean_log_line(g["example"])[:300]))
    else:
        parts.append(L("nothing"))
    return [{"role": "system", "content": KEYWORD_ARBITER_SYSTEM.get(lang, KEYWORD_ARBITER_SYSTEM["fr"])},
            {"role": "user", "content": "\n".join(parts)}]


# ── Garde-fous et application ──────────────────────────────────────────────────────

def clean_value(value) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'`«» ").strip()
    return v


def _value_problem(v: str) -> Optional[str]:
    """Raison de rejet d'une valeur proposée par l'arbitre (None si acceptable)."""
    if not (VALUE_MIN_LEN <= len(v) <= VALUE_MAX_LEN):
        return "length"
    if v.lower() in GENERIC_VALUES:
        return "generic"
    if _has_variable_part(v):
        return "variable"
    return None


def _already_decided(db, rule_id: int, kind: str, action: str, value: str) -> bool:
    """Un changement identique a été refusé, annulé, ou attend déjà une décision."""
    rows = db.query(KeywordChange).filter(KeywordChange.rule_id == rule_id, KeywordChange.kind == kind,
                                          KeywordChange.action == action,
                                          KeywordChange.status.in_(("rejected", "reverted", "pending"))).all()
    return any(r.value.lower() == value.lower() for r in rows)


def apply_change_to_rule(rule: Rule, kind: str, action: str, value: str) -> bool:
    """Modifie la règle ; False si rien à faire (déjà dans l'état voulu)."""
    current = rule.get_keywords() if kind == "keyword" else rule.get_excluded_patterns()
    present = any(x.lower() == value.lower() for x in current)
    if action == "add":
        if present:
            return False
        new = current + [value]
    else:
        if not present:
            return False
        new = [x for x in current if x.lower() != value.lower()]
    if kind == "keyword":
        rule.set_keywords(new)
    else:
        rule.set_excluded_patterns(new)
    return True


def record_change(db, rule: Rule, kind: str, action: str, value: str, origin: str, reason: str = "",
                  confidence: Optional[int] = None, evidence: Optional[dict] = None, hold: Optional[str] = None,
                  session_id: Optional[int] = None) -> Optional[KeywordChange]:
    """Applique (mode auto, aucun garde-fou déclenché) ou propose un changement, et le journalise."""
    if _already_decided(db, rule.id, kind, action, value):
        return None
    mode = rule.keyword_tuning_mode or "auto"
    if not hold and mode != "auto":
        hold = "suggest_mode"
    status = "pending"
    if not hold:
        if not apply_change_to_rule(rule, kind, action, value):
            return None
        status = "applied"
    change = KeywordChange(rule_id=rule.id, kind=kind, action=action, value=value, origin=origin, status=status,
                           reason=(reason or "")[:1000], confidence=confidence, hold_reason=hold,
                           evidence_json=json.dumps(evidence or {}, default=str)[:20000], session_id=session_id,
                           decided_at=datetime.utcnow() if status == "applied" else None)
    db.add(change)
    return change


def _confidence(item: dict) -> int:
    try:
        return max(0, min(100, int(item.get("confidence", 0))))
    except (TypeError, ValueError):
        return 0


def apply_decisions(db, rule: Rule, decision: dict, blind: List[dict], noise: List[dict],
                    origin_blind: str, origin_noise: str, session_id: Optional[int] = None) -> List[KeywordChange]:
    """Vérifie chaque décision de l'arbitre sur les données, puis l'applique ou la propose."""
    changes = []
    keywords = rule.get_keywords()
    excluded = rule.get_excluded_patterns()
    seen = set()  # même changement proposé deux fois dans une réponse (ex. une exclusion pour deux mots-clés)

    def once(kind, action, value):
        key = (kind, action, value.lower())
        if key in seen:
            return False
        seen.add(key)
        return True

    for item in decision.get("blind_spots") or []:
        try:
            cand = blind[int(item.get("template", 0)) - 1]
        except (ValueError, TypeError, IndexError):
            continue
        tpl = db.query(KeywordTemplate).filter(KeywordTemplate.id == cand["template_id"]).first() \
            if cand.get("template_id") else None
        reason, conf = str(item.get("reason", "")), _confidence(item)
        if item.get("decision") != "add_keyword":
            if tpl is not None:
                tpl.status, tpl.reviewed_count = "ignored", tpl.count
            continue
        value = clean_value(item.get("keyword"))
        examples = cand["examples"]
        if _value_problem(value) or not examples or not all(keyword_match.contains(value, ex.lower()) for ex in examples):
            # Non fondé sur les lignes (inventé, variable ou générique) : écarté
            if tpl is not None:
                tpl.status, tpl.reviewed_count = "ignored", tpl.count
            logger.info("KeywordTuning", f"Mot-clé proposé écarté pour '{rule.name}' : « {value} »")
            continue
        if keyword_match.neutralized([value], excluded) or any(k.lower() == value.lower() for k in keywords) \
                or not once("keyword", "add", value):
            continue
        # Déjà couvert par un mot-clé ajouté juste avant (deux lignes du même événement)
        if all(keyword_match.classify(ex, keywords, excluded)[0] for ex in examples):
            if tpl is not None:
                tpl.status = "covered"
            continue
        hold = None
        if conf < AUTO_CONFIDENCE:
            hold = "low_confidence"
        elif cand["per_day"] > MAX_KEYWORD_PER_DAY:
            hold = "too_frequent"
        evidence = {"examples": examples[:3], "count": cand["count"], "per_day": cand["per_day"],
                    "signature": cand["signature"]}
        c = record_change(db, rule, "keyword", "add", value, origin_blind, reason, conf, evidence, hold, session_id)
        if tpl is not None:
            tpl.status = "covered"
        if c:
            changes.append(c)
            keywords = rule.get_keywords()

    noise_by_kw = {c["keyword"].lower(): c for c in noise}
    data = load_stats(rule)
    reviews = data.setdefault("reviews", {})
    for item in decision.get("noise") or []:
        cand = noise_by_kw.get(clean_value(item.get("keyword")).lower())
        if not cand:
            continue
        kw = cand["keyword"]
        reviews[kw] = {"at": datetime.utcnow().isoformat(), "irrelevant": cand.get("irrelevant_ids", [])}
        reason, conf = str(item.get("reason", "")), _confidence(item)
        sev = cand["severities"]
        evidence = {"alerts": cand["alerts"], "severities": sev, "irrelevant": cand["irrelevant"],
                    "per_day": cand["per_day"], "examples": [g["example"] for g in cand["lines"][:3]]}
        decided = item.get("decision")
        if decided == "remove_keyword":
            hold = None
            if conf < AUTO_CONFIDENCE:
                hold = "low_confidence"
            elif len(keywords) <= 1:
                hold = "last_keyword"
            elif sev.get("critical") or (sev.get("warning", 0) > 0.2 * cand["alerts"] and not cand["irrelevant"]):
                hold = "important_alerts"
            if not once("keyword", "remove", kw):
                continue
            c = record_change(db, rule, "keyword", "remove", kw, origin_noise, reason, conf, evidence, hold, session_id)
            if c:
                changes.append(c)
                keywords = rule.get_keywords()
        elif decided == "add_exclusion":
            value = clean_value(item.get("exclusion"))
            noisy_lines = [g["example"] for g in cand["lines"]]
            if _value_problem(value) or not any(keyword_match.contains(value, l.lower()) for l in noisy_lines):
                logger.info("KeywordTuning", f"Exclusion proposée écartée pour '{rule.name}' : « {value} »")
                continue
            if any(e.lower() == value.lower() for e in excluded) or not once("exclusion", "add", value):
                continue
            hidden = [l for l in cand["important_lines"] if keyword_match.contains(value, l.lower())]
            hold = None
            if conf < AUTO_CONFIDENCE:
                hold = "low_confidence"
            elif hidden:
                hold = "hides_important"
                evidence["hidden_examples"] = hidden[:3]
            elif keyword_match.neutralized(keywords, [value]):
                hold = "neutralizes_keyword"
            c = record_change(db, rule, "exclusion", "add", value, origin_noise, reason, conf, evidence, hold, session_id)
            if c:
                changes.append(c)
                excluded = rule.get_excluded_patterns()
    rule.keyword_stats_json = json.dumps({**load_stats(rule), "reviews": reviews})
    return changes


# ── Décisions de l'utilisateur ─────────────────────────────────────────────────────

def decide(db, change: KeywordChange, action: str) -> Optional[str]:
    """accept (proposé → appliqué), reject (proposé → refusé), revert (appliqué → annulé).
    Retourne un message d'erreur, ou None."""
    rule = db.query(Rule).filter(Rule.id == change.rule_id).first()
    if not rule:
        return "rule_not_found"
    if action == "accept" and change.status == "pending":
        apply_change_to_rule(rule, change.kind, change.action, change.value)
        change.status = "applied"
    elif action == "reject" and change.status == "pending":
        change.status = "rejected"
    elif action == "revert" and change.status == "applied":
        inverse = "remove" if change.action == "add" else "add"
        apply_change_to_rule(rule, change.kind, inverse, change.value)
        change.status = "reverted"
    else:
        return "invalid_transition"
    change.decided_at = datetime.utcnow()
    return None


def sync_manual_edit(db, rule: Rule, old_keywords: List[str], old_excluded: List[str]):
    """Après une modification à la main : un ajout de l'arbitre retiré (ou un retrait remis) est marqué annulé,
    pour qu'il ne soit pas reproposé ; une proposition réalisée à la main est marquée appliquée."""
    now = datetime.utcnow()
    for kind, old, new in (("keyword", old_keywords, rule.get_keywords()),
                           ("exclusion", old_excluded, rule.get_excluded_patterns())):
        old_l, new_l = {x.lower() for x in old}, {x.lower() for x in new}
        if old_l == new_l:
            continue
        rows = db.query(KeywordChange).filter(KeywordChange.rule_id == rule.id, KeywordChange.kind == kind,
                                              KeywordChange.status.in_(("applied", "pending"))).all()
        for c in rows:
            v = c.value.lower()
            if c.status == "applied":
                undone = (c.action == "add" and v in old_l and v not in new_l) or \
                         (c.action == "remove" and v not in old_l and v in new_l)
                if undone:
                    c.status, c.decided_at, c.hold_reason = "reverted", now, "manual_edit"
            else:
                done = (c.action == "add" and v in new_l) or (c.action == "remove" and v not in new_l)
                if done:
                    c.status, c.decided_at = "applied", now


def record_manual_change(rule_id: int, kind: str, action: str, value: str, origin: str = "chat", reason: str = ""):
    """Journalise un changement fait ailleurs (chat avec confirmation) pour l'historique de la règle."""
    db = SessionLocal()
    try:
        db.add(KeywordChange(rule_id=rule_id, kind=kind, action=action, value=value, origin=origin,
                             status="applied", reason=reason[:1000], decided_at=datetime.utcnow()))
        db.commit()
    finally:
        db.close()


# ── Notification ───────────────────────────────────────────────────────────────────

async def notify_changes(rule_name: str, notify: bool, changes: List[dict], config: dict, title: str = None,
                         extra: Optional[List[str]] = None):
    """Une notification par revue. extra : lignes déjà formatées (réglages définis par le pilote automatique)."""
    applied = [c for c in changes if c["status"] == "applied"]
    pending = [c for c in changes if c["status"] == "pending"]
    if not notify or not (applied or pending or extra):
        return
    lang = config.get("ollama_prompt_lang", "fr")
    fr = lang == "fr"
    sign = {("keyword", "add"): "+ mot-clé" if fr else "+ keyword", ("keyword", "remove"): "− mot-clé" if fr else "− keyword",
            ("exclusion", "add"): "+ exclusion", ("exclusion", "remove"): "− exclusion"}
    lines = list(extra or [])
    lines += [f"- {sign[(c['kind'], c['action'])]} `{c['value']}` — {c['reason'][:200]}" for c in applied]
    if pending:
        lines.append(("\n**À valider dans l'onglet Mots-clés :**" if fr else "\n**To review in the Keywords tab:**"))
        lines += [f"- {sign[(c['kind'], c['action'])]} `{c['value']}`" for c in pending]
    subject = title or ((f"[Sentinel] 🔧 Mots-clés ajustés — {rule_name}") if fr else f"[Sentinel] 🔧 Keywords tuned — {rule_name}")
    instance = (config.get("instance_name") or "").strip()
    if instance:
        subject = f"[{instance}] {subject}"
    from app.services.notification_service import NotificationService
    try:
        await asyncio.to_thread(NotificationService().send, subject, "\n".join(lines), config)
    except Exception as e:
        logger.warning("KeywordTuning", f"Notification impossible : {e}")


# ── Purge ──────────────────────────────────────────────────────────────────────────

def purge_old():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        # Les refus et annulations sont gardés : ils empêchent de reproposer le même changement
        db.query(KeywordChange).filter(KeywordChange.status == "applied",
                                       KeywordChange.created_at < now - timedelta(days=CHANGES_RETENTION_DAYS)).delete()
        db.query(KeywordTemplate).filter(KeywordTemplate.last_seen < now - timedelta(days=TEMPLATES_RETENTION_DAYS)).delete()
        db.commit()
    finally:
        db.close()
    # Journaux texte des sessions d'apprentissage des versions précédentes (contenaient des lignes de log brutes)
    old_logs = Path(os.environ.get("SENTINEL_DATA_DIR", "/app/data")) / "learning_logs"
    if old_logs.is_dir():
        for f in old_logs.glob("session_*.txt"):
            try:
                if now.timestamp() - f.stat().st_mtime > TEMPLATES_RETENTION_DAYS * 86400:
                    f.unlink()
            except OSError:
                pass
