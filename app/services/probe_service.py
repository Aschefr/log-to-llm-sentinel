"""
Sonde de santé d'une règle : preuve positive, indépendante des logs, qu'un service répond.
Les cibles sont définies par l'utilisateur dans les réglages de la règle (jamais par le LLM).
"""
import asyncio
import time
from datetime import datetime

import httpx

PROBE_TIMEOUT_S = 5.0
PROBE_TYPES = ("http", "tcp")


def _expected_codes(expect: str) -> set:
    codes = set()
    for part in (expect or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            codes.add(int(part))
    return codes


def _split_host_port(target: str):
    host, _, port = (target or "").strip().rpartition(":")
    if not host or not port.isdigit():
        raise ValueError("hôte:port attendu")
    return host.strip("[]"), int(port)


async def run_probe(probe_type: str, target: str, expect: str = "") -> dict:
    """Exécute la sonde. Retourne {"ok", "detail", "latency_ms", "at"} ; ne lève jamais."""
    start = time.monotonic()
    ok, detail = False, ""
    try:
        if probe_type == "http":
            url = target.strip()
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
            # Certificats auto-signés fréquents sur un réseau local : on teste la disponibilité, pas le certificat
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, verify=False, follow_redirects=True) as client:
                resp = await client.get(url)
            codes = _expected_codes(expect)
            ok = resp.status_code in codes if codes else 200 <= resp.status_code < 400
            detail = f"HTTP {resp.status_code}"
        elif probe_type == "tcp":
            host, port = _split_host_port(target)
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT_S)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            ok, detail = True, "TCP OK"
        else:
            detail = "type de sonde inconnu"
    except asyncio.TimeoutError:
        detail = f"délai dépassé ({PROBE_TIMEOUT_S:.0f} s)"
    except Exception as e:
        detail = (str(e) or type(e).__name__)[:120]
    return {"ok": ok, "detail": detail, "latency_ms": int((time.monotonic() - start) * 1000), "at": datetime.utcnow()}
