"""
Indicateurs des règles pour l'interface : vue d'ensemble, page d'une règle, onglet Résolution.
"""
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.models import Analysis, ResolutionVerdict, Rule
from app.services.alert_analysis import analysis_lines, short_summary
from app.utils.log_utils import line_signature

SEVERITIES = ("critical", "warning", "info")
ALL_DAY_HOURS = 8  # au-delà de N heures distinctes, un événement est « toute la journée »
_GROUP_PREFIX = re.compile(r"^\[×\d+\]\s*")  # préfixe des lignes regroupées (ancien format)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def source_info(log_file_path: str) -> dict:
    path = log_file_path or ""
    if path.startswith("[WEBHOOK]"):
        return {"type": "webhook", "label": ""}
    if path.startswith("[SYSLOG]"):
        return {"type": "syslog", "label": path.split(":", 1)[1] if ":" in path else "*"}
    return {"type": "file", "label": path}


def resolution_minutes(a: Analysis) -> Optional[float]:
    if a.resolved_at and a.analyzed_at and not a.exclude_from_mttr and a.resolution_status == "resolved":
        return max(0.0, (a.resolved_at - a.analyzed_at).total_seconds() / 60)
    return None


def median_mttr(analyses: List[Analysis]) -> Optional[int]:
    values = [m for m in (resolution_minutes(a) for a in analyses) if m is not None]
    return round(statistics.median(values)) if values else None


def daily_counts(analyses: List[Analysis], days: int, now: datetime) -> List[dict]:
    """Alertes par jour et par sévérité, du plus ancien au plus récent (aujourd'hui inclus)."""
    start = (now - timedelta(days=days - 1)).date()
    buckets = {start + timedelta(days=i): Counter() for i in range(days)}
    for a in analyses:
        if a.analyzed_at and a.analyzed_at.date() in buckets:
            buckets[a.analyzed_at.date()][a.severity if a.severity in SEVERITIES else "info"] += 1
    return [{"date": d.isoformat(), **{s: c[s] for s in SEVERITIES}} for d, c in buckets.items()]


def analysis_brief(a: Analysis) -> dict:
    return {
        "id": a.id,
        "detection_id": a.detection_id,
        "analyzed_at": iso(a.analyzed_at),
        "severity": a.severity or "info",
        "summary": short_summary(a, 220),
        "status": "resolved" if a.resolution_status == "resolved" else "open",
        "resolution_minutes": resolution_minutes(a),
    }


def recurrence_counts(db, analyses: List[Analysis], days: int = 7) -> Dict[int, int]:
    """Pour chaque analyse : nombre d'analyses de la même règle, dans les `days` jours précédents,
    qui partagent au moins un événement (signature de ligne)."""
    if not analyses:
        return {}
    result = {}
    by_rule = defaultdict(list)
    for a in analyses:
        if a.analyzed_at:
            by_rule[a.rule_id].append(a)
    for rule_id, items in by_rule.items():
        lo = min(a.analyzed_at for a in items) - timedelta(days=days)
        hi = max(a.analyzed_at for a in items)
        pool = db.query(Analysis).filter(Analysis.rule_id == rule_id, Analysis.analyzed_at >= lo,
                                         Analysis.analyzed_at <= hi).all()
        sigs = {p.id: {line_signature(l) for l in analysis_lines(p)} for p in pool}
        for a in items:
            mine = sigs.get(a.id) or {line_signature(l) for l in analysis_lines(a)}
            window = a.analyzed_at - timedelta(days=days)
            result[a.id] = sum(1 for p in pool if p.id != a.id and window <= p.analyzed_at < a.analyzed_at
                               and mine & sigs.get(p.id, set()))
    return result


def recurring_events(analyses: List[Analysis], limit: int = 5) -> List[dict]:
    """Événements les plus fréquents (lignes identiques aux valeurs variables près)."""
    groups = {}
    for a in analyses:
        lines = [_GROUP_PREFIX.sub("", l) for l in analysis_lines(a)]
        for sig, n in Counter(line_signature(l) for l in lines).items():
            g = groups.setdefault(sig, {"signature": sig, "count": 0, "alerts": 0, "last_at": None,
                                        "hours": Counter(), "durations": []})
            g["count"] += n
            g["alerts"] += 1
            if a.analyzed_at:
                g["hours"][a.analyzed_at.hour] += 1
                if not g["last_at"] or a.analyzed_at > g["last_at"]:
                    g["last_at"] = a.analyzed_at
            m = resolution_minutes(a)
            if m is not None:
                g["durations"].append(m)
    top = sorted(groups.values(), key=lambda g: (g["count"], g["alerts"]), reverse=True)[:limit]
    return [{
        "signature": g["signature"],
        "count": g["count"],
        "alerts": g["alerts"],
        "last_at": iso(g["last_at"]),
        "hours": sorted(h for h, _ in g["hours"].most_common(3)),
        "all_day": len(g["hours"]) >= ALL_DAY_HOURS,
        "mttr_minutes": round(statistics.median(g["durations"])) if g["durations"] else None,
    } for g in top]


def is_muted(rule: Rule, now: datetime) -> bool:
    if not rule.enabled or not rule.inactivity_period_hours:
        return False
    if not rule.last_line_received_at:
        return True
    return now - rule.last_line_received_at > timedelta(hours=rule.inactivity_period_hours)


def pattern_rows(rule: Rule, verdicts: List[ResolutionVerdict]) -> List[dict]:
    """Motifs de résolution avec leur origine et leurs déclenchements acceptés / refusés."""
    hits = defaultdict(lambda: {"ok": 0, "ko": 0})
    for v in verdicts:
        for p in json.loads(v.resolution_patterns_json or "[]"):
            key = p.lower()
            if v.outcome in ("accepted", "accepted_no_ai"):
                hits[key]["ok"] += 1
            elif v.outcome in ("rejected_ai", "rejected_low_confidence", "false_positive_user"):
                hits[key]["ko"] += 1
    rows = []
    for item in rule.get_weighted_resolution_patterns():
        pat = item.get("pattern")
        if not pat:
            continue
        status = item.get("status", "active")
        if status == "rejected":
            continue
        rows.append({
            "pattern": pat,
            "status": status,
            # Anciennes entrées sans origine : un motif appris porte une date de validation, un motif manuel non
            "origin": item.get("origin") or ("learned" if item.get("last_validated_at") else "manual"),
            "weight": item.get("weight", 1),
            "last_validated_at": item.get("last_validated_at"),
            "hits_ok": hits[pat.lower()]["ok"],
            "hits_ko": hits[pat.lower()]["ko"],
        })
    return rows
