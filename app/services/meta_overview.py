"""
Vue long terme d'une méta-analyse, calculée sans IA : l'activité de chaque règle dans le temps, et ce qui change d'une
période à l'autre. Elle ne dit que ce que les données permettent de dire : sans historique, pas de comparaison.
"""
import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from app.models import Analysis, MetaAnalysisConfig, Rule
from app.services.alert_analysis import short_summary
from app.services.rule_stats import SEVERITIES, iso

DAILY_MAX_DAYS = 31
WEEK = 7
EVENTS_MAX = 100
SUMMARY_CHARS = 220
# Une répétition n'aggrave rien tant qu'elle reste rare : il faut doubler et gagner au moins ce nombre d'occurrences
WORSE_MIN_GAIN = 3
_RANK = {"info": 0, "warning": 1, "critical": 2}
_SEVERITY_OF = {rank: name for name, rank in _RANK.items()}
_KIND_ORDER = {"worse": 0, "new": 1, "returning": 2, "unknown": 2, "gone": 3}


def _effective_severity(severity: Optional[str], feedback: Optional[str]) -> str:
    """Comme sur le tableau de bord : signalée « pas pertinente », une alerte non critique ne pèse plus que comme une info."""
    value = severity if severity in SEVERITIES else "info"
    return "info" if feedback == "irrelevant" and value != "critical" else value


def _rule_ids(config: MetaAnalysisConfig) -> list:
    try:
        return json.loads(config.rule_ids_json) if config.rule_ids_json else []
    except ValueError:
        return []


def _new_event(rule_id: int, key: str) -> dict:
    return {"rule_id": rule_id, "key": key, "count": 0, "previous": 0, "rank": 0, "previous_rank": -1, "flagged": False,
            "first_at": None, "last_at": None, "latest_id": None, "previous_at": None, "previous_id": None}


def _kind(event: dict, first_seen: Optional[datetime], start: datetime) -> str:
    if not event["count"]:
        return "gone"
    if first_seen is not None and first_seen >= start:
        return "new"
    if event["previous"] and not event["flagged"]:
        more_serious = event["rank"] > event["previous_rank"]
        more_often = event["count"] >= 2 * event["previous"] and event["count"] - event["previous"] >= WORSE_MIN_GAIN
        if more_serious or more_often:
            return "worse"
    return "returning"


def _still_heard(rule: Rule, start: datetime) -> bool:
    """« Plus vu » n'a de sens que si la règle tourne et que sa source parle encore : sinon, on ne sait pas."""
    return bool(rule.enabled and rule.last_line_received_at and rule.last_line_received_at >= start)


def _event_view(event: dict, rule: Rule, latest: Optional[Analysis]) -> dict:
    rank = event["rank"] if event["count"] else max(event["previous_rank"], 0)
    return {
        "kind": event["kind"],
        "rule_id": event["rule_id"],
        "rule_name": rule.name,
        "severity": _SEVERITY_OF[rank],
        "count": event["count"],
        "previous_count": event["previous"],
        "first_at": iso(event["first_at"]),
        "last_at": iso(event["last_at"] or event["previous_at"]),
        "summary": short_summary(latest, SUMMARY_CHARS) if latest else "",
        "analysis_id": latest.id if latest else None,
        "flagged": event["flagged"],
    }


