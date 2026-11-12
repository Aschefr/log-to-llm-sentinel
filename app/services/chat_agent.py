"""
Boucle agent du chat : le LLM peut appeler des outils (lecture des logs, historique, propositions d'actions)
entre deux tours de génération, dans des limites strictes pour éviter l'emballement.
"""
import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from app import logger
from app.services.llm_tools import ACTIONS, PROPOSAL_HINT, ToolTagFilter, available_tools, execute_tool, tool_label, truncate_output
from app.services.prompts import label

TOOL_BUDGET_RATIO = 0.4   # part max du contexte occupée par les sorties d'outils
CHARS_PER_TOKEN = 3.0


# Réponse qui annonce une action à confirmer (vu en prod : « **Action proposée :** `trigger_meta_analysis` » sans appel
# d'outil, donc sans carte). Le nom interne d'une action suffit à le signaler. Une action déjà numérotée (« action #12 »)
# est une vraie proposition, d'un tour précédent : pas de relance.
_ANNOUNCE_RE = re.compile(
    r"action\s+(?:propos[ée]e|à\s+(?:valider|confirmer))|(?:n[ée]cessite|attend|requiert)\s+(?:ta\s+|votre\s+|la\s+|une\s+)?confirmation"
    r"|\bj['’]ai\s+propos[ée]\s+(?:l['’]action|une\s+action)|\bje\s+(?:te\s+|vous\s+)?propose\s+l['’]action"
    r"|(?:carte|bouton)\s+(?:de\s+confirmation|ci-dessous)|confirme[rz]?\s+ci-dessous"
    r"|marqu[ée]e?s?\s+comme\s+r[ée]solue?s?|\bl['’]incident\s+est\s+(?:clos|cl[ôo]tur[ée])"
    r"|proposed\s+action|marked\s+as\s+resolved|incident\s+(?:is|has\s+been)\s+closed|(?:requires?|needs?|awaits?|awaiting)\s+(?:your\s+)?confirmation"
    r"|\bi(?:['’]ve|\s+have)\s+proposed\b|confirm\s+(?:it\s+)?below"
    r"|\b(?:" + "|".join(ACTIONS) + r")\b", re.IGNORECASE)
_REAL_ACTION_RE = re.compile(r"action\s*#\s*\d+", re.IGNORECASE)


# Appel d'outil écrit dans le texte au lieu d'être émis (mesuré sur gemma4:e4b, 9 tours sur 50) :
# « propose_action(action='mark_resolved', …) » ou « propose_action{action:<|"|>…<|"|>} », jusqu'à la fin du paragraphe.
_RAW_CALL_RE = re.compile(r"(?:```[\w-]*[ \t]*\n?)?`?(?:call:)?\bpropose_action\s*[({][^\n]*(?:\n(?![ \t]*\n)[^\n]*)*", re.IGNORECASE)
_RAW_TOKEN_RE = re.compile(r"<\|\"\|>")


def strip_raw_calls(text: str) -> str:
    """Retire du texte final les appels d'outil recopiés et la consigne de propose_action (la carte suffit)."""
    clean = _RAW_TOKEN_RE.sub('"', _RAW_CALL_RE.sub("", text or "")).replace(PROPOSAL_HINT, "")
    clean = re.sub(r"\n{3,}", "\n\n", clean).rstrip()
    return clean + ("\n\n" if clean and text.endswith("\n\n") else "")


def announces_action(text: str) -> bool:
    """La réponse dit qu'une action est proposée ou attend une confirmation."""
    text = text or ""
    return bool(_ANNOUNCE_RE.search(text)) and not _REAL_ACTION_RE.search(text)


# Offre d'une action en toutes lettres (« je propose de marquer cette alerte comme résolue »). Seule une méta-analyse
# compte pour lancer/déclencher : « je propose de lancer une recherche » n'est pas une action à confirmer.
_OFFER_RE = re.compile(
    r"\b(?:je\s+(?:te\s+|vous\s+)?propose|je\s+(?:peux|pourrais)|i\s+(?:can|could|propose|suggest))\s+(?:de\s+|d['’]\s*|to\s+)?"
    r"(?:marquer|clore|cl[ôo]turer|ajouter|retirer|exclure|renvoyer|mark|close|add|remove|exclude|resend"
    r"|(?:d[ée]clencher|lancer|relancer|run|trigger)\s+(?:une\s+|la\s+|a\s+|the\s+)?m[ée]ta-?\s?analy)", re.IGNORECASE)


