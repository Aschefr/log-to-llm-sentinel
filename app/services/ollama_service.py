import json
import time
from typing import Optional, AsyncGenerator, List, Union
import httpx

from app import logger

# (url, modèle) -> set de capacités (/api/show)
_capabilities_cache: dict = {}


def strip_think(text: str) -> str:
    """Retire les blocs <think>…</think> (y compris un bloc non fermé en fin de texte)."""
    result = text
    while "<think>" in result:
        start = result.find("<think>")
        end = result.find("</think>")
        if end != -1:
            result = result[:start] + result[end+8:]
        else:
            result = result[:start]
            break
    return result.strip()


class OllamaService:
    """
    Service de communication avec Ollama pour l'analyse des logs.
    """

    async def analyze_async(
        self,
        prompt: str,
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        options: Optional[dict] = None,
        think: bool = True,
    ) -> str:
        """
        Version asynchrone d'analyse blindée contre les timeouts et les balises coupées.
        """
        combined_text = ""
        last_log_time = time.time()
        
        async for chunk in self.generate_stream(prompt, url, model, options, think):
            if "error" in chunk:
                return f"[Erreur Ollama] {chunk['error']}"
            
            # Compatibilité /api/chat (message.content) et /api/generate (response)
            text = chunk.get("message", {}).get("content", "") or chunk.get("response", "")
            thinking = chunk.get("message", {}).get("thinking", "") or chunk.get("thinking", "")
            
            if text:
                combined_text += text
            
            # Log de progression toutes les 5 secondes pour rassurer l'utilisateur
            if time.time() - last_log_time > 5.0:
                if thinking and not text:
                    logger.debug("OllamaService", f"Réflexion en cours... (modèle en cours de raisonnement)")
                else:
                    logger.debug("OllamaService", f"Réception en cours... ({len(combined_text)} car. reçus)")
                last_log_time = time.time()
            
            if chunk.get("done"):
                logger.debug("OllamaService", "Fin du stream (done: true)")
                break

        full_text = strip_think(combined_text)
        logger.debug("OllamaService", f"Analyse terminée. Taille finale: {len(full_text)} car.")

        return full_text.strip() if full_text else "Aucune réponse d'Ollama"

    async def chat(
        self,
        messages: List[dict],
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        options: Optional[dict] = None,
        think: bool = True,
        format: Optional[Union[str, dict]] = None,
        tools: Optional[List[dict]] = None,
    ) -> dict:
        """
        Appel /api/chat avec de vrais rôles (system/user/assistant/tool).
        Retourne {"content", "thinking", "tool_calls", "error"} ; content est nettoyé des balises <think>.
        """
        content, thinking, tool_calls = "", "", []
        async for chunk in self.generate_stream(None, url, model, options, think,
                                                messages=messages, format=format, tools=tools):
            if "error" in chunk:
                return {"content": strip_think(content), "thinking": thinking, "tool_calls": tool_calls,
                        "error": str(chunk["error"])}
            msg = chunk.get("message", {})
            content += msg.get("content", "") or ""
            thinking += msg.get("thinking", "") or ""
            tool_calls.extend(msg.get("tool_calls") or [])
            if chunk.get("done"):
                break
        return {"content": strip_think(content), "thinking": thinking, "tool_calls": tool_calls, "error": None}

    async def get_capabilities(self, url: str, model: str) -> set:
        """Capacités annoncées par le modèle via /api/show (ex: 'tools', 'thinking'). Mises en cache par (url, modèle)."""
        key = ((url or "").rstrip("/"), model)
        if key in _capabilities_cache:
            return _capabilities_cache[key]
        caps = set()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                r = await client.post(f"{key[0]}/api/show", json={"model": model})
                if r.status_code == 200:
                    caps = set(r.json().get("capabilities") or [])
                    _capabilities_cache[key] = caps
        except Exception as e:
            logger.warning("OllamaService", f"Impossible de lire les capacités de {model} : {e}")
        return caps

    async def generate_stream(
        self,
        prompt: Optional[str],
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        options: Optional[dict] = None,
        think: bool = True,
        messages: Optional[List[dict]] = None,
        format: Optional[Union[str, dict]] = None,
        tools: Optional[List[dict]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Génère une réponse en streaming via Ollama (Asynchrone).
        `messages` (rôles explicites) est prioritaire sur `prompt` (envoyé comme unique message utilisateur).
        """
        base = (url or "http://ollama:11434").strip().rstrip("/")
        api_url = f"{base}/api/chat"
        payload = {
            "model": model,
            "messages": messages if messages is not None else [
                {"role": "user", "content": prompt}
            ],
            "stream": True,
        }
        if format is not None:
            payload["format"] = format
        if tools:
            payload["tools"] = tools
        # Copie : ne pas muter le dict d'options de l'appelant
        options = dict(options) if options else {}

        # On n'envoie 'think': False que si l'utilisateur veut explicitement désactiver 
        # le raisonnement. Selon la version d'Ollama, c'est à la racine ou dans options.
        if think is False:
            payload["think"] = False
            options["think"] = False
            
        if options:
            payload["options"] = options

        preview = prompt if prompt is not None else (payload["messages"][-1].get("content") or "")
        logger.debug("OllamaService", f"Streaming à {api_url} | modèle={model} | prompt={preview[:300]}...")
        try:
            # Timeout de connexion de 5s pour détecter un Ollama figé immédiatement
            timeout = httpx.Timeout(None, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", api_url, json=payload) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        logger.error("OllamaService", f"Erreur streaming Ollama ({response.status_code}): {err_body.decode()}")
                        yield {"error": f"Ollama HTTP {response.status_code}"}
                        return

                    logger.debug("OllamaService", "Flux streaming ouvert, réception des données...")
                    async for line in response.aiter_lines():
                        if line:
                            chunk = json.loads(line)
                            if "error" in chunk:
                                logger.error("OllamaService", f"Erreur dans le chunk: {chunk['error']}")
                            
                            yield chunk
        except Exception as e:
            logger.error("OllamaService", f"Exception pendant le stream : {str(e)}")
            yield {"error": str(e)}
