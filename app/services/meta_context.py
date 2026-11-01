"""
Contexte de la méta-analyse : agrégation des analyses de la période par événement (signature),
statistiques par règle, rappel de la synthèse précédente, et interprétation de la réponse structurée.
"""
import json
from collections import Counter, OrderedDict
from datetime import datetime
from typing import Optional

from sqlalchemy import desc, func

from app.models import Analysis, Rule, MetaAnalysisConfig, MetaAnalysisResult
from app.services.alert_analysis import analysis_lines, short_summary, _load_json
from app.services.prompts import meta_label
from app.utils.log_utils import line_signature

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
CHARS_PER_TOKEN = 3.0


def _rule_ids(config: MetaAnalysisConfig) -> list:
    try:
        return json.loads(config.rule_ids_json) if config.rule_ids_json else []
    except ValueError:
        return []


def collect(db, config: MetaAnalysisConfig, period_start: datetime, period_end: datetime,
            excluded_ids=(), annotations: Optional[dict] = None) -> dict:
    """Agrège les analyses de la période (au plus config.max_analyses, les plus récentes) par règle puis par événement."""
    rule_ids = _rule_ids(config)
    base = db.query(Analysis).filter(Analysis.analyzed_at >= period_start, Analysis.analyzed_at <= period_end)
    if rule_ids:
        base = base.filter(Analysis.rule_id.in_(rule_ids))
    totals_by_rule = dict(base.with_entities(Analysis.rule_id, func.count(Analysis.id)).group_by(Analysis.rule_id).all())
    rows = base.order_by(desc(Analysis.analyzed_at)).limit(config.max_analyses).all()
    excluded = {int(i) for i in excluded_ids or []}
    rows = [a for a in reversed(rows) if a.id not in excluded]  # ordre chronologique
    notes = {int(k): str(v).strip() for k, v in (annotations or {}).items() if str(v).strip()}

    rules = {r.id: r for r in db.query(Rule).all()}
    per_rule = OrderedDict()
    keywords = set()
    for a in rows:
        sigs = sorted({line_signature(l) for l in analysis_lines(a)}) or [(a.triggered_line or "")[:200]]
        key = tuple(sigs[:3])
        pr = per_rule.setdefault(a.rule_id, {"rule_id": a.rule_id, "rule": rules.get(a.rule_id),
                                             "analyses": [], "groups": OrderedDict()})
        pr["analyses"].append(a)
        g = pr["groups"].setdefault(key, {"analyses": [], "annotations": []})
        g["analyses"].append(a)
        if a.id in notes:
            g["annotations"].append(notes[a.id])
        if a.matched_keywords_json:
            try:
                keywords.update(json.loads(a.matched_keywords_json))
            except ValueError:
                pass

    for pr in per_rule.values():
        pr["total"] = totals_by_rule.get(pr["rule_id"], len(pr["analyses"]))
        pr["max_rank"] = max(SEVERITY_RANK.get(a.severity, 0) for a in pr["analyses"])
    ordered = sorted(per_rule.values(), key=lambda pr: (-pr["max_rank"], -len(pr["analyses"])))

    watched = [r for r in rules.values() if (r.id in rule_ids if rule_ids else r.enabled)]
    silent = [r.name for r in watched if r.id not in per_rule]
    return {
        "rows": rows,
        "per_rule": ordered,
        "silent_rules": silent,
        "total_period": sum(totals_by_rule.values()),
        "included": len(rows),
        "limit": config.max_analyses,
        "detection_ids": [a.detection_id for a in rows if a.detection_id],
        "keywords": sorted(keywords),
    }


def previous_result(db, config_id: int, before: datetime) -> Optional[MetaAnalysisResult]:
    return (db.query(MetaAnalysisResult)
            .filter(MetaAnalysisResult.config_id == config_id, MetaAnalysisResult.period_end <= before)
            .order_by(desc(MetaAnalysisResult.period_end)).first())


def _hours(analyses) -> str:
    c = Counter(a.analyzed_at.hour for a in analyses)
    return ", ".join(f"{h:02d}h ×{n}" for h, n in sorted(c.items(), key=lambda x: (-x[1], x[0]))[:4])


def _render_group(g: dict, lang: str) -> str:
    an = g["analyses"]
    sev = max((a.severity for a in an), key=lambda s: SEVERITY_RANK.get(s, 0))
    first, last = an[0].analyzed_at, an[-1].analyzed_at
    span = first.strftime("%Y-%m-%d %H:%M") if len(an) == 1 else f"{first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M}"
    unresolved = sum(1 for a in an if not a.resolved_at)
    lines = [f"- {meta_label(lang, 'group', count=len(an), severity=sev, span=span, hours=_hours(an), unresolved=meta_label(lang, 'unresolved', n=unresolved) if unresolved else '')}"]
    example = " ".join((analysis_lines(an[-1]) or [an[-1].triggered_line or ""])[-1].split())
    lines.append("  " + meta_label(lang, "example", line=example[:300] + ("…" if len(example) > 300 else "")))
    summary = short_summary(an[-1], 300)
    if summary:
        lines.append("  " + meta_label(lang, "summary", summary=summary))
    for note in g["annotations"]:
        lines.append("  " + meta_label(lang, "annotation", text=note[:300]))
    return "\n".join(lines)