def still_offers_action(text: str) -> bool:
    """Après la relance sans appel, le texte nettoyé promet-il encore une action ? Sinon (l'annonce n'était que l'appel
    recopié, mesuré : « je propose de ne rien faire »), l'avertissement n'a pas lieu d'être."""
    clean = strip_raw_calls(text)
    return announces_action(clean) or bool(_OFFER_RE.search(clean) and not _REAL_ACTION_RE.search(clean))


@dataclass
class AgentLimits:
    max_rounds: int = 4    # tours de génération avec outils (un tour final sans outils suit)
    max_calls: int = 8     # appels d'outils au total
    turn_timeout: float = 600.0


@dataclass
class AgentResult:
    text: str = ""
    tools: List[dict] = field(default_factory=list)    # trace : name, args, label, result
    action_ids: List[int] = field(default_factory=list)


class _ThinkFilter:
    """Retire les blocs <think>…</think> d'un flux de tokens."""
    def __init__(self):
        self.inside = False

    def feed(self, text: str) -> str:
        out = ""
        while text:
            if self.inside:
                end = text.find("</think>")
                if end == -1:
                    return out
                text, self.inside = text[end + 8:], False
            else:
                start = text.find("<think>")
                if start == -1:
                    return out + text
                out, text, self.inside = out + text[:start], text[start + 7:], True
        return out


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except (TypeError, ValueError):
        return {}


