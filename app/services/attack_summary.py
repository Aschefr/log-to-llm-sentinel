"""
Tentatives bloquées par un pare-feu, regroupées sans IA à partir des lignes CEF d'une source (UniFi et autres).
Une alerte isolée dit peu ; « 15 flux de 15 sources vers le port 32400 » dit ce qui est exposé.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from app.models import Rule
from app.services import log_source
from app.utils import keyword_match

MAX_BYTES = 5 * 1024 * 1024
MAX_PORTS = 8
_FIELD = r"(?:^|[\s|]){}=(.*?)(?=\s+[A-Za-z_][\w.-]*=|$)"
_TS = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)")
_CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)


def field(line: str, name: str) -> str:
    m = re.search(_FIELD.format(re.escape(name)), line)
    return m.group(1).strip() if m else ""


def _family(signature: str) -> str:
    """Famille d'une signature Emerging Threats : « ET DROP Dshield… » donne DROP."""
    parts = signature.split()
    return parts[1] if len(parts) > 1 and parts[0] in ("ET", "GPL", "ETPRO") else ""


def summary(rule: Rule, days: int = 7, now: Optional[datetime] = None) -> Optional[dict]:
    """Regroupement par port de destination et sens. None si la source n'a pas de telles lignes."""
    path = log_source.resolve_path(rule.log_file_path)
    lines = log_source.read_tail_lines(path, max_bytes=MAX_BYTES) if path else []
    keywords, excluded = rule.get_keywords(), rule.get_excluded_patterns()
    since = (now or datetime.utcnow()) - timedelta(days=days)
    flows = defaultdict(lambda: {"flows": 0, "sources": set(), "signatures": Counter()})
    families, cves, total = Counter(), Counter(), 0
    for line in lines:
        if "CEF:" not in line or keyword_match.classify(line, keywords, excluded)[0] != "matched":
            continue
        port, source = field(line, "dpt"), field(line, "src")
        if not port or not source:
            continue
        m = _TS.match(line)
        if m:
            try:
                if datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}") < since:
                    continue
            except ValueError:
                pass
        total += 1
        signature = field(line, "UNIFIipsSignature")
        group = flows[(port, field(line, "UNIFIdirection"))]
        group["flows"] += 1
        group["sources"].add(source)
        if signature:
            group["signatures"][signature] += 1
            if _family(signature):
                families[_family(signature)] += 1
        cves.update(c.upper() for c in _CVE.findall(signature or line))
    if not total:
        return None
    ports = sorted(flows.items(), key=lambda kv: kv[1]["flows"], reverse=True)
    return {"days": days, "flows": total,
            "ports": [{"port": port, "direction": direction, "flows": g["flows"], "sources": len(g["sources"]),
                       "signature": g["signatures"].most_common(1)[0][0] if g["signatures"] else ""}
                      for (port, direction), g in ports[:MAX_PORTS]],
            "families": families.most_common(6), "cves": [c for c, _ in cves.most_common(6)]}


def summary_text(data: Optional[dict], lang: str = "fr") -> str:
    fr = lang != "en"
    if not data:
        return ("Aucune tentative bloquée au format CEF dans cette source." if fr else "No blocked attempt in CEF format in this source.")
    head = (f"{data['flows']} tentatives bloquées sur {data['days']} jours." if fr else f"{data['flows']} blocked attempts over {data['days']} days.")
    rows = [(f"- port {p['port']} ({p['direction'] or '?'}) : {p['flows']} flux, {p['sources']} sources{' — ' + p['signature'] if p['signature'] else ''}"
             if fr else f"- port {p['port']} ({p['direction'] or '?'}): {p['flows']} flows, {p['sources']} sources{' — ' + p['signature'] if p['signature'] else ''}")
            for p in data["ports"]]
    return "\n".join([head] + rows + ([("CVE cités : " if fr else "CVEs cited: ") + ", ".join(data["cves"])] if data["cves"] else []))
