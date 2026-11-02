"""
Construction du contexte et des messages de l'analyse d'alerte, et interprétation de la réponse structurée.
Tout est déterministe (pas d'outils) : contexte de la source, historique de la règle, budget de tokens.
"""
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import desc

from app.models import Analysis, Rule
from app.services import log_source
from app.services.prompts import ALERT_SYSTEM, label
from app.utils.log_utils import line_signature

HISTORY_DAYS = 7
CHARS_PER_TOKEN = 3.0  # estimation prudente (logs : beaucoup de symboles)


# ── Historique ─────────────────────────────────────────────────────────────────

def analysis_lines(a: Analysis) -> List[str]:
    """Lignes brutes d'une analyse (format structuré si disponible, sinon reconstruites depuis le texte)."""
    if a.triggered_lines_json:
        try:
            return json.loads(a.triggered_lines_json).get("lines", [])
        except (ValueError, AttributeError):
            pass
    lines = [l for l in (a.triggered_line or "").splitlines() if l.strip()]
    if len(lines) > 1:
        lines = lines[1:]  # ancien format groupé : la première ligne est l'en-tête
    return [l for l in lines if not l.startswith("... [")]


_SUMMARY_TAG = re.compile(r"^[-*_#\s]*(r[ée]sum[ée](\s+court)?|summary|tl;?dr)\s*[*_]*\s*:\s*[*_]*\s*", re.IGNORECASE)
_CONFIDENCE_LINE = re.compile(r"^[*_\s]*(confiance|confidence)\b", re.IGNORECASE)


def short_summary(a: Analysis, max_len: int = 300) -> str:
    """Résumé court d'une analyse : champ structuré, sinon dernier paragraphe du texte (anciennes analyses)."""
    text = a.summary
    if not text:
        paras = [p.strip() for p in (a.ollama_response or "").split("\n\n") if p.strip().strip("-")]
        # Paragraphe « Résumé : … » s'il existe (format structuré et anciens prompts), sinon le dernier hors confiance
        tagged = [p for p in paras if _SUMMARY_TAG.match(p)]
        rest = [p for p in paras if not _CONFIDENCE_LINE.match(p)]
        text = _SUMMARY_TAG.sub("", tagged[0]) if tagged else (rest[-1] if rest else "")
        text = text.replace("**", "").replace("__", "").strip("*_ ")
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[:max_len] + "…"


def _format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes:02d}"
    return f"{hours // 24} j"


def build_history(db, rule_id: int, signatures: set, ref_time: datetime,
                  exclude_id: Optional[int] = None, days: int = HISTORY_DAYS) -> dict:
    query = db.query(Analysis).filter(
        Analysis.rule_id == rule_id,
        Analysis.analyzed_at >= ref_time - timedelta(days=days),
        Analysis.analyzed_at < ref_time,
    )
    if exclude_id:
        query = query.filter(Analysis.id != exclude_id)
    rows = query.order_by(desc(Analysis.analyzed_at)).limit(300).all()
    similar = [a for a in rows if signatures & {line_signature(l) for l in analysis_lines(a)}]
    return {"days": days, "total": len(rows), "similar": similar}


def render_history(history: dict, lang: str, max_recent: int = 5) -> str:
    lines = [f"## {label(lang, 'history', days=history['days'])}"]
    similar = history["similar"]
    if not similar:
        lines.append(label(lang, "history_total", total=history["total"], similar=0))
        lines.append(label(lang, "history_first"))
        return "\n".join(lines)
    lines.append(label(lang, "history_total", total=history["total"], similar=len(similar)))
    hours = Counter(a.analyzed_at.hour for a in similar)
    lines.append(label(lang, "history_hours", hours=", ".join(f"{h:02d}h ×{n}" for h, n in sorted(hours.items()))))
    lines.append(label(lang, "history_recent"))
    for a in similar[:max_recent]:
        if a.resolved_at:
            status = label(lang, "resolved_after", duration=_format_duration((a.resolved_at - a.analyzed_at).total_seconds()))
        else:
            status = a.resolution_status or label(lang, "resolution_pending")
        lines.append(f"- {a.analyzed_at.strftime('%Y-%m-%d %H:%M')} UTC — {a.severity} — {status} — « {short_summary(a)} »")
    return "\n".join(lines)


# ── Messages ───────────────────────────────────────────────────────────────────

def _render_rule(rule: Rule, now: datetime, lang: str) -> str:
    kind = log_source.source_type(rule.log_file_path)
    if kind == "file":
        source = f"{label(lang, 'source_file')} {rule.log_file_path}"
    elif kind == "syslog":
        source = label(lang, "source_syslog", host=rule.log_file_path.split(":", 1)[1])
    else:
        source = label(lang, "source_webhook")
    lines = [f"## {label(lang, 'rule')}",
             f"{label(lang, 'name')} : {rule.name}",
             f"{label(lang, 'source')} : {source}"]
    if rule.application_context:
        lines.append(f"{label(lang, 'app_context')} : {rule.application_context.strip()}")
    lines.append(f"{label(lang, 'now')} : {now.strftime('%Y-%m-%d %H:%M')}")
    return "\n".join(lines)