async def run_agent_turn(ollama, semaphore, messages: List[dict], config: dict,
                         emit_text: Callable[[str], None], emit_event: Callable[[dict], None],
                         use_tools: bool, conversation_id: Optional[int] = None,
                         limits: AgentLimits = AgentLimits(), lang: str = "fr",
                         result: Optional[AgentResult] = None) -> AgentResult:
    """
    Exécute un tour de chat complet. `messages` est complété au fil des appels d'outils.
    emit_text reçoit les tokens visibles, emit_event les événements d'outils et d'actions.
    `result` se remplit au fil du tour : l'appelant le garde pour conserver ce qui précède un arrêt.
    """
    result = result if result is not None else AgentResult()
    calls, seen = 0, {}
    budget = int(config.get("ollama_ctx", 4096) * CHARS_PER_TOKEN * TOOL_BUDGET_RATIO)
    used = 0
    options = {"temperature": config.get("ollama_temp", 0.1), "num_ctx": config.get("ollama_ctx", 4096)}

    def _show(visible):
        nonlocal content
        if visible:
            content += visible
            result.text += visible
            emit_text(visible)

    content = ""

    async def _generate(tools_now, final=False, silent=False):
        """Un tour de génération : le texte visible part vers result et emit_text (rien si silent).
        final : outils retirés après usage. Sans réflexion : chez Alanbix, qwen3.8:27b et bonsai-2:27b réfléchissaient
        alors sans fin (300 s sans réponse, une fois sur deux) ; sans outils, la réflexion n'apporte rien (mesuré)."""
        nonlocal content
        content, tool_calls, think, tags = "", [], _ThinkFilter(), ToolTagFilter()
        stream = ollama.generate_stream(
            None, url=config.get("ollama_url"), model=config.get("ollama_model"),
            options=options, think=False if final else config.get("ollama_think_tools", True),
            messages=messages, tools=tools_now,
        )
        # aclosing : referme le flux HTTP dès la sortie de boucle
        async with semaphore, contextlib.aclosing(stream):
            async for chunk in stream:
                if "error" in chunk:
                    raise RuntimeError(chunk["error"])
                msg = chunk.get("message", {})
                visible = tags.feed(think.feed(msg.get("content", "") or ""))
                if not silent:
                    _show(visible)
                tool_calls.extend(msg.get("tool_calls") or [])
                if chunk.get("done"):
                    break
        if not silent:
            _show(tags.flush())
        if not tool_calls and tools_now:
            tool_calls = tags.calls   # appel écrit en texte : exécuté comme un vrai appel
        return content, tool_calls

    async def _run_calls(tool_calls, extra=False):
        """extra : appel de la relance, permis même quand la limite d'appels du tour est atteinte."""
        nonlocal calls, used
        for tc in tool_calls:
            fn = tc.get("function", {})
            name, args = fn.get("name", ""), _parse_args(fn.get("arguments"))
            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            call_id = len(result.tools) + 1
            if key in seen:
                output = label(lang, "tool_duplicate")
            elif calls >= limits.max_calls and not extra:
                output = label(lang, "tool_limit")
            else:
                calls += 1
                lbl = tool_label(name, args, lang)
                emit_event({"type": "tool_call", "id": call_id, "name": name, "label": lbl})
                output = await execute_tool(name, args, conversation_id)
                if used + len(output) > budget:
                    output = truncate_output(output, max(200, budget - used)) + "\n" + label(lang, "tool_budget")
                used += len(output)
                seen[key] = True
                n_lines = len(output.splitlines())
                emit_event({"type": "tool_result", "id": call_id, "name": name,
                            "error": output.startswith("Erreur"), "lines": n_lines, "output": output})
                result.tools.append({"name": name, "args": args, "label": lbl, "result": output})
                if name == "propose_action" and output.startswith("Action #"):
                    action_id = int(output.split("#", 1)[1].split()[0])
                    result.action_ids.append(action_id)
                    emit_event({"type": "action", "id": action_id})
            messages.append({"role": "tool", "content": output, "tool_name": name})

    async def _turn():
        nonlocal calls, used
        for round_idx in range(limits.max_rounds + 1):
            tools_now = available_tools(conversation_id) if use_tools and round_idx < limits.max_rounds and calls < limits.max_calls else None
            if use_tools and tools_now is None:
                # En "user" : en "system", qwen3.8 l'ignore et redemande des outils même sans en recevoir, donc n'écrit rien
                messages.append({"role": "user", "content": label(lang, "tools_exhausted")})

            final = use_tools and tools_now is None
            content, tool_calls = await _generate(tools_now, final=final)

            # Au tour final (outils retirés), on ignore d'éventuels appels : la réponse texte fait foi
            if not tool_calls or tools_now is None:
                # qwen3.8 s'arrête parfois sans rien écrire après beaucoup d'outils : une relance suffit (18 fois sur 18)
                if not result.text.strip():
                    messages.append({"role": "user", "content": label(lang, "empty_retry")})
                    await _generate(None, final=use_tools)
                return
            if result.text and not result.text.endswith("\n"):
                result.text += "\n\n"
                emit_text("\n\n")
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            await _run_calls(tool_calls)

    async def _ensure_action():
        """Action annoncée sans appel d'outil : une relance silencieuse avec le seul outil propose_action, puis, s'il
        n'est toujours pas appelé, un avertissement visible plutôt qu'une carte promise qui n'existe pas."""
        if not use_tools or result.action_ids or not announces_action(result.text):
            return
        logger.info("ChatAgent", "Action annoncée sans appel d'outil : relance pour obtenir propose_action")
        propose = [t for t in available_tools(conversation_id) if t.get("function", {}).get("name") == "propose_action"]
        messages.append({"role": "assistant", "content": result.text})
        messages.append({"role": "user", "content": label(lang, "action_nudge")})
        _, tool_calls = await _generate(propose, final=True, silent=True)
        tool_calls = [tc for tc in tool_calls if tc.get("function", {}).get("name") == "propose_action"]
        if tool_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
            await _run_calls(tool_calls[:1], extra=True)
        if not result.action_ids and not still_offers_action(result.text):
            logger.info("ChatAgent", "Relance sans appel ; le texte nettoyé n'annonce plus d'action, pas d'avertissement")
        elif not result.action_ids:
            logger.warning("ChatAgent", "Action annoncée, toujours pas proposée après la relance")
            notice = "\n\n" + label(lang, "action_missing")
            result.text += notice
            emit_text(notice)

    async def _turn_and_check():
        await _turn()
        await _ensure_action()

    try:
        await asyncio.wait_for(_turn_and_check(), timeout=limits.turn_timeout)
    except asyncio.TimeoutError:
        logger.warning("ChatAgent", f"Tour de chat interrompu après {limits.turn_timeout:.0f}s")
        tail = "\n\n" + label(lang, "turn_timeout")
        result.text += tail
        emit_text(tail)
    result.text = strip_raw_calls(result.text)
    if not result.text.strip() and not result.action_ids:
        # Un silence ressemble à une panne : l'utilisateur doit savoir que le modèle n'a rien écrit (une carte d'action suffit)
        logger.warning("ChatAgent", f"Réponse vide après {len(result.tools)} appel(s) d'outils")
        empty = label(lang, "empty_answer")
        result.text += empty
        emit_text(empty)
    return result


def replay_tool_messages(tools: List[dict]) -> List[dict]:
    """Reconstitue les messages assistant(tool_calls) + tool d'un tour passé, pour l'historique du chat."""
    if not tools:
        return []
    msgs = [{"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": t["name"], "arguments": t.get("args") or {}}} for t in tools]}]
    msgs += [{"role": "tool", "content": t.get("result", ""), "tool_name": t["name"]} for t in tools]
    return msgs
