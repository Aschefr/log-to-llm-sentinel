"""
Second regard : après l'analyse automatique d'une alerte, l'IA vérifie l'état réel des machines de la règle
(conteneurs, services, montages, journal) avec au plus 3 contrôles en lecture seule, avant que l'alerte ne soit
enregistrée et notifiée. Elle confirme, relève ou, si l'option est activée, baisse la gravité.

Garde-fous :
- seulement les outils de diagnostic en lecture seule, et seulement sur les connexions de la règle ;
- pas de second regard quand une ligne de l'alerte s'adresse à l'IA : c'est elle qui choisirait les contrôles ;
- les sorties d'outils sont encadrées comme des lignes de log (les logs d'un conteneur peuvent venir d'un attaquant) ;
- par défaut la gravité ne peut que monter ; même avec l'option, jamais quand une sortie d'outil s'adresse à l'IA ;
- le constat est borné et passé au masque des secrets avant d'aller dans l'analyse et la notification.
"""
import asyncio
import json
import re
from datetime import datetime
from typing import Optional

from app import logger
from app.services import diagnostics as diag
from app.services import llm_tools
from app.services.alert_analysis import _load_json
from app.services.app_config import llm_options
from app.services.log_guard import addresses_the_ai, fence
from app.services.prompts import SECOND_LOOK_SCHEMA, SECOND_LOOK_SYSTEM, label, second_look_label

MAX_CALLS = 3          # contrôles au plus
MAX_ROUNDS = 3         # tours de génération avec outils ; un tour final sans outils, au format JSON, suit
TIMEOUT_S = 300.0      # pour tout le second regard ; au-delà, l'alerte garde la première analyse
TOOL_CHARS = 2500      # par sortie d'outil envoyée au modèle
KEPT_CHARS = 1500      # extrait de chaque sortie gardé avec l'analyse, pour la carte
FINDINGS_CHARS = 300
LINES_CHARS = 3000
TOOL_NAMES = ("docker_list_containers", "docker_container_status", "docker_container_logs", "host_check")
RANK = {"info": 0, "warning": 1, "critical": 2}