def _render_context(context: dict, n: int, lang: str) -> str:
    body = list(context.get("before") or [])
    body += context.get("between") or [label(lang, "context_gap")]
    body += context.get("after") or []
    return f"## {label(lang, 'context', n=n)}\n" + "\n".join(body)


def _shrink_context(context: dict) -> dict:
    """Retire les lignes les plus éloignées des événements (une de chaque côté)."""
    before, after = list(context.get("before") or []), list(context.get("after") or [])
    between = context.get("between")
    if before:
        before = before[1:]
    if after:
        after = after[:-1]
    if not before and not after:
        between = None
    return {"before": before, "between": between, "after": after}


def build_alert_messages(rule: Rule, events_text: str, matched_keywords: Optional[list],
                         context: Optional[dict], history: Optional[dict],
                         config: dict, now: datetime) -> List[dict]:
    lang = config.get("ollama_prompt_lang", "fr")
    system = ALERT_SYSTEM.get(lang, ALERT_SYSTEM["fr"])
    if (config.get("system_prompt") or "").strip():
        system = f"## {label(lang, 'admin_instructions')}\n{config['system_prompt'].strip()}\n\n{system}"

    events = f"## {label(lang, 'events')}\n"
    if matched_keywords:
        events += f"{label(lang, 'keywords')} : {', '.join(matched_keywords)}\n"
    events += events_text

    # Budget : contexte total moins une réserve pour la réponse (et le raisonnement s'il est actif)
    reserve = 2048 + (2048 if config.get("ollama_think", True) else 0)
    budget = max(3000, int((config.get("ollama_ctx", 4096) - reserve) * CHARS_PER_TOKEN))
    rule_block = _render_rule(rule, now, lang)
    remaining = budget - len(system) - len(rule_block)
    if len(events) > remaining - 200:
        events = events[:max(500, remaining - 200)] + "…"
    remaining -= len(events)

    sections = [rule_block, events]
    # Priorité à l'historique (compact, signal de récurrence) puis au contexte
    if history is not None:
        hist = render_history(history, lang)
        if len(hist) > remaining:
            hist = render_history(history, lang, max_recent=1)
        if len(hist) <= remaining:
            sections.append(hist)
            remaining -= len(hist)
    if context and (context.get("before") or context.get("between") or context.get("after")):
        n = max(len(context.get("before") or []), len(context.get("after") or []))
        ctx = context
        while ctx and len(_render_context(ctx, n, lang)) > remaining:
            if not (ctx.get("before") or ctx.get("after")):
                ctx = None
                break
            ctx = _shrink_context(ctx)
        if ctx:
            sections.insert(2, _render_context(ctx, n, lang))

    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(sections)}]


def messages_to_text(messages: List[dict]) -> str:
    return "\n\n".join(f"[{m['role'].upper()}]\n{m.get('content') or ''}" for m in messages)


# ── Réponse ────────────────────────────────────────────────────────────────────

def detect_severity(response: str) -> str:
    """Détermine la sévérité depuis une réponse texte libre (repli si la sortie structurée échoue)."""
    for line in response.splitlines()[:10]:
        l = line.upper()
        if "SEVERITY:" in l:
            if "CRITICAL" in l: return "critical"
            if "WARNING" in l: return "warning"
            if "INFO" in l: return "info"
    lower_resp = response.lower()
    if "severity: critical" in lower_resp or "sévérité: critical" in lower_resp:
        return "critical"
    if "severity: warning" in lower_resp or "sévérité: warning" in lower_resp:
        return "warning"
    if "severity: info" in lower_resp or "sévérité: info" in lower_resp:
        return "info"
    if "critical" in lower_resp or "urgent" in lower_resp:
        return "critical"
    elif "warning" in lower_resp or "warn" in lower_resp:
        return "warning"
    return "info"


def _load_json(content: str) -> Optional[dict]:
    try:
        data = json.loads(content)
    except ValueError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def render_alert_markdown(data: dict, lang: str) -> str:
    parts = []
    if data.get("summary"):
        parts.append(f"**{label(lang, 'summary')} :** {data['summary'].strip()}")
    if data.get("probable_cause"):
        parts.append(f"**{label(lang, 'cause')} :** {data['probable_cause'].strip()}")
    if data.get("explanation"):
        parts.append(data["explanation"].strip())
    actions = [a.strip() for a in data.get("actions") or [] if str(a).strip()]
    if actions:
        parts.append(f"**{label(lang, 'actions')} :**\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1)))
    else:
        parts.append(f"**{label(lang, 'actions')} :** {label(lang, 'no_action')}")
    if isinstance(data.get("confidence"), int):
        parts.append(f"*{label(lang, 'confidence')} : {data['confidence']} %*")
    return "\n\n".join(parts)


def parse_alert_response(content: str, lang: str) -> dict:
    """{"markdown", "severity", "summary", "data"} ; repli sur le texte brut si le JSON est inexploitable."""
    data = _load_json(content)
    if not data or not (data.get("summary") or data.get("explanation")):
        return {"markdown": content.strip(), "severity": detect_severity(content), "summary": None, "data": None}
    severity = data.get("severity") if data.get("severity") in ("info", "warning", "critical") else detect_severity(content)
    data["severity"] = severity
    summary = " ".join(str(data.get("summary") or "").split())[:500] or None
    return {"markdown": render_alert_markdown(data, lang), "severity": severity, "summary": summary, "data": data}