def build(db, config: MetaAnalysisConfig, days: int = 30, tz_minutes: int = 0, now: Optional[datetime] = None) -> dict:
    """tz_minutes : décalage du navigateur (getTimezoneOffset), pour que les jours soient ceux de l'utilisateur."""
    now = now or datetime.utcnow()
    offset = timedelta(minutes=tz_minutes)
    bucket_days = 1 if days <= DAILY_MAX_DAYS else WEEK
    bucket_count = days if bucket_days == 1 else -(-days // WEEK)
    today = (now - offset).date()
    first_day = today - timedelta(days=bucket_count * bucket_days - 1)
    start = datetime.combine(first_day, datetime.min.time()) + offset
    previous_start = start - (now - start)

    chosen = _rule_ids(config)
    all_rules = {r.id: r for r in db.query(Rule).all()}
    rules = {i: r for i, r in all_rules.items() if (i in chosen if chosen else True)}

    rows = db.query(Analysis.id, Analysis.rule_id, Analysis.event_key, Analysis.severity, Analysis.feedback, Analysis.analyzed_at) \
        .filter(Analysis.rule_id.in_(rules), Analysis.analyzed_at >= previous_start, Analysis.analyzed_at <= now) \
        .order_by(Analysis.analyzed_at, Analysis.id).all()
    first_seen = {(rule_id, key): at for rule_id, key, at in
                  db.query(Analysis.rule_id, Analysis.event_key, func.min(Analysis.analyzed_at))
                  .filter(Analysis.rule_id.in_(rules)).group_by(Analysis.rule_id, Analysis.event_key).all()}
    history_since = min(first_seen.values(), default=None)

    cells = {i: {} for i in rules}
    totals = {i: Counter() for i in rules}
    events = {}
    previous_alerts = 0
    for row in rows:
        key = (row.rule_id, row.event_key or f"#{row.id}")
        event = events.setdefault(key, _new_event(*key))
        severity = _effective_severity(row.severity, row.feedback)
        rank = _RANK[severity]
        event["flagged"] = event["flagged"] or row.feedback == "irrelevant"
        if row.analyzed_at < start:
            previous_alerts += 1
            event["previous"] += 1
            event["previous_rank"] = max(event["previous_rank"], rank)
            event["previous_at"], event["previous_id"] = row.analyzed_at, row.id
            continue
        index = min(((row.analyzed_at - offset).date() - first_day).days // bucket_days, bucket_count - 1)
        cells[row.rule_id].setdefault(index, Counter())[severity] += 1
        totals[row.rule_id][severity] += 1
        event["count"] += 1
        event["rank"] = max(event["rank"], rank)
        event["first_at"] = event["first_at"] or row.analyzed_at
        event["last_at"], event["latest_id"] = row.analyzed_at, row.id

    # Sans alerte plus ancienne que la période, tout paraîtrait « nouveau » : on ne compare pas
    comparable = history_since is not None and history_since < start
    listed = []
    counts = Counter()
    for (rule_id, key), event in events.items():
        kind = _kind(event, first_seen.get((rule_id, key)), start) if comparable else ("unknown" if event["count"] else None)
        if kind is None or (kind == "gone" and not _still_heard(rules[rule_id], start)):
            continue
        event["kind"] = kind
        counts[kind] += 1
        listed.append(event)
    listed.sort(key=lambda e: (_KIND_ORDER[e["kind"]], -e["rank"], -e["count"]))
    listed = listed[:EVENTS_MAX]

    latest_ids = [e["latest_id"] or e["previous_id"] for e in listed]
    details = {a.id: a for a in db.query(Analysis).filter(Analysis.id.in_(latest_ids)).all()} if latest_ids else {}

    per_rule = [{
        "id": r.id,
        "name": r.name,
        "enabled": bool(r.enabled),
        "total": sum(totals[r.id].values()),
        **{s: totals[r.id][s] for s in SEVERITIES},
        "events": sum(1 for e in events.values() if e["rule_id"] == r.id and e["count"]),
        "cells": [{"i": i, **{s: c[s] for s in SEVERITIES}} for i, c in sorted(cells[r.id].items())],
    } for r in rules.values()]
    per_rule.sort(key=lambda r: (-r["critical"], -r["warning"], -r["total"], r["name"].lower()))

    grand = Counter()
    for t in totals.values():
        grand.update(t)
    return {
        "days": days,
        "start": iso(start),
        "end": iso(now),
        "bucket_days": bucket_days,
        "buckets": [(first_day + timedelta(days=i * bucket_days)).isoformat() for i in range(bucket_count)],
        "rules": per_rule,
        "missing_rules": sum(1 for i in chosen if i not in all_rules),
        "totals": {
            "alerts": sum(grand.values()),
            "events": sum(1 for e in events.values() if e["count"]),
            **{s: grand[s] for s in SEVERITIES},
            "previous_alerts": previous_alerts if comparable else None,
        },
        "comparable": comparable,
        # Une partie de la période d'avant n'existe plus (purge, règle récente) : la comparaison reste partielle
        "partial": bool(comparable and history_since > previous_start),
        "history_since": iso(history_since),
        "counts": {k: counts[k] for k in ("new", "worse", "returning", "gone")} if comparable else None,
        "events_total": sum(counts.values()),
        "events": [_event_view(e, rules[e["rule_id"]], details.get(e["latest_id"] or e["previous_id"])) for e in listed],
    }
