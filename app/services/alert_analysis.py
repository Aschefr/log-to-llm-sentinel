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
from app.services.log_guard import addresses_the_ai, fence
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


_SUMMARY_TAG = re.compile(r"^[-#\s]*(?:(?:\*\*|__)\s*(?:r[ée]sum[ée](?:\s+court)?|summary|tl;?dr)\s*:?\s*(?:\*\*|__)|[*_]*(?:r[ée]sum[ée](?:\s+court)?|summary|tl;?dr)\s*[*_]*\s*:)\s*:?\s*[*_]*\s*", re.IGNORECASE)
_CONFIDENCE_LINE = re.compile(r"^[*_\s]*(confiance|confidence)\b", re.IGNORECASE)


def short_summary(a: Analysis, max_len: int = 300) -> str:
    """Résumé court d'une analyse : champ structuré, sinon dernier paragraphe du texte (anciennes analyses)."""
    text = a.summary
    if not text:
        paras = [p.strip() for p in (a.ollama_response or "").split("\n\n") if p.strip().strip("-")]
        # Paragraphe « Résumé : … » s'il existe (format structuré et anciens prompts), sinon le dernier hors confiance
        tagged = [i for i, p in enumerate(paras) if _SUMMARY_TAG.match(p)]
        rest = [p for p in paras if not _CONFIDENCE_LINE.match(p)]
        text = _SUMMARY_TAG.sub("", paras[tagged[0]]) if tagged else (rest[-1] if rest else "")
        if tagged and not text.strip("*_ ") and tagged[0] + 1 < len(paras):
            text = paras[tagged[0] + 1]  # libellé seul, le résumé est dans le paragraphe suivant
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
    flagged = sum(1 for a in similar if a.feedback == "irrelevant")
    if flagged:
        lines.append(label(lang, "history_flagged", n=flagged))
    hours = Counter(a.analyzed_at.hour for a in similar)
    lines.append(label(lang, "history_hours", hours=", ".join(f"{h:02d}h ×{n}" for h, n in sorted(hours.items()))))
    lines.append(label(lang, "history_recent"))
    for a in similar[:max_recent]:
        if a.resolution_status == ONE_OFF:
            status = label(lang, "resolution_one_off")
        elif a.resolved_at:
            status = label(lang, "resolved_after", duration=_format_duration((a.resolved_at - a.analyzed_at).total_seconds()))
        else:
            status = a.resolution_status or label(lang, "resolution_pending")
        lines.append(f"- {a.analyzed_at.strftime('%Y-%m-%d %H:%M')} UTC — {a.severity} — {status} — « {short_summary(a)} »")
    return "\n".join(lines)


def render_notes(notes: Optional[List[dict]], lang: str, max_chars: Optional[int] = None, intro: str = "notes_intro",
                 with_line: bool = False) -> str:
    """Bloc des notes de l'administrateur, les plus proches de l'événement d'abord. Sur une ligne chacune : une note ne
    peut pas imiter une autre section du prompt. Au-delà de max_chars, les dernières notes sont retirées."""
    if not notes:
        return ""
    items = []
    for n in notes:
        where = label(lang, f"note_scope_{n['scope']}")
        if with_line and n.get("line") and n["scope"] != "source":
            where = label(lang, "note_on_line", line=n["line"][:120])
        items.append(f"- {(n.get('updated_at') or '')[:10]} ({where}) : {' '.join(n['text'].split())}")
    head = f"## {label(lang, 'notes_title')}\n{label(lang, intro)}"
    if max_chars:
        while len(items) > 1 and len(head) + sum(len(i) + 1 for i in items) > max_chars:
            items.pop()
        room = max(0, max_chars - len(head) - 1)
        if len(items[0]) > room:
            items[0] = items[0][:room].rstrip() + "…"
    return "\n".join([head, *items])


# ── Messages ───────────────────────────────────────────────────────────────────

def _render_rule(rule: Rule, now: datetime, lang: str) -> str:
    kind = log_source.source_type(rule.log_file_path)
    if kind == "file":
        source = f"{label(lang, 'source_file')} {rule.log_file_path}"
    elif kind == "syslog":
        source = label(lang, "source_syslog", host=rule.log_file_path.split(":", 1)[1])
    elif kind == "docker":
        source = label(lang, "source_docker", name=log_source.parse_connected(rule.log_file_path)["container"])
    elif kind == "homeassistant":
        source = label(lang, "source_homeassistant")
    elif kind == "mqtt":
        source = label(lang, "source_mqtt", topic=log_source.parse_connected(rule.log_file_path)["topic"])
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
    return f"## {label(lang, 'context', n=n)}\n" + fence("\n".join(body))


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


