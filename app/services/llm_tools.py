"""
Outils mis à disposition du LLM dans le chat (lecture seule) et actions proposées (exécutées uniquement
après confirmation explicite de l'utilisateur).

Garde-fous : les outils n'acceptent que des identifiants (jamais de chemin libre), chaque sortie est bornée
en lignes et en caractères, et la recherche se fait par sous-chaîne (pas de regex fournie par le LLM).
"""
import asyncio
import copy
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import desc

from app import logger
from app.database import SessionLocal
from app.models import Analysis, ChatAction, ChatConversation, GlobalConfig, MetaAnalysisConfig, ResolutionVerdict, Rule
from app.services import attack_summary, cve_lookup, log_source, web_search
from app.services import diagnostics as diag
from app.services import event_notes
from app.services.alert_analysis import analysis_lines, render_notes, short_summary
from app.utils import keyword_match
from app.utils.log_utils import clean_log_line, line_signature

MAX_OUTPUT_CHARS = 4000
MAX_LINES = 100
SEARCH_MAX_BYTES = 20 * 1024 * 1024
CORRELATION_MAX_BYTES = 5 * 1024 * 1024
LINE_CHARS = 400


def _clip(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _line(l: str) -> str:
    l = clean_log_line(l.rstrip())
    return l if len(l) <= LINE_CHARS else l[:LINE_CHARS] + "…"


def truncate_output(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[… {len(text) - limit} caractères omis]"


def _rule(db, rule_id) -> Rule:
    rule = db.query(Rule).filter(Rule.id == _clip(rule_id, 0, 10**9, 0)).first()
    if not rule:
        raise ToolError(f"Règle #{rule_id} introuvable. Utilise list_rules pour connaître les identifiants.")
    return rule


class ToolError(Exception):
    pass


# gemma4:e4b écrit parfois un appel d'outil en texte, <tool_call name="get_x"/>, au lieu du format d'Ollama
# (constaté chez Alanbix, 2026-11-08). Sans traitement, la balise s'affiche et aucun outil ne tourne.
TOOL_TAG = "<tool_call"
_TOOL_TAG_RE = re.compile(r"<tool_call\b([^>]*?)/>|<tool_call\b([^>]*)>(.*?)</tool_call>", re.S)
_TAG_ATTR_RE = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _tag_call(m) -> Optional[dict]:
    attrs = {k: a if a else b for k, a, b in _TAG_ATTR_RE.findall(m.group(1) or m.group(2) or "")}
    body = (m.group(3) or "").strip()
    name, args = attrs.pop("name", ""), attrs
    if body:
        try:
            val = json.loads(body)
        except ValueError:
            val = None
        if isinstance(val, dict):   # forme longue : <tool_call>{"name": ..., "arguments": {...}}</tool_call>
            name = name or str(val.get("name") or "")
            if isinstance(val.get("arguments"), dict):
                args = {**args, **val["arguments"]}
    return {"function": {"name": name, "arguments": args}} if name else None


def text_tool_calls(text: str):
    """(texte sans les balises d'appel d'outil, appels trouvés dans ces balises)."""
    calls = [c for c in (_tag_call(m) for m in _TOOL_TAG_RE.finditer(text or "")) if c]
    return _TOOL_TAG_RE.sub("", text or ""), calls


class ToolTagFilter:
    """Pour un flux : retient le texte dès qu'une balise <tool_call commence, le rend s'il n'en est pas une."""
    MAX_TAG = 2000

    def __init__(self):
        self.buf, self.calls = "", []

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while self.buf:
            i = self.buf.find("<")
            if i == -1:
                out, self.buf = out + self.buf, ""
                break
            out, self.buf = out + self.buf[:i], self.buf[i:]
            if not TOOL_TAG.startswith(self.buf[:len(TOOL_TAG)]):
                out, self.buf = out + "<", self.buf[1:]
                continue
            if len(self.buf) < len(TOOL_TAG):
                break   # début de balise possible : attendre la suite
            m = _TOOL_TAG_RE.match(self.buf)
            if not m:
                if len(self.buf) > self.MAX_TAG:   # jamais refermée : c'était du texte
                    out, self.buf = out + self.buf, ""
                break
            call = _tag_call(m)
            if call:
                self.calls.append(call)
            self.buf = self.buf[m.end():]
        return out

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return out


_TS_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?(Z|[+-]\d{2}:?\d{2})?")


def parse_line_ts(line: str) -> Optional[datetime]:
    """Premier horodatage ISO de la ligne, converti en UTC naïf (sans fuseau : considéré UTC)."""
    m = _TS_ISO.search(line[:120])
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    except ValueError:
        return None
    tz = m.group(3)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        dt -= sign * timedelta(hours=hh, minutes=mm)
    return dt


# ── Outils en lecture seule ────────────────────────────────────────────────────

def list_rules(db, **_):
    since = datetime.utcnow() - timedelta(hours=24)
    counts = Counter(r for (r,) in db.query(Analysis.rule_id).filter(Analysis.analyzed_at >= since).all())
    out = []
    for r in db.query(Rule).order_by(Rule.id).all():
        status = r.alert_status or "normal"
        if r.alert_started_at and status != "normal":
            status += f" depuis {r.alert_started_at.strftime('%Y-%m-%d %H:%M')} UTC"
        last = r.last_line_received_at.strftime('%Y-%m-%d %H:%M') + " UTC" if r.last_line_received_at else "jamais"
        out.append(f"#{r.id} {r.name} — source {log_source.source_type(r.log_file_path)}"
                   f"{'' if r.enabled else ' (désactivée)'} — état : {status} — dernière ligne reçue : {last}"
                   f" — alertes 24 h : {counts.get(r.id, 0)} — mots-clés : {', '.join(r.get_keywords())}")
    return "\n".join(out) or "Aucune règle configurée."


def tail_logs(db, rule_id, lines=50, **_):
    rule = _rule(db, rule_id)
    n = _clip(lines, 1, MAX_LINES, 50)
    content = log_source.read_tail_lines(log_source.resolve_path(rule.log_file_path), 1024 * 1024)[-n:]
    if not content:
        return f"Aucune ligne lisible pour la règle #{rule.id}."
    return f"{len(content)} dernières lignes de #{rule.id} {rule.name} :\n" + "\n".join(_line(l) for l in content)


def get_log_context(db, rule_id=None, analysis_id=None, anchor=None, before=20, after=20, **_):
    b, a = _clip(before, 0, 50, 20), _clip(after, 0, 50, 20)
    if analysis_id:
        analysis = db.query(Analysis).filter(Analysis.id == _clip(analysis_id, 0, 10**9, 0)).first()
        if not analysis:
            raise ToolError(f"Analyse #{analysis_id} introuvable.")
        rule = _rule(db, analysis.rule_id)
        matched = analysis_lines(analysis)
    elif anchor and rule_id:
        rule = _rule(db, rule_id)
        matched = [str(anchor)]
    else:
        raise ToolError("Fournis analysis_id, ou rule_id + anchor (horodatage ou extrait de ligne).")
    lines = log_source.read_tail_lines(log_source.resolve_path(rule.log_file_path), SEARCH_MAX_BYTES)
    raws = [m.strip() for m in matched if m.strip()]
    idx = [i for i, l in enumerate(lines) if any(r in l for r in raws)]
    if not idx:
        return f"Repère introuvable dans les {SEARCH_MAX_BYTES // (1024 * 1024)} derniers Mo de #{rule.id} (rotation possible)."
    # Dernière occurrence du repère (et les occurrences proches qui la précèdent)
    last = idx[-1]
    first = min(i for i in idx if i >= last - 40)
    window = lines[max(0, first - b):last + 1 + a]
    start = max(0, first - b)
    out = [("» " if any(r in l for r in raws) else "  ") + _line(l) for l in window]
    return f"Contexte dans #{rule.id} {rule.name} (lignes {start + 1}–{start + len(window)}, repère marqué ») :\n" + "\n".join(out)


def search_logs(db, rule_id, query, max_results=50, **_):
    rule = _rule(db, rule_id)
    q = str(query or "").strip().lower()
    if len(q) < 2:
        raise ToolError("La recherche doit contenir au moins 2 caractères.")
    n = _clip(max_results, 1, MAX_LINES, 50)
    lines = log_source.read_tail_lines(log_source.resolve_path(rule.log_file_path), SEARCH_MAX_BYTES)
    hits = [l for l in lines if q in l.lower()]
    if not hits:
        return f"Aucune ligne contenant « {query} » dans les {SEARCH_MAX_BYTES // (1024 * 1024)} derniers Mo de #{rule.id}."
    shown = hits[-n:]
    return (f"{len(hits)} ligne(s) contenant « {query} » dans #{rule.id} {rule.name}"
            f"{f' ; les {len(shown)} plus récentes' if len(hits) > len(shown) else ''} :\n"
            + "\n".join(_line(l) for l in shown))


def find_correlated_events(db, time=None, analysis_id=None, window_minutes=10, rule_ids=None, **_):
    w = _clip(window_minutes, 1, 30, 10)
    if analysis_id:
        a = db.query(Analysis).filter(Analysis.id == _clip(analysis_id, 0, 10**9, 0)).first()
        if not a:
            raise ToolError(f"Analyse #{analysis_id} introuvable.")
        center = a.analyzed_at
    else:
        try:
            center = datetime.fromisoformat(str(time).replace("Z", "").strip())
        except (TypeError, ValueError):
            raise ToolError("Fournis analysis_id ou time au format 'AAAA-MM-JJ HH:MM' (UTC).")
    lo, hi = center - timedelta(minutes=w), center + timedelta(minutes=w)
    rules = db.query(Rule).order_by(Rule.id).all()
    if rule_ids:
        wanted = {_clip(r, 0, 10**9, 0) for r in (rule_ids if isinstance(rule_ids, list) else [rule_ids])}
        rules = [r for r in rules if r.id in wanted]

    out = [f"Événements entre {lo.strftime('%Y-%m-%d %H:%M')} et {hi.strftime('%Y-%m-%d %H:%M')} UTC :"]
    alerts = db.query(Analysis).filter(Analysis.analyzed_at >= lo, Analysis.analyzed_at <= hi).order_by(Analysis.analyzed_at).limit(20).all()
    names = {r.id: r.name for r in db.query(Rule).all()}
    out.append(f"\nAlertes Sentinel ({len(alerts)}) :")
    out += [f"- #{x.id} {x.analyzed_at.strftime('%H:%M:%S')} {names.get(x.rule_id, '?')} [{x.severity}] {short_summary(x, 160)}" for x in alerts] or ["- aucune"]

    budget = MAX_LINES
    for r in rules:
        if budget <= 0:
            break
        lines = log_source.read_tail_lines(log_source.resolve_path(r.log_file_path), CORRELATION_MAX_BYTES)
        found = [l for l in lines if (ts := parse_line_ts(l)) and lo <= ts <= hi]
        if found:
            keep = found[-min(15, budget):]
            budget -= len(keep)
            out.append(f"\nLignes de #{r.id} {r.name} ({len(found)} dans la fenêtre{f', {len(keep)} dernières' if len(found) > len(keep) else ''}) :")
            out += [_line(l) for l in keep]
    return "\n".join(out)


def list_analyses(db, rule_id=None, severity=None, since_hours=168, limit=20, **_):
    q = db.query(Analysis).filter(Analysis.analyzed_at >= datetime.utcnow() - timedelta(hours=_clip(since_hours, 1, 24 * 90, 168)))
    if rule_id:
        q = q.filter(Analysis.rule_id == _clip(rule_id, 0, 10**9, 0))
    if severity in ("info", "warning", "critical"):
        q = q.filter(Analysis.severity == severity)
    rows = q.order_by(desc(Analysis.analyzed_at)).limit(_clip(limit, 1, 30, 20)).all()
    names = {r.id: r.name for r in db.query(Rule).all()}
    if not rows:
        return "Aucune analyse correspondante."
    return "\n".join(f"#{a.id} [{a.detection_id or '-'}] {a.analyzed_at.strftime('%Y-%m-%d %H:%M')} UTC — {names.get(a.rule_id, '?')} — "
                     f"{a.severity} — {_status(a)} — {short_summary(a, 200)}" for a in rows)


def _status(a: Analysis) -> str:
    return {"one_off": "ponctuel, clos à la détection (rien à surveiller)"}.get(a.resolution_status, a.resolution_status or "non résolu")


def get_analysis(db, analysis_id=None, detection_id=None, **_):
    q = db.query(Analysis)
    if analysis_id:
        a = q.filter(Analysis.id == _clip(analysis_id, 0, 10**9, 0)).first()
    elif detection_id:
        a = q.filter(Analysis.detection_id == str(detection_id).lstrip("#")).first()
    else:
        raise ToolError("Fournis analysis_id ou detection_id.")
    if not a:
        raise ToolError("Analyse introuvable.")
    rule = db.query(Rule).filter(Rule.id == a.rule_id).first()
    parts = [f"Analyse #{a.id} [{a.detection_id or '-'}] — règle #{a.rule_id} {rule.name if rule else '?'} — {a.analyzed_at.strftime('%Y-%m-%d %H:%M')} UTC — sévérité {a.severity}",
             f"Résolution : {_status(a)}" + (f" le {a.resolved_at.strftime('%Y-%m-%d %H:%M')} UTC" if a.resolved_at else ""),
             f"\nÉvénements :\n{a.triggered_line}"]
    before = json.loads(a.context_before_json or "[]")
    after = json.loads(a.context_after_json or "[]")
    if before or after:
        parts.append("\nContexte avant :\n" + "\n".join(before) + "\nContexte après :\n" + "\n".join(after))
    parts.append(f"\nAnalyse :\n{a.ollama_response or ''}")
    notes = render_notes(event_notes.for_analysis(db, a), "fr")
    if notes:
        parts.append("\n" + notes)
    return "\n".join(parts)


def get_rule_stats(db, rule_id, days=7, **_):
    rule = _rule(db, rule_id)
    d = _clip(days, 1, 30, 7)
    rows = db.query(Analysis).filter(Analysis.rule_id == rule.id,
                                     Analysis.analyzed_at >= datetime.utcnow() - timedelta(days=d)).all()
    if not rows:
        return f"Aucune alerte sur #{rule.id} {rule.name} ces {d} derniers jours."
    by_day = defaultdict(Counter)
    sigs = Counter()
    for a in rows:
        by_day[a.analyzed_at.strftime("%Y-%m-%d")][a.severity] += 1
        for s in {line_signature(l) for l in analysis_lines(a)}:
            sigs[s] += 1
    durations = [(a.resolved_at - a.analyzed_at).total_seconds() / 60 for a in rows
                 if a.resolved_at and not a.exclude_from_mttr]
    out = [f"#{rule.id} {rule.name} — {len(rows)} alerte(s) sur {d} jours — état actuel : {rule.alert_status or 'normal'}"]
    out.append("Par jour : " + " ; ".join(f"{day} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
                                         for day, c in sorted(by_day.items())))
    out.append(f"MTTR : {sum(durations) / len(durations):.0f} min sur {len(durations)} résolution(s)" if durations else "MTTR : aucune résolution mesurée")
    out.append("Événements les plus fréquents :")
    out += [f"- ×{n} {s[:200]}" for s, n in sigs.most_common(5)]
    return "\n".join(out)


def get_resolution_history(db, rule_id, limit=10, **_):
    rule = _rule(db, rule_id)
    rows = (db.query(ResolutionVerdict).filter(ResolutionVerdict.rule_id == rule.id)
            .order_by(desc(ResolutionVerdict.created_at)).limit(_clip(limit, 1, 20, 10)).all())
    if not rows:
        return f"Aucun verdict de résolution pour #{rule.id}."
    return "\n".join(f"- {v.created_at.strftime('%Y-%m-%d %H:%M')} UTC — {v.outcome} — déclencheur : {v.trigger}"
                     + (f" — IA : {v.ai_confidence}% « {(v.ai_explanation or '')[:160]} »" if v.ai_explanation else "")
                     for v in rows)


# ── Actions proposées ──────────────────────────────────────────────────────────

ACTIONS = {
    "add_excluded_pattern": ("rule_id", "value"),
    "add_keyword": ("rule_id", "value"),
    "remove_keyword": ("rule_id", "value"),
    "mark_resolved": ("rule_id",),
    "exclude_from_mttr": ("analysis_id",),
    "resend_notification": ("analysis_id",),
    "trigger_meta_analysis": ("config_id",),
}


def describe_action(action: str, params: dict, lang: str = "fr") -> str:
    v, r, a, c = params.get("value"), params.get("rule_id"), params.get("analysis_id"), params.get("config_id")
    fr = {
        "add_excluded_pattern": f"Ajouter le motif d'exclusion « {v} » à la règle #{r}",
        "add_keyword": f"Ajouter le mot-clé « {v} » à la règle #{r}",
        "remove_keyword": f"Retirer le mot-clé « {v} » de la règle #{r}",
        "mark_resolved": f"Marquer la règle #{r} comme résolue",
        "exclude_from_mttr": f"Exclure l'analyse #{a} du calcul du MTTR",
        "resend_notification": f"Renvoyer la notification de l'analyse #{a}",
        "trigger_meta_analysis": f"Lancer la méta-analyse #{c}",
    }
    en = {
        "add_excluded_pattern": f"Add exclusion pattern \"{v}\" to rule #{r}",
        "add_keyword": f"Add keyword \"{v}\" to rule #{r}",
        "remove_keyword": f"Remove keyword \"{v}\" from rule #{r}",
        "mark_resolved": f"Mark rule #{r} as resolved",
        "exclude_from_mttr": f"Exclude analysis #{a} from MTTR",
        "resend_notification": f"Resend the notification of analysis #{a}",
        "trigger_meta_analysis": f"Run meta-analysis #{c}",
    }
    return (en if lang == "en" else fr).get(action, action)


def describe_effect(action: str, params: dict, lang: str = "fr") -> str:
    """Effet concret d'une action appliquée, et où le constater dans l'interface."""
    v, r, a, c = params.get("value"), params.get("rule_id"), params.get("analysis_id"), params.get("config_id")
    fr = {
        "add_excluded_pattern": f"Les lignes contenant « {v} » sont désormais ignorées par la règle #{r} (Règles → motifs d'exclusion).",
        "add_keyword": f"La règle #{r} surveille désormais le mot-clé « {v} » (Règles → mots-clés).",
        "remove_keyword": f"La règle #{r} ne surveille plus le mot-clé « {v} » (Règles → mots-clés).",
        "mark_resolved": f"La règle #{r} est revenue à l'état normal (Monitor).",
        "exclude_from_mttr": f"L'analyse #{a} n'est plus comptée dans le MTTR du tableau de bord. Les règles et les futures alertes ne changent pas.",
        "resend_notification": f"La notification de l'analyse #{a} a été renvoyée.",
        "trigger_meta_analysis": f"La méta-analyse #{c} est lancée ; le résultat apparaîtra dans Méta-analyse.",
    }
    en = {
        "add_excluded_pattern": f"Lines containing \"{v}\" are now ignored by rule #{r} (Rules → exclusion patterns).",
        "add_keyword": f"Rule #{r} now watches the keyword \"{v}\" (Rules → keywords).",
        "remove_keyword": f"Rule #{r} no longer watches the keyword \"{v}\" (Rules → keywords).",
        "mark_resolved": f"Rule #{r} is back to normal (Monitor).",
        "exclude_from_mttr": f"Analysis #{a} is no longer counted in the dashboard MTTR. Rules and future alerts are unchanged.",
        "resend_notification": f"The notification of analysis #{a} was resent.",
        "trigger_meta_analysis": f"Meta-analysis #{c} started; the result will appear in Meta-analysis.",
    }
    return (en if lang == "en" else fr).get(action, "ok")


def propose_action(db, action=None, reason=None, rule_id=None, analysis_id=None, config_id=None, value=None,
                   _conversation_id=None, **_):
    if action not in ACTIONS:
        raise ToolError(f"Action inconnue. Actions possibles : {', '.join(ACTIONS)}.")
    down = _SEEN_DOWN.get(_conversation_id) if _conversation_id is not None else None
    if action == "mark_resolved" and down:
        listing = " ; ".join(f"{name} : {why}" for name, why in list(down.items())[:5])
        raise ToolError(f"Clôture non proposée : tes propres vérifications montrent un conteneur encore en anomalie ({listing}). "
                        "Trouver la cause ne règle pas le problème. Dis à l'utilisateur ce qu'il faut faire pour le remettre en marche ; "
                        "une fois reparti, une nouvelle vérification le montrera et la clôture redeviendra possible.")
    cited = _alert_citing_cve(db, _conversation_id)
    if action == "mark_resolved" and cited:
        raise ToolError("Clôture non proposée : cette alerte cite une faille. Clore l'incident ne corrige rien, et seul l'utilisateur "
                        "sait si la machine est à jour et si l'attaque est restée sans effet. Dis-lui ce qu'il reste à vérifier.")
    if action == "add_excluded_pattern" and cited and str(value or "").strip().lower() in cited.lower():
        raise ToolError("Exclusion non proposée : ce motif est dans la ligne de cette alerte de sécurité, il masquerait les prochaines "
                        "tentatives, y compris une attaque de la même source.")
    params = {}
    for key in ACTIONS[action]:
        val = {"rule_id": rule_id, "analysis_id": analysis_id, "config_id": config_id, "value": value}[key]
        if val in (None, ""):
            raise ToolError(f"Paramètre manquant pour {action} : {key}.")
        params[key] = str(val).strip()[:200] if key == "value" else _clip(val, 0, 10**9, 0)
    if "rule_id" in params:
        rule = _rule(db, params["rule_id"])
        if action == "remove_keyword" and params["value"] not in rule.get_keywords():
            raise ToolError(f"« {params['value']} » n'est pas un mot-clé de la règle #{rule.id} ({', '.join(rule.get_keywords())}).")
        if action == "add_excluded_pattern":
            targets, scope = _exclusion_targets(db, rule, _conversation_id)
            low = params["value"].lower()
            if targets and not any(keyword_match.contains(low, l.lower()) for l in targets):
                raise ToolError(f"Exclusion non proposée : « {params['value']} » n'apparaît dans aucune ligne {scope}, elle ne "
                                "ferait rien disparaître. Reprends un fragment exact d'une ligne déclenchante (copié de la ligne, "
                                "pas déduit d'un nom ou d'une adresse vus ailleurs).")
    if "analysis_id" in params and not db.query(Analysis).filter(Analysis.id == params["analysis_id"]).first():
        raise ToolError(f"Analyse #{params['analysis_id']} introuvable.")
    if "config_id" in params and not db.query(MetaAnalysisConfig).filter(MetaAnalysisConfig.id == params["config_id"]).first():
        raise ToolError(f"Méta-analyse #{params['config_id']} introuvable.")
    act = ChatAction(conversation_id=_conversation_id, action=action, params_json=json.dumps(params, ensure_ascii=False),
                     reason=(reason or "")[:500], status="pending")
    db.add(act)
    db.commit()
    db.refresh(act)
    return (f"Action #{act.id} proposée : {describe_action(action, params)}. Elle est EN ATTENTE de confirmation par "
            f"l'utilisateur et n'est PAS appliquée ; ne la présente pas comme effectuée. "
            f"Termine ta réponse en la mentionnant en une phrase, sans réécrire ton analyse.")


def action_to_dict(act: ChatAction, lang: str = "fr") -> dict:
    params = json.loads(act.params_json or "{}")
    return {"id": act.id, "action": act.action, "params": params, "reason": act.reason,
            "description": describe_action(act.action, params, lang), "status": act.status, "result": act.result}


async def apply_action(action_id: int) -> dict:
    """Exécute une action confirmée par l'utilisateur."""
    db = SessionLocal()
    try:
        act = db.query(ChatAction).filter(ChatAction.id == action_id).first()
        if not act:
            raise ToolError("action_not_found")
        if act.status != "pending":
            return action_to_dict(act)
        params = json.loads(act.params_json or "{}")
        try:
            if act.action in ("add_excluded_pattern", "add_keyword", "remove_keyword"):
                from app.models import KeywordChange
                from app.services.keyword_tuning import apply_change_to_rule
                rule = _rule(db, params["rule_id"])
                kind, change = {"add_excluded_pattern": ("exclusion", "add"), "add_keyword": ("keyword", "add"),
                                "remove_keyword": ("keyword", "remove")}[act.action]
                if apply_change_to_rule(rule, kind, change, params["value"]):
                    # Journal des mots-clés de la règle (annulable depuis l'onglet Mots-clés)
                    db.add(KeywordChange(rule_id=rule.id, kind=kind, action=change, value=params["value"],
                                         origin="chat", status="applied", reason=(act.reason or "")[:1000],
                                         decided_at=datetime.utcnow()))
                db.commit()
            elif act.action == "mark_resolved":
                from app.main import resolution_service
                await resolution_service.mark_resolved_manually(params["rule_id"])
            elif act.action == "exclude_from_mttr":
                a = db.query(Analysis).filter(Analysis.id == params["analysis_id"]).first()
                a.exclude_from_mttr = True
                db.commit()
            elif act.action == "resend_notification":
                from app.main import orchestrator
                from app.services.app_config import config_to_dict
                from app.models import GlobalConfig
                a = db.query(Analysis).filter(Analysis.id == params["analysis_id"]).first()
                rule = _rule(db, a.rule_id)
                await orchestrator.trigger_notification(a, rule, config_to_dict(db.query(GlobalConfig).first()), db, manual=True)
            elif act.action == "trigger_meta_analysis":
                from app.main import meta_service
                from app.routers.meta_analysis import _running_configs
                cid = params["config_id"]
                if cid in _running_configs:
                    raise ToolError("Cette méta-analyse est déjà en cours.")

                async def runner():
                    _running_configs.add(cid)
                    try:
                        await meta_service.execute_meta_analysis(cid, custom_context=None)
                    finally:
                        _running_configs.discard(cid)
                asyncio.create_task(runner())
            act.status = "applied"
            from app.models import GlobalConfig
            cfg = db.query(GlobalConfig).first()
            act.result = describe_effect(act.action, params, cfg.site_lang if cfg and cfg.site_lang else "fr")
        except Exception as e:
            act.status = "failed"
            act.result = str(e)[:500]
            logger.error("LLMTools", f"Échec de l'action #{act.id} ({act.action}) : {e}")
        act.decided_at = datetime.utcnow()
        db.commit()
        return action_to_dict(act)
    finally:
        db.close()


# ── Diagnostic de l'infrastructure (lecture seule, si configuré) ──────────────

def _diag(fn, *args):
    try:
        return diag.as_text(fn(*args))
    except diag.DiagnosticError as e:
        raise ToolError(str(e))


_EXIT_CODE = re.compile(r"Exited \((\d+)\)")


def _is_problem(c: dict) -> bool:
    """Conteneur en anomalie : redémarrage en boucle, unhealthy, mort, ou arrêté sur une erreur.
    Un conteneur jamais démarré ou arrêté normalement (code 0, 143 = SIGTERM) n'en est pas un."""
    status = c.get("status") or ""
    if c.get("state") in ("restarting", "dead") or "unhealthy" in status:
        return True
    if c.get("state") == "exited":
        m = _EXIT_CODE.search(status)
        return bool(m and m.group(1) not in ("0", "143"))
    return False


def _link(db, kind, connection, conversation_id, rule_id=None) -> dict:
    """La connexion demandée, si cette conversation (ou, pour le second regard, cette règle) y a droit.
    Sans précision, la seule disponible."""
    if rule_id is not None:
        rule = db.query(Rule).filter(Rule.id == rule_id).first()
        links = diag.links_for_rule(db, rule, kind) if rule else []
    else:
        links = diag.links_for_conversation(db, conversation_id, kind)
    if not links:
        raise ToolError(f"aucune connexion {dict(docker='Docker', ssh='SSH', web='de recherche web')[kind]} pour cette conversation")
    names = ", ".join(l["name"] for l in links)
    if connection:
        for link in links:
            if link["name"].lower() == str(connection).strip().lower():
                return link
        raise ToolError(f"connexion « {connection} » inconnue (disponibles : {names})")
    if len(links) > 1:
        raise ToolError(f"précise connection parmi : {names}")
    return links[0]


# Conteneurs vus en anomalie par les outils, par conversation : tant qu'il en reste, on ne propose pas de clore
_SEEN_DOWN: dict = {}
_SEEN_DOWN_MAX = 200


def _note_container(conversation_id, name: str, problem: Optional[str]):
    if conversation_id is None or not name:
        return
    seen = _SEEN_DOWN.setdefault(conversation_id, {})
    if problem:
        seen[name] = problem
    else:
        seen.pop(name, None)
    while len(_SEEN_DOWN) > _SEEN_DOWN_MAX:
        _SEEN_DOWN.pop(next(iter(_SEEN_DOWN)))


def _status_problem(s: dict) -> Optional[str]:
    """Anomalie d'un conteneur d'après son état détaillé, ou None s'il tourne normalement."""
    health = (s.get("health") or {}).get("status")
    if s.get("restarting"):
        return "redémarre en boucle"
    if health == "unhealthy":
        return "en mauvaise santé (unhealthy)"
    if not s.get("running") and s.get("exit_code") not in (0, 143, None):
        return f"arrêté (code {s.get('exit_code')})"
    return None


def docker_list_containers(db, only_problems=False, connection=None, _conversation_id=None, _rule_id=None, **_):
    rows = _diag(diag.docker_containers, _link(db, "docker", connection, _conversation_id, _rule_id)["docker_url"])
    data = json.loads(rows)
    for c in data:
        _note_container(_conversation_id, c.get("name"), f"{c.get('state')} — {c.get('status')}" if _is_problem(c) else None)
    if only_problems:
        data = [c for c in data if _is_problem(c)]
    if not data:
        return "Aucun conteneur en anomalie." if only_problems else "Aucun conteneur."
    return "\n".join(f"- {c['name']} — {c['state']} — {c['status']} — {c['image']}" for c in data)


def docker_container_status(db, name, connection=None, _conversation_id=None, _rule_id=None, **_):
    out = _diag(diag.docker_container_status, _link(db, "docker", connection, _conversation_id, _rule_id)["docker_url"], name)
    try:
        state = json.loads(out)
        _note_container(_conversation_id, state.get("name") or name, _status_problem(state))
    except (ValueError, AttributeError):
        pass
    return out


def docker_container_logs(db, name, lines=100, since_minutes=None, connection=None, _conversation_id=None, _rule_id=None, **_):
    out = _diag(diag.docker_container_logs, _link(db, "docker", connection, _conversation_id, _rule_id)["docker_url"], name, _clip(lines, 1, diag.MAX_LOG_LINES, 100), since_minutes)
    return out or "(aucune ligne)"


def docker_container_stats(db, name, connection=None, _conversation_id=None, **_):
    return _diag(diag.docker_container_stats, _link(db, "docker", connection, _conversation_id)["docker_url"], name)


def _exclusion_targets(db, rule: Rule, conversation_id) -> tuple:
    """Lignes qu'une exclusion proposée doit recouvrir : celles de l'alerte de la conversation quand elle est de cette
    règle, sinon celles des alertes de la règle sur 30 jours. Aucune ligne : rien à vérifier."""
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first() if conversation_id else None
    analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first() if conv and conv.analysis_id else None
    if analysis and analysis.rule_id == rule.id:
        return analysis_lines(analysis), "de l'alerte de cette conversation"
    since = datetime.utcnow() - timedelta(days=30)
    rows = db.query(Analysis).filter(Analysis.rule_id == rule.id, Analysis.analyzed_at >= since).all()
    return [l for a in rows for l in analysis_lines(a)], f"des alertes de la règle #{rule.id} sur 30 jours"


def _alert_citing_cve(db, conversation_id) -> str:
    """La ligne de l'alerte de cette conversation si elle cite un CVE, sinon une chaîne vide."""
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first() if conversation_id else None
    analysis = db.query(Analysis).filter(Analysis.id == conv.analysis_id).first() if conv and conv.analysis_id else None
    return analysis.triggered_line if analysis and cve_lookup.first_cve(analysis.triggered_line) else ""


def _lang(db) -> str:
    config = db.query(GlobalConfig).first()
    return (config.ollama_prompt_lang if config else "") or "fr"


def lookup_cve(db, cve=None, **_):
    if not cve_lookup.enabled(db):
        raise ToolError("Les fiches CVE sont désactivées dans Configuration > Diagnostic.")
    return cve_lookup.sheet_text(cve_lookup.lookup(db, cve), _lang(db))


def web_search_tool(db, query=None, connection=None, _conversation_id=None, **_):
    link = _link(db, "web", connection, _conversation_id)
    why = web_search.query_refusal(query, web_search.private_terms(db))
    if why:
        reasons = {"empty": "vide", "long": "trop longue : ne colle pas une ligne de log", "ip": "elle contient une adresse IP",
                   "mac": "elle contient une adresse MAC", "mail": "elle contient une adresse e-mail", "path": "elle contient un chemin de machine",
                   "token": "elle contient un identifiant ou un jeton", "domain": "elle contient un domaine interne", "name": "elle contient le nom d'une de tes machines"}
        raise ToolError(f"Requête refusée, {reasons[why]}. Elle partirait vers des moteurs publics : reformule avec seulement des termes publics (produit, version, message d'erreur, CVE).")
    refused = web_search.quota_refusal(_conversation_id, _lang(db))
    if refused:
        return refused
    try:
        return web_search.search_text(link["web_url"], query, _lang(db))
    except web_search.WebSearchError as e:
        raise ToolError(f"recherche web indisponible ({e}). Dis-le à l'utilisateur et n'invente rien.")


def get_attack_summary(db, rule_id, days=7, **_):
    rule = _rule(db, rule_id)
    return attack_summary.summary_text(attack_summary.summary(rule, _clip(days, 1, 30, 7)), _lang(db))


def host_check(db, check, target=None, connection=None, _conversation_id=None, _rule_id=None, **_):
    return _diag(diag.run_host_check, _link(db, "ssh", connection, _conversation_id, _rule_id), check, target)


# ── Définitions (format Ollama) et exécution ───────────────────────────────────

def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


_INT = {"type": "integer"}
TOOLS = [
    _fn("list_rules", "Liste les règles de surveillance (id, source, état d'alerte, dernière ligne reçue, alertes des dernières 24 h, mots-clés).", {}),
    _fn("tail_logs", "Dernières lignes de la source de logs d'une règle (100 max).",
        {"rule_id": _INT, "lines": _INT}, ["rule_id"]),
    _fn("get_log_context", "Lignes autour d'un événement dans la source : soit autour des lignes d'une analyse (analysis_id), soit autour d'un repère textuel (rule_id + anchor : horodatage ou extrait de ligne). 50 lignes max avant et après.",
        {"analysis_id": _INT, "rule_id": _INT, "anchor": {"type": "string"}, "before": _INT, "after": _INT}),
    _fn("search_logs", "Cherche une chaîne (insensible à la casse, pas de regex) dans la source d'une règle ; renvoie les occurrences les plus récentes (100 max) et le total.",
        {"rule_id": _INT, "query": {"type": "string"}, "max_results": _INT}, ["rule_id", "query"]),
    _fn("find_correlated_events", "Ce qui s'est passé sur TOUTES les règles autour d'un instant : alertes Sentinel et lignes de logs horodatées. Instant donné par analysis_id ou time ('AAAA-MM-JJ HH:MM', UTC). Fenêtre ±window_minutes (30 max).",
        {"analysis_id": _INT, "time": {"type": "string"}, "window_minutes": _INT, "rule_ids": {"type": "array", "items": _INT}}),
    _fn("list_analyses", "Liste les analyses (alertes) récentes avec leur résumé, filtrables par règle et sévérité (30 max).",
        {"rule_id": _INT, "severity": {"type": "string", "enum": ["info", "warning", "critical"]}, "since_hours": _INT, "limit": _INT}),
    _fn("get_analysis", "Détail complet d'une analyse : événements, contexte, analyse, statut de résolution.",
        {"analysis_id": _INT, "detection_id": {"type": "string"}}),
    _fn("get_rule_stats", "Statistiques d'une règle sur N jours (30 max) : alertes par jour et sévérité, MTTR, événements les plus fréquents.",
        {"rule_id": _INT, "days": _INT}, ["rule_id"]),
    _fn("get_resolution_history", "Historique des verdicts de résolution (retour à la normale) d'une règle.",
        {"rule_id": _INT, "limit": _INT}, ["rule_id"]),
    _fn("get_attack_summary", "Tentatives bloquées par le pare-feu sur une source CEF (UniFi…), regroupées par port et sens : flux, nombre de sources, signature. Montre ce qui est exposé et visé, au-delà d'une alerte isolée.",
        {"rule_id": _INT, "days": _INT}, ["rule_id"]),
    _fn("propose_action", "Propose une modification à l'utilisateur, qui devra la confirmer (rien n'est appliqué sans son accord). "
        "Actions : add_excluded_pattern / add_keyword / remove_keyword (rule_id + value), mark_resolved (rule_id), "
        "exclude_from_mttr (analysis_id : exclut UNE analyse précise du calcul du MTTR, ne change ni la règle ni les futures alertes), "
        "resend_notification (analysis_id), trigger_meta_analysis (config_id). Pour ne plus être alerté sur un bruit récurrent, "
        "c'est add_excluded_pattern (ou remove_keyword) qu'il faut proposer. Explique la raison dans reason.",
        {"action": {"type": "string", "enum": list(ACTIONS)}, "reason": {"type": "string"}, "rule_id": _INT,
         "analysis_id": _INT, "config_id": _INT, "value": {"type": "string"}}, ["action", "reason"]),
]

DOCKER_TOOLS = [
    _fn("docker_list_containers", "Liste les conteneurs Docker du serveur (nom, état, statut avec santé et durée, image). only_problems=true : seulement ceux en redémarrage, unhealthy, morts ou arrêtés sur une erreur (pas ceux arrêtés volontairement).",
        {"only_problems": {"type": "boolean"}}),
    _fn("docker_container_status", "État détaillé d'un conteneur Docker : statut, santé et derniers healthchecks, nombre de redémarrages, code de sortie, OOM, dates de démarrage et d'arrêt, montages, réseaux.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("docker_container_logs", "Dernières lignes des logs d'un conteneur Docker (200 max), éventuellement limitées aux since_minutes dernières minutes.",
        {"name": {"type": "string"}, "lines": _INT, "since_minutes": _INT}, ["name"]),
    _fn("docker_container_stats", "Consommation instantanée d'un conteneur Docker : CPU, mémoire et limite, processus, réseau.",
        {"name": {"type": "string"}}, ["name"]),
]
CVE_TOOLS = [
    _fn("lookup_cve", "Fiche officielle d'un CVE (base NVD) : description, score, exploitation active (catalogue KEV de la CISA), versions touchées. À appeler pour tout CVE cité, avant une recherche web.",
        {"cve": {"type": "string"}}, ["cve"]),
]
WEB_TOOLS = [
    _fn("web_search", "Recherche sur le web pour ce qui manque : message d'erreur inconnu, comportement d'un logiciel, version corrigée. Uniquement des termes publics et courts : jamais d'adresse IP, de nom de machine, de chemin, ni une ligne de log entière.",
        {"query": {"type": "string"}}, ["query"]),
]
HOST_TOOLS = [
    _fn("host_check", "Contrôle en lecture seule du serveur hôte. check : overview (charge, mémoire, disques), disk, memory, load, processes, mounts (partages réseau NFS/SMB), network, kernel (derniers messages du noyau, sans le bruit réseau de Docker), syslog, array (état de l'array Unraid et de ses disques, ou RAID logiciel / ZFS), failed-services, service (état d'un service ou d'une unité systemd, .mount compris, target = nom), journal (journal d'un service systemd, target = nom), fstab (montages déclarés au démarrage et unités de montage, mots de passe masqués), nfs-exports (partages exportés par un serveur NFS que cette machine monte déjà, target = nom ou adresse du serveur). Sur Unraid, pas de systemd : utiliser syslog, array et processes.",
        {"check": {"type": "string", "enum": list(diag.HOST_CHECKS)}, "target": {"type": "string"}}, ["check"]),
]


def _for_links(tools: list, links: list) -> list:
    """Outils de diagnostic pour ces connexions. À partir de deux, l'IA doit dire laquelle elle interroge."""
    if not links:
        return []
    if len(links) == 1:
        return tools
    tools = copy.deepcopy(tools)
    for tool in tools:
        params = tool["function"]["parameters"]
        params["properties"]["connection"] = {"type": "string", "enum": [l["name"] for l in links]}
        params["required"].append("connection")
    return tools


def available_tools(conversation_id: Optional[int] = None) -> list:
    """Outils du chat : les outils de diagnostic ne sont proposés que pour les connexions de cette conversation."""
    db = SessionLocal()
    try:
        return (TOOLS + _for_links(DOCKER_TOOLS, diag.links_for_conversation(db, conversation_id, "docker"))
                + _for_links(HOST_TOOLS, diag.links_for_conversation(db, conversation_id, "ssh"))
                + (CVE_TOOLS if cve_lookup.enabled(db) else [])
                + _for_links(WEB_TOOLS, diag.links_for_conversation(db, conversation_id, "web")))
    finally:
        db.close()


_EXECUTORS = {
    "list_rules": list_rules, "tail_logs": tail_logs, "get_log_context": get_log_context, "search_logs": search_logs,
    "find_correlated_events": find_correlated_events, "list_analyses": list_analyses, "get_analysis": get_analysis,
    "get_rule_stats": get_rule_stats, "get_resolution_history": get_resolution_history, "propose_action": propose_action,
    "docker_list_containers": docker_list_containers, "docker_container_status": docker_container_status,
    "docker_container_logs": docker_container_logs, "docker_container_stats": docker_container_stats, "host_check": host_check,
    "lookup_cve": lookup_cve, "web_search": web_search_tool, "get_attack_summary": get_attack_summary,
}


def tool_label(name: str, args: dict, lang: str = "fr") -> str:
    """Libellé lisible d'un appel d'outil pour l'interface."""
    r = f" #{args['rule_id']}" if args.get("rule_id") else ""
    fr = {
        "list_rules": "Liste des règles",
        "tail_logs": f"Dernières lignes{r}",
        "get_log_context": f"Contexte{r}" + (f" de l'analyse #{args['analysis_id']}" if args.get("analysis_id") else ""),
        "search_logs": f"Recherche « {args.get('query', '')} »{r}",
        "find_correlated_events": "Événements corrélés" + (f" autour de {args['time']}" if args.get("time") else ""),
        "list_analyses": f"Analyses récentes{r}",
        "get_analysis": f"Analyse #{args.get('analysis_id') or args.get('detection_id', '')}",
        "get_rule_stats": f"Statistiques{r}",
        "get_resolution_history": f"Historique de résolution{r}",
        "propose_action": f"Proposition : {args.get('action', '')}",
        "lookup_cve": f"Fiche {args.get('cve', '')}",
        "web_search": f"Web : {args.get('query', '')}",
        "get_attack_summary": f"Tentatives bloquées{r}",
        "docker_list_containers": "Conteneurs Docker" + (" en anomalie" if args.get("only_problems") else ""),
        "docker_container_status": f"État du conteneur {args.get('name', '')}",
        "docker_container_logs": f"Logs du conteneur {args.get('name', '')}",
        "docker_container_stats": f"Consommation du conteneur {args.get('name', '')}",
        "host_check": f"Serveur : {args.get('check', '')}" + (f" {args['target']}" if args.get("target") else ""),
    }
    en = {
        "list_rules": "Rule list",
        "tail_logs": f"Latest lines{r}",
        "get_log_context": f"Context{r}" + (f" of analysis #{args['analysis_id']}" if args.get("analysis_id") else ""),
        "search_logs": f"Search \"{args.get('query', '')}\"{r}",
        "find_correlated_events": "Correlated events" + (f" around {args['time']}" if args.get("time") else ""),
        "list_analyses": f"Recent analyses{r}",
        "get_analysis": f"Analysis #{args.get('analysis_id') or args.get('detection_id', '')}",
        "get_rule_stats": f"Statistics{r}",
        "get_resolution_history": f"Resolution history{r}",
        "propose_action": f"Proposal: {args.get('action', '')}",
        "lookup_cve": f"Sheet {args.get('cve', '')}",
        "web_search": f"Web: {args.get('query', '')}",
        "get_attack_summary": f"Blocked attempts{r}",
        "docker_list_containers": "Docker containers" + (" with problems" if args.get("only_problems") else ""),
        "docker_container_status": f"Container status {args.get('name', '')}",
        "docker_container_logs": f"Container logs {args.get('name', '')}",
        "docker_container_stats": f"Container usage {args.get('name', '')}",
        "host_check": f"Host: {args.get('check', '')}" + (f" {args['target']}" if args.get("target") else ""),
    }
    text = (en if lang == "en" else fr).get(name, name)
    return f"{text} · {args['connection']}" if args.get("connection") else text


def _run_sync(name: str, args: dict, conversation_id: Optional[int], rule_id: Optional[int] = None) -> str:
    db = SessionLocal()
    try:
        scope = {"_rule_id": rule_id} if rule_id is not None else {}
        return _EXECUTORS[name](db, **args, _conversation_id=conversation_id, **scope)
    finally:
        db.close()


async def execute_tool(name: str, args: dict, conversation_id: Optional[int] = None, rule_id: Optional[int] = None) -> str:
    """Exécute un outil et renvoie un texte borné ; les erreurs sont renvoyées au LLM sous forme de texte.
    rule_id : connexions de cette règle seulement (second regard, sans conversation)."""
    if name not in _EXECUTORS:
        return f"Erreur : outil inconnu « {name} »."
    args = {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}
    try:
        return truncate_output(await asyncio.to_thread(_run_sync, name, args, conversation_id, rule_id))
    except ToolError as e:
        return f"Erreur : {e}"
    except TypeError as e:
        return f"Erreur : paramètres invalides pour {name} ({e})."
    except Exception as e:
        logger.error("LLMTools", f"Erreur outil {name} : {e}")
        return f"Erreur interne de l'outil {name}."
