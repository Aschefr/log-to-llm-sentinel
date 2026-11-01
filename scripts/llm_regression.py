"""
Jeu de régression du prompt d'analyse : rejoue des cas de logs contre le modèle configuré et compare la sévérité.

Usage (depuis la racine du projet, ou dans le conteneur) :
    python scripts/llm_regression.py scripts/llm_regression.example.json
    python scripts/llm_regression.py data/llm_regression.json --model qwen3:14b --ctx 16384
    docker exec <conteneur> python scripts/llm_regression.py data/llm_regression.json

Format d'un cas (fichier JSON = liste de cas) :
    {"name": "...", "rule": "Nom de règle", "application_context": "...", "lines": ["ligne 1", "..."],
     "context_before": ["..."], "context_after": ["..."], "expected": "warning" | ["info", "warning"]}

Gardez vos cas réels (noms d'hôtes, IP) dans data/, qui n'est pas versionné.
Sans --url/--model/--ctx, la configuration est lue dans la base de Sentinel.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.alert_analysis import build_alert_messages, parse_alert_response  # noqa: E402
from app.services.ollama_service import OllamaService  # noqa: E402
from app.services.orchestrator import Orchestrator  # noqa: E402
from app.services.prompts import ALERT_SCHEMA  # noqa: E402


def load_config(args) -> dict:
    config = {}
    try:
        from app.services.app_config import load_config_dict
        config = load_config_dict()
    except Exception as e:
        print(f"(configuration Sentinel illisible : {e} — valeurs par défaut / arguments utilisés)")
    for key, val in (("ollama_url", args.url), ("ollama_model", args.model), ("ollama_ctx", args.ctx)):
        if val:
            config[key] = val
    config.setdefault("ollama_url", "http://ollama:11434")
    config.setdefault("ollama_model", "gemma4:e4b")
    config.setdefault("ollama_ctx", 4096)
    config.setdefault("ollama_temp", 0.1)
    config.setdefault("ollama_think", True)
    config.setdefault("max_log_chars", 5000)
    if args.lang:
        config["ollama_prompt_lang"] = args.lang
    if args.think is not None:
        config["ollama_think"] = args.think
    config.setdefault("ollama_prompt_lang", "fr")
    return config


async def run_case(ollama, case: dict, config: dict) -> dict:
    lang = config["ollama_prompt_lang"]
    rule = SimpleNamespace(name=case.get("rule", "Test"), log_file_path=case.get("log_file_path", "/var/log/test.log"),
                           application_context=case.get("application_context", ""))
    lines = case["lines"]
    events = Orchestrator.bundle_lines(lines, len(lines), case.get("window_s", 60), config["max_log_chars"], lang)
    context = None
    if case.get("context_before") or case.get("context_after"):
        context = {"before": case.get("context_before") or [], "between": None, "after": case.get("context_after") or []}
    messages = build_alert_messages(rule, events, case.get("keywords"), context, None, config, datetime.utcnow())
    t0 = time.time()
    res = await ollama.chat(messages, config["ollama_url"], config["ollama_model"],
                            options={"temperature": config["ollama_temp"], "num_ctx": config["ollama_ctx"]},
                            think=config["ollama_think"], format=ALERT_SCHEMA)
    elapsed = time.time() - t0
    if res["error"]:
        return {"severity": None, "summary": f"[Erreur] {res['error']}", "structured": False, "elapsed": elapsed}
    parsed = parse_alert_response(res["content"], lang)
    return {"severity": parsed["severity"], "summary": parsed["summary"] or "", "structured": parsed["data"] is not None,
            "elapsed": elapsed}


async def main():
    parser = argparse.ArgumentParser(description="Jeu de régression du prompt d'analyse d'alerte")
    parser.add_argument("cases", help="Fichier JSON de cas")
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--ctx", type=int)
    parser.add_argument("--lang", choices=["fr", "en"])
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=None, help="Forcer / désactiver le raisonnement")
    parser.add_argument("--only", help="N'exécuter que les cas dont le nom contient ce texte")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if args.only.lower() in c.get("name", "").lower()]
    config = load_config(args)
    print(f"Modèle : {config['ollama_model']} @ {config['ollama_url']} (num_ctx {config['ollama_ctx']}) — {len(cases)} cas\n")

    ollama = OllamaService()
    failures = 0
    for case in cases:
        expected = case["expected"] if isinstance(case["expected"], list) else [case["expected"]]
        r = await run_case(ollama, case, config)
        ok = r["severity"] in expected and r["structured"]
        failures += not ok
        flag = "PASS" if ok else "FAIL"
        note = "" if r["structured"] else " (sortie non structurée)"
        print(f"{flag}  {case.get('name', '?')[:50]:<50} attendu {'/'.join(expected):<16} obtenu {str(r['severity']):<9} {r['elapsed']:5.0f}s{note}")
        print(f"      {r['summary'][:160]}")
    print(f"\n{len(cases) - failures}/{len(cases)} cas conformes")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