# Ce qui ressemble à un secret ne sort pas du second regard (constat, carte, notification)
_SECRET = re.compile(
    r"(?i)\b((?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|auth|credentials?)\s*[=:]\s*)\S+"
    r"|\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)


def scrub(text: str) -> str:
    def _mask(m):
        if m.group(1):
            return m.group(1) + "***"
        if m.group(2):
            return m.group(2) + " ***"
        return "[clé privée masquée]"
    return _SECRET.sub(_mask, text or "")


def settings(config: dict) -> dict:
    return {"enabled": bool(config.get("second_look_enabled")), "include_info": bool(config.get("second_look_include_info")),
            "may_lower": bool(config.get("second_look_may_lower"))}


def tools_for_rule(db, rule) -> list:
    """Outils de diagnostic en lecture seule, pour les connexions Docker et SSH de cette règle."""
    docker = [t for t in llm_tools.DOCKER_TOOLS if t["function"]["name"] in TOOL_NAMES]
    return (llm_tools._for_links(docker, diag.links_for_rule(db, rule, "docker"))
            + llm_tools._for_links(llm_tools.HOST_TOOLS, diag.links_for_rule(db, rule, "ssh")))


def skip_reason(config: dict, result: dict) -> Optional[str]:
    """Pourquoi cette analyse n'a pas de second regard, ou None. Seul « suspicious » est montré sur la carte."""
    s = settings(config)
    if not s["enabled"]:
        return "disabled"
    if result.get("failed"):
        return "failed"
    if result.get("severity") == "info" and not s["include_info"]:
        return "scope"
    if result.get("log_instruction") or (result.get("data") or {}).get("log_manipulation") is True:
        return "suspicious"
    return None


def merge_severity(before: str, proposed: str, verdict: str, may_lower: bool, tool_addresses_ai: bool) -> str:
    """Par défaut le second regard ne peut que relever la gravité ; baisser demande l'option, un verdict « better »
    et aucune sortie d'outil qui s'adresse à l'IA."""
    before = before if before in RANK else "warning"
    proposed = proposed if proposed in RANK else before
    if verdict == "worse" and RANK[proposed] <= RANK[before]:
        # « Plus grave que l'analyse » : au moins un cran (mesuré : un modèle répondait worse en gardant warning)
        proposed = [s for s, r in RANK.items() if r == min(RANK[before] + 1, 2)][0]
    if RANK[proposed] > RANK[before]:
        return proposed
    if RANK[proposed] < RANK[before] and may_lower and verdict == "better" and not tool_addresses_ai:
        return proposed
    return before


def _task(rule, result: dict, links: list, lang: str) -> str:
    data = result.get("data") or {}
    lines = (result.get("events_text") or "")[-LINES_CHARS:]
    context = f" ({rule.application_context.strip()[:300]})" if (rule.application_context or "").strip() else ""
    return second_look_label(
        lang, "task", rule=rule.name, context=context, severity=result.get("severity"),
        summary=result.get("summary") or "", cause=data.get("probable_cause") or second_look_label(lang, "unknown"),
        lines=fence(lines), links=", ".join(f"{l['name']} ({l['kind']})" for l in links) or second_look_label(lang, "none"),
        now=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _check(ollama, semaphore, rule, result: dict, config: dict, db, tools: list, lang: str) -> dict:
    links = [l for kind in ("docker", "ssh") for l in diag.links_for_rule(db, rule, kind)]
    messages = [{"role": "system", "content": SECOND_LOOK_SYSTEM.get(lang, SECOND_LOOK_SYSTEM["fr"])},
                {"role": "user", "content": _task(rule, result, links, lang)}]
    options = llm_options(config)
    allowed = {t["function"]["name"] for t in tools}
    checks, seen, tool_addresses_ai = [], set(), False

    async def _chat(**kw):
        async with semaphore:
            res = await ollama.chat(messages, url=config.get("ollama_url"), model=config.get("ollama_model"),
                                    think=config.get("ollama_think_tools", True), options=options, **kw)
        if res.get("error"):
            raise RuntimeError(res["error"])
        return res

    answer = None   # réponse donnée sans nouvel appel d'outil : si c'est déjà le JSON demandé, pas de tour final
    nudged = False
    for _ in range(MAX_ROUNDS):
        if len(checks) >= MAX_CALLS:
            break
        res = await _chat(tools=tools)
        calls = res.get("tool_calls") or []
        if not calls and not checks and not nudged:
            # Mesuré : sans réflexion, gemma4:e4b répond le verdict d'emblée, « l'état sera vérifié », sans rien vérifier.
            # Une relance, une seule : il appelle alors les outils, ou redonne sa réponse si rien n'est vérifiable.
            nudged = True
            messages.append({"role": "assistant", "content": res.get("content") or ""})
            messages.append({"role": "user", "content": second_look_label(lang, "nudge")})
            continue
        if not calls:
            answer = res.get("content") or ""
            break
        messages.append({"role": "assistant", "content": res.get("content") or "", "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function") or {}
            name, args = fn.get("name", ""), _args(fn.get("arguments"))
            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            if name not in allowed:
                out = second_look_label(lang, "not_allowed")
            elif key in seen:
                out = label(lang, "tool_duplicate")
            elif len(checks) >= MAX_CALLS:
                out = label(lang, "tool_limit")
            else:
                seen.add(key)
                out = await llm_tools.execute_tool(name, args, rule_id=rule.id)
                tool_addresses_ai = tool_addresses_ai or addresses_the_ai(out.splitlines())
                checks.append({"name": name, "args": args, "label": llm_tools.tool_label(name, args, lang),
                               "error": out.startswith("Erreur"), "excerpt": scrub(out[:KEPT_CHARS])})
                out = fence(llm_tools.truncate_output(out, TOOL_CHARS))
            messages.append({"role": "tool", "content": out, "tool_name": name})

    if not checks:
        return {"status": "no_check", "checks": []}
    data = _load_json(answer) if answer else None
    if not (data and data.get("verdict")):
        if answer:
            messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": second_look_label(lang, "final")})
        res = await _chat(format=SECOND_LOOK_SCHEMA)
        data = _load_json(res.get("content") or "") or {}
    verdict = data.get("verdict") if data.get("verdict") in ("worse", "confirmed", "better", "unclear") else "unclear"
    findings = scrub(" ".join(str(data.get("findings") or "").split()))[:FINDINGS_CHARS]
    return {"status": "done", "checks": checks, "verdict": verdict, "proposed": data.get("severity"),
            "findings": findings, "tool_addresses_ai": tool_addresses_ai}


def _apply(result: dict, record: dict, lang: str) -> None:
    """Reporte le second regard sur le résultat de l'analyse : gravité, et une section dans l'analyse affichée."""
    before, after = record["severity_before"], record["severity_after"]
    if after != before:
        result["severity"] = after
        if result.get("data"):
            result["data"] = {**result["data"], "severity": after}
    if record["status"] != "done":
        return
    change = (second_look_label(lang, "raised" if RANK[after] > RANK[before] else "lowered", before=before, after=after)
              if after != before else second_look_label(lang, "kept", severity=before))
    lines = [f"**{second_look_label(lang, 'section')}** ({change})"]
    if record.get("findings"):
        lines.append(record["findings"])
    lines.append(f"{second_look_label(lang, 'checked')} : " + " · ".join(c["label"] for c in record["checks"]))
    if record.get("tool_addresses_ai"):
        lines.append(f"*{second_look_label(lang, 'tool_addresses_ai')}*")
    result["markdown"] = (result.get("markdown") or "").rstrip() + "\n\n" + "\n\n".join(lines)
    if after != before and record.get("findings") and result.get("summary"):
        # La notification courte n'a que le résumé : elle doit dire pourquoi la gravité a changé
        result["summary"] = f"{result['summary']} {second_look_label(lang, 'section')} : {record['findings']}"[:500]


async def run(ollama, semaphore, rule, result: dict, config: dict, db) -> Optional[dict]:
    """Second regard sur le résultat d'analyze_alert. Modifie result (gravité, analyse) et renvoie l'enregistrement
    à garder avec l'analyse, ou None quand il n'y a pas lieu d'en faire."""
    lang = config.get("ollama_prompt_lang", "fr")
    reason = skip_reason(config, result)
    if reason and reason != "suspicious":
        return None
    tools = tools_for_rule(db, rule)
    if not tools:
        return None   # sans connexion Docker ni SSH, il n'y a rien à vérifier : la carte n'en dit rien
    if reason == "suspicious":
        return {"status": "skipped_suspicious", "checks": [], "severity_before": result.get("severity"),
                "severity_after": result.get("severity"), "at": datetime.utcnow().isoformat()}
    before = result.get("severity")
    started = datetime.utcnow()
    try:
        record = await asyncio.wait_for(_check(ollama, semaphore, rule, result, config, db, tools, lang), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        record = {"status": "error", "checks": [], "error": f"timeout {TIMEOUT_S:.0f} s"}
    except Exception as e:
        record = {"status": "error", "checks": [], "error": str(e)[:200]}
    after = before
    if record["status"] == "done":
        after = merge_severity(before, record.get("proposed"), record["verdict"], settings(config)["may_lower"],
                               record.get("tool_addresses_ai", False))
    record.update(severity_before=before, severity_after=after, at=started.isoformat(),
                  seconds=round((datetime.utcnow() - started).total_seconds(), 1))
    _apply(result, record, lang)
    logger.info("SecondLook", f"'{rule.name}' : {record['status']}, {len(record['checks'])} contrôle(s), "
                              f"{before} → {after} en {record['seconds']} s")
    return record
