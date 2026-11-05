"""
Fiche d'un CVE tirée de la base officielle NVD : description, score, exploitation active (catalogue KEV de la CISA),
versions touchées. Sentinel repère lui-même l'identifiant dans la ligne : seul ce numéro sort de chez toi.

Une ligne de log peut citer n'importe quel CVE, un attaquant aussi : la fiche informe et ne change jamais la gravité.
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

import httpx

from app import logger
from app.models import CveEntry, GlobalConfig

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
TIMEOUT_S = 20.0
FOUND_TTL = timedelta(days=7)
MISSING_TTL = timedelta(days=1)   # un CVE tout récent peut arriver dans la base
MIN_INTERVAL_S = 6.0              # NVD sans clé : 5 requêtes par 30 s
MAX_PER_ANALYSIS = 3
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)

_lock = threading.Lock()
_last_call = 0.0


def cves_in(text: str) -> List[str]:
    seen = []
    for m in CVE_RE.findall(text or ""):
        cve = m.upper()
        if cve not in seen:
            seen.append(cve)
    return seen


def enabled(db) -> bool:
    config = db.query(GlobalConfig).first()
    return bool(config and config.cve_lookup_enabled)


def _bounds(match: dict) -> str:
    parts = []
    for key, op in (("versionStartIncluding", ">="), ("versionStartExcluding", ">"), ("versionEndExcluding", "<"), ("versionEndIncluding", "<=")):
        if match.get(key):
            parts.append(f"{op} {match[key]}")
    return " ".join(parts)


def parse_nvd(cve: str, payload: dict) -> dict:
    """Ce qui sert dans la réponse de NVD. Une entrée absente donne found=False."""
    vulns = payload.get("vulnerabilities") or []
    if not vulns:
        return {"id": cve, "found": False}
    c = vulns[0].get("cve") or {}
    description = next((d.get("value") for d in c.get("descriptions", []) if d.get("lang") == "en"), "") or ""
    score, severity = None, None
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        metrics = (c.get("metrics") or {}).get(key)
        if metrics:
            data = metrics[0].get("cvssData") or {}
            score, severity = data.get("baseScore"), data.get("baseSeverity") or metrics[0].get("baseSeverity")
            break
    products = []
    for conf in c.get("configurations") or []:
        for node in conf.get("nodes") or []:
            for m in node.get("cpeMatch") or []:
                if not m.get("vulnerable"):
                    continue
                parts = (m.get("criteria") or "").split(":")
                name = " ".join(parts[3:5]).replace("_", " ") if len(parts) > 4 else "?"
                entry = f"{name} {_bounds(m)}".strip()
                if entry not in products:
                    products.append(entry)
    return {"id": cve, "found": True, "status": c.get("vulnStatus"), "description": re.sub(r"\s+", " ", description)[:700],
            "score": score, "severity": severity, "kev_added": c.get("cisaExploitAdd"), "kev_due": c.get("cisaActionDue"),
            "products": products[:6], "url": f"https://nvd.nist.gov/vuln/detail/{cve}"}


def _fetch(cve: str) -> dict:
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    with httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": "log-to-llm-sentinel"}) as client:
        resp = client.get(NVD_URL, params={"cveId": cve})
    if resp.status_code == 404:
        return {"id": cve, "found": False}
    resp.raise_for_status()
    return parse_nvd(cve, resp.json())


def lookup(db, cve: str) -> dict:
    """Fiche d'un CVE, depuis le cache si elle est récente. NVD injoignable : l'ancienne fiche, sinon {"error": ...}."""
    cve = (cve or "").strip().upper()
    if not CVE_RE.fullmatch(cve):
        return {"id": cve, "error": "invalid"}
    row = db.query(CveEntry).filter(CveEntry.cve_id == cve).first()
    now = datetime.utcnow()
    if row:
        data = json.loads(row.data_json)
        if now - row.fetched_at < (FOUND_TTL if data.get("found") else MISSING_TTL):
            return data
    try:
        data = _fetch(cve)
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("CveLookup", f"NVD indisponible pour {cve} : {type(e).__name__}")
        return json.loads(row.data_json) if row else {"id": cve, "error": "unavailable"}
    if row:
        row.data_json, row.fetched_at = json.dumps(data, ensure_ascii=False), now
    else:
        db.add(CveEntry(cve_id=cve, data_json=json.dumps(data, ensure_ascii=False), fetched_at=now))
    db.commit()
    return data


def sheet_text(data: dict, lang: str = "fr") -> str:
    """Fiche pour le LLM. Elle dit ce qu'on ignore plutôt que de laisser deviner."""
    fr = lang != "en"
    cve = data.get("id")
    if data.get("error") == "invalid":
        return "Identifiant CVE invalide, attendu : CVE-AAAA-NNNNN." if fr else "Invalid CVE identifier, expected: CVE-YYYY-NNNNN."
    if data.get("error"):
        return (f"Base NVD indisponible pour {cve} : dis-le à l'utilisateur et n'invente ni version, ni score, ni date."
                if fr else f"NVD unavailable for {cve}: tell the user and do not invent a version, a score or a date.")
    if not data.get("found"):
        return (f"{cve} : absent de la base NVD. Il peut être trop récent ou ne pas exister : la ligne de log ne le prouve pas."
                if fr else f"{cve}: not in NVD. It may be too recent or not exist: the log line does not prove it.")
    score = f"{data.get('score')} ({data.get('severity') or 'n/a'})" if data.get("score") is not None else ("inconnu" if fr else "unknown")
    if data.get("kev_added"):
        due = f". Échéance {data['kev_due']}" if fr and data.get("kev_due") else (f". Due {data['kev_due']}" if data.get("kev_due") else "")
        exploit = (f"Exploitation active CONFIRMÉE : au catalogue KEV de la CISA depuis {data['kev_added']}{due}."
                   if fr else f"Active exploitation CONFIRMED: in CISA's KEV catalog since {data['kev_added']}{due}.")
    else:
        exploit = ("Exploitation : absent du catalogue KEV de la CISA, ce qui ne prouve pas qu'il n'est pas exploité."
                   if fr else "Exploitation: not in CISA's KEV catalog, which does not prove it is not exploited.")
    products = "; ".join(data.get("products") or []) or ("non précisées par NVD" if fr else "not specified by NVD")
    return "\n".join([
        f"{cve} (source officielle : {data.get('url')}, statut {data.get('status')})" if fr else f"{cve} (official source: {data.get('url')}, status {data.get('status')})",
        f"Description : {data.get('description')}" if fr else f"Description: {data.get('description')}",
        (f"Score CVSS : {score}. Un score moyen n'exclut pas un risque réel : regarde l'exploitation." if fr
         else f"CVSS score: {score}. A medium score does not rule out real risk: look at exploitation."),
        exploit,
        f"Versions touchées : {products}" if fr else f"Affected versions: {products}",
    ])


def sheets_for_text(db, text: str) -> List[dict]:
    return [lookup(db, cve) for cve in cves_in(text)[:MAX_PER_ANALYSIS]]


def first_cve(text: Optional[str]) -> Optional[str]:
    found = cves_in(text or "")
    return found[0] if found else None