def _render_rule_header(pr: dict, lang: str) -> str:
    an = pr["analyses"]
    sev = Counter(a.severity for a in an)
    durations = [(a.resolved_at - a.analyzed_at).total_seconds() / 60 for a in an if a.resolved_at and not a.exclude_from_mttr]
    rule = pr["rule"]
    title = meta_label(lang, "rule", id=rule.id, name=rule.name) if rule else meta_label(lang, "rule", id="?", name="?")
    stats = meta_label(lang, "rule_stats", total=pr["total"], included=len(an),
                       severities=", ".join(f"{k} {v}" for k, v in sorted(sev.items(), key=lambda x: -SEVERITY_RANK.get(x[0], 0))),
                       resolved=sum(1 for a in an if a.resolved_at),
                       mttr=meta_label(lang, "mttr", mttr=round(sum(durations) / len(durations))) if durations else "",
                       state=(rule.alert_status or "normal") if rule else "?")
    ctx = f"\n{rule.application_context.strip()}" if rule and rule.application_context else ""
    return f"## {title}\n{stats}{ctx}"


def render_context(agg: dict, period_start: datetime, period_end: datetime, lang: str,
                   previous: Optional[MetaAnalysisResult], budget_chars: int) -> str:
    """Texte agrégé envoyé au LLM, rempli par priorité (règles les plus sévères d'abord) dans le budget."""
    parts = [meta_label(lang, "period", start=f"{period_start:%Y-%m-%d %H:%M}", end=f"{period_end:%Y-%m-%d %H:%M}"),
             meta_label(lang, "totals", total=agg["total_period"], included=agg["included"], limit=agg["limit"])]
    if previous:
        tldr = previous.tldr or short_summary(SimpleText(previous.ollama_response), 600)
        parts.append(f"## {meta_label(lang, 'previous', start=f'{previous.period_start:%Y-%m-%d}', end=f'{previous.period_end:%Y-%m-%d}', status=previous.status or '?')}\n{tldr}")
    if agg["silent_rules"]:
        parts.append(meta_label(lang, "silent", names=", ".join(agg["silent_rules"])))

    used = sum(len(p) for p in parts)
    omitted_groups = omitted_alerts = 0
    for pr in agg["per_rule"]:
        header = _render_rule_header(pr, lang)
        groups = sorted(pr["groups"].values(),
                        key=lambda g: (-max(SEVERITY_RANK.get(a.severity, 0) for a in g["analyses"]), -len(g["analyses"])))
        block = [header]
        for g in groups:
            text = _render_group(g, lang)
            if used + sum(len(b) + 1 for b in block) + len(text) > budget_chars:
                omitted_groups += 1
                omitted_alerts += len(g["analyses"])
                continue
            block.append(text)
        if len(block) > 1:
            text = "\n".join(block)
            parts.append(text)
            used += len(text)
    if omitted_groups:
        parts.append(meta_label(lang, "omitted", groups=omitted_groups, alerts=omitted_alerts))
    return "\n\n".join(parts)


class SimpleText:
    """Adaptateur minimal pour short_summary() sur un texte brut (anciens résultats sans tl;dr)."""
    def __init__(self, text):
        self.summary = None
        self.ollama_response = text or ""


def budget_for(config: MetaAnalysisConfig, think: bool) -> int:
    reserve = 4096 + (4096 if think else 0)
    return max(4000, int(((config.context_size or 16000) - reserve) * CHARS_PER_TOKEN))


def parse_meta_response(content: str, lang: str) -> dict:
    """{"markdown", "tldr", "status", "data"} ; repli sur le texte brut si le JSON est inexploitable."""
    data = _load_json(content)
    if not data or not (data.get("tldr") or data.get("synthesis")):
        return {"markdown": content.strip(), "tldr": None, "status": None, "data": None}
    status = data.get("status") if data.get("status") in ("ok", "degraded", "critical") else None
    tldr = " ".join(str(data.get("tldr") or "").split())[:600] or None
    parts = []
    if status:
        parts.append(f"**{meta_label(lang, 'state')} : {meta_label(lang, 'status_label')[status]}**")
    if tldr:
        parts.append(f"**TL;DR :** {tldr}")
    highlights = [h.strip() for h in data.get("highlights") or [] if str(h).strip()]
    if highlights:
        parts.append(f"### {meta_label(lang, 'highlights')}\n" + "\n".join(f"- {h}" for h in highlights))
    if data.get("synthesis"):
        parts.append(str(data["synthesis"]).strip())
    recos = [r.strip() for r in data.get("recommendations") or [] if str(r).strip()]
    parts.append(f"### {meta_label(lang, 'recommendations')}\n" +
                 ("\n".join(f"{i}. {r}" for i, r in enumerate(recos, 1)) if recos else meta_label(lang, "no_action")))
    return {"markdown": "\n\n".join(parts), "tldr": tldr, "status": status, "data": data}