# En-tête syslog : « date machine programme[pid]: message ». Dates ISO (fichier syslog), à l'ancienne (« Nov 13 16:20:40 »)
# ou ligne reçue par Sentinel (« 2026-11-13 16:20:40  <6>machine programme: », souvent avec une date à l'ancienne
# après <PRI> : « <38>Nov  8 09:03:09 machine programme[pid]: »). Le programme doit être suivi de « : » et
# d'une espace : une ligne CEF (« machine CEF:0|… ») n'est pas lue.
_SYSLOG_HEAD = re.compile(
    r"^(?:\[×\d+\]\s+)?(?:\d{4}-\d{2}-\d{2}T\S+|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+<\d+>(?:[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+)?)"
    r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s+([A-Za-z0-9_.\-/]+)(?:\[\d+\])?:\s")
_DOCKER_DAEMONS = {"dockerd", "containerd"}
# Une ligne d'application datée en ISO (« 2026-11-13T16:20:40Z ERROR main: … ») n'a pas de machine
_LEVELS = {"trace", "debug", "info", "notice", "warn", "warning", "error", "err", "fatal", "critical", "crit"}


def syslog_origin(text: str, lang: str = "fr") -> str:
    """Machine et programme des lignes au format syslog, dits à l'IA : vu en prod, elle prenait le nom de la machine
    (« srv-maison dockerd[1125]: … ») pour un conteneur, et le second regard cherchait ce conteneur en vain."""
    hosts, programs = [], []
    for line in (text or "").splitlines():
        m = _SYSLOG_HEAD.match(line.strip())
        if m and m.group(1).lower() not in _LEVELS:
            host, prog = m.group(1), m.group(2).rsplit("/", 1)[-1]
            if host not in hosts:
                hosts.append(host)
            if prog not in programs:
                programs.append(prog)
    if not hosts:
        return ""
    note = label(lang, "syslog_origin", hosts=", ".join(hosts[:3]), programs=", ".join(programs[:5]))
    daemons = [p for p in programs if p in _DOCKER_DAEMONS]
    if daemons:
        note += " " + label(lang, "syslog_origin_docker", programs=", ".join(daemons))
    return note


def build_alert_messages(rule: Rule, events_text: str, matched_keywords: Optional[list],
                         context: Optional[dict], history: Optional[dict],
                         config: dict, now: datetime, notes: Optional[List[dict]] = None) -> List[dict]:
    lang = config.get("ollama_prompt_lang", "fr")
    system = ALERT_SYSTEM.get(lang, ALERT_SYSTEM["fr"])

    events_head = f"## {label(lang, 'events')}\n"
    if matched_keywords:
        events_head += f"{label(lang, 'keywords')} : {', '.join(matched_keywords)}\n"
    origin = syslog_origin(events_text, lang)
    if origin:
        events_head += origin + "\n"

    # Budget : contexte total moins une réserve pour la réponse (et le raisonnement s'il est actif)
    reserve = 2048 + (2048 if config.get("ollama_think", False) else 0)
    budget = max(3000, int((config.get("ollama_ctx", 4096) - reserve) * CHARS_PER_TOKEN))
    rule_block = _render_rule(rule, now, lang)
    remaining = budget - len(system) - len(rule_block)
    # Les notes passent avant l'historique et le contexte, sans priver l'analyse de ses lignes
    notes_block = render_notes(notes, lang, max_chars=max(800, remaining // 3))
    remaining -= len(notes_block)
    events_room = max(500, remaining - 200 - len(events_head))
    if len(events_text) > events_room:
        events_text = events_text[:events_room] + "…"
    events = events_head + fence(events_text)
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
    if notes_block:
        sections.insert(1, notes_block)

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


ONE_OFF = "one_off"  # statut d'une analyse close dès sa création : événement ponctuel, rien à surveiller


def watch_needed(parsed: dict, lines: List[str]) -> bool:
    """Faut-il surveiller le retour à la normale ? Seule l'analyse peut dire qu'un événement est ponctuel et déjà fini,
    mais elle ne peut pas couper la surveillance d'une panne franche, d'une alerte critical, ni quand une ligne tente
    de dicter le verdict. Sans réponse exploitable de l'IA, on surveille."""
    data = parsed.get("data") or {}
    if parsed.get("failed") or not data or data.get("needs_watch") is not False:
        return True
    # log_guarded : le modèle a signalé une manipulation sur une ligne qui donne un verdict (une panne marquée « Simulation ») ;
    # mesuré le 2026-11-08, qwen3.8:27b classait alors une panne de base de données en ponctuel, info et « simulation »
    if parsed.get("severity") == "critical" or parsed.get("log_instruction") or parsed.get("log_guarded"):
        return True
    return any(is_hard_failure(line) for line in lines if line)


def close_one_off(analysis: Analysis):
    """Clôt une analyse ponctuelle à sa détection, hors du temps de retour à la normale (ce n'était pas une panne)."""
    analysis.resolution_status = ONE_OFF
    analysis.resolved_at = analysis.analyzed_at
    analysis.exclude_from_mttr = True


def guard_log_instruction(parsed: dict, lines: List[str], lang: str) -> dict:
    """Une ligne qui cherche à influencer l'IA ne fait pas baisser la gravité, et la personne qui lit l'alerte en est prévenue.
    log_instruction ne vaut True que pour le détecteur : le signal du modèle se trompe parfois, il ne doit pas forcer de notification."""
    detected = addresses_the_ai(lines)
    flagged = (parsed.get("data") or {}).get("log_manipulation") is True and model_flag_counts(lines)
    if not (detected or flagged):
        return {**parsed, "log_instruction": False, "log_guarded": False}
    severity = "warning" if parsed["severity"] == "info" else parsed["severity"]
    data = {**parsed["data"], "severity": severity} if parsed.get("data") else parsed.get("data")
    notice = label(lang, "log_instruction")
    markdown, summary = parsed["markdown"], parsed.get("summary")
    if not (summary or markdown).startswith(notice):
        markdown = f"**{notice}**\n\n{markdown}"
        summary = f"{notice} {summary}"[:500] if summary else summary
    return {**parsed, "severity": severity, "data": data, "markdown": markdown, "summary": summary, "log_instruction": detected,
            "log_guarded": True}   # gravité tenue par ce garde-fou : l'événement reste aussi surveillé (watch_needed)


# Pannes franches qui ne descendent jamais à info, quel que soit le modèle : la liste du pilote automatique,
# plus les boucles de redémarrage de systemd (mesuré : « restart counter is at 36 » noté info par gemma4:12b).
_RESTART_LOOP = re.compile(r"restart counter is at (?:[5-9]|\d{2,})\b|start request repeated too quickly", re.I)


def is_hard_failure(line: str) -> bool:
    from app.services.autopilot import serious_line   # import tardif : autopilot importe ce module
    return serious_line(line) or bool(_RESTART_LOOP.search(line))


# Ligne d'un équipement de sécurité qui dit avoir déjà bloqué la connexion (CEF act=blocked, dropped, denied…)
BLOCKED_LINE = re.compile(r"\bact=(?:blocked?|drop(?:ped)?|den(?:y|ied)|reject(?:ed)?)\b", re.IGNORECASE)


def is_signal(rule: Rule, lines: List[str], matched_keywords: Optional[List[str]] = None) -> bool:
    """Signal ponctuel : une trace à garder, pas une alerte. Toutes les lignes ont déjà été bloquées par l'équipement de
    sécurité, ou ne sont attrapées que par des mots-clés marqués « signal ponctuel ». Une panne franche n'en est jamais un."""
    lines = [l for l in lines if l]
    if not lines or any(is_hard_failure(l) for l in lines):
        return False
    if all(BLOCKED_LINE.search(l) for l in lines):
        return True
    signals = {k.lower() for k in rule.get_signal_keywords()}
    matched = [k for k in (matched_keywords or []) if k]
    return bool(signals and matched) and all(k.lower() in signals for k in matched)


def as_signal(result: dict) -> dict:
    """Verdict d'un signal ponctuel : info, clos à la détection, sans notification. Une ligne qui tente de dicter le
    verdict, ou une analyse que l'IA n'a pas pu faire, restent traitées comme une alerte."""
    if result.get("failed") or result.get("log_instruction"):
        return result
    data = {**result["data"], "severity": "info"} if result.get("data") else result.get("data")
    return {**result, "severity": "info", "data": data, "signal": True}


def hard_failure_floor(parsed: dict, lines: List[str], lang: str) -> dict:
    """Une panne franche dans les lignes de l'alerte reste au moins warning, quoi que dise le modèle."""
    if parsed["severity"] != "info" or not any(is_hard_failure(l) for l in lines):
        return parsed
    data = {**parsed["data"], "severity": "warning"} if parsed.get("data") else parsed.get("data")
    markdown = (parsed.get("markdown") or "") + f"\n\n*{label(lang, 'severity_floor')}*"
    return {**parsed, "severity": "warning", "data": data, "markdown": markdown}


# Mots d'une ligne qui donnerait un verdict (test, simulation, normal, info…). Le signal « manipulation » du modèle ne compte
# que si une ligne en contient un : mesuré le 2026-11-08, gemma4:12b le cochait sur 14 lignes normales sur 30 (session cron,
# GET /time), ce qui les remontait à warning ; aucune de ces lignes ne contient un tel mot, et qwen3.8:27b ne perd rien.
_VERDICT_WORDS = re.compile(
    r"\b(?:tests?|testing|simulat\w*|normal\w*|benign|b[ée]nin\w*|harmless|inoffensi\w*|ignor\w*|info|informational|"
    r"maintenance|valid\w*|approved|resolved|r[ée]solu\w*|no action|aucune action|expected|attendu\w*|classif\w*|"
    r"severity|s[ée]v[ée]rit[ée])\b", re.I)


def model_flag_counts(lines: List[str]) -> bool:
    return any(line and _VERDICT_WORDS.search(line) for line in lines)


def parse_alert_response(content: str, lang: str) -> dict:
    """{"markdown", "severity", "summary", "data"} ; repli sur le texte brut si le JSON est inexploitable."""
    data = _load_json(content)
    if not data or not (data.get("summary") or data.get("explanation")):
        return {"markdown": content.strip(), "severity": detect_severity(content), "summary": None, "data": None}
    severity = data.get("severity") if data.get("severity") in ("info", "warning", "critical") else detect_severity(content)
    data["severity"] = severity
    summary = " ".join(str(data.get("summary") or "").split())[:500] or None
    return {"markdown": render_alert_markdown(data, lang), "severity": severity, "summary": summary, "data": data}
