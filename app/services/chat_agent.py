"""
Boucle agent du chat : le LLM peut appeler des outils (lecture des logs, historique, propositions d'actions)
entre deux tours de génération, dans des limites strictes pour éviter l'emballement.
"""
import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from app import logger
from app.services.llm_tools import ToolTagFilter, available_tools, execute_tool, tool_label, truncate_output
from app.services.prompts import label

TOOL_BUDGET_RATIO = 0.4   # part max du contexte occupée par les sorties d'outils
CHARS_PER_TOKEN = 3.0


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

    async def _generate(tools_now, final=False):
        """Un tour de génération : le texte visible part vers result et emit_text.
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
                _show(tags.feed(think.feed(msg.get("content", "") or "")))
                tool_calls.extend(msg.get("tool_calls") or [])
                if chunk.get("done"):
                    break
        _show(tags.flush())
        if not tool_calls and tools_now:
            tool_calls = tags.calls   # appel écrit en texte : exécuté comme un vrai appel
        return content, tool_calls

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

            for tc in tool_calls:
                fn = tc.get("function", {})
                name, args = fn.get("name", ""), _parse_args(fn.get("arguments"))
                key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                call_id = len(result.tools) + 1
                if key in seen:
                    output = label(lang, "tool_duplicate")
                elif calls >= limits.max_calls:
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

    try:
        await asyncio.wait_for(_turn(), timeout=limits.turn_timeout)
    except asyncio.TimeoutError:
        logger.warning("ChatAgent", f"Tour de chat interrompu après {limits.turn_timeout:.0f}s")
        tail = "\n\n" + label(lang, "turn_timeout")
        result.text += tail
        emit_text(tail)
    if not result.text.strip():
        # Un silence ressemble à une panne : l'utilisateur doit savoir que le modèle n'a rien écrit
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
