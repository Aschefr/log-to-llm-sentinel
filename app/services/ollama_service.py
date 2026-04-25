import json
import time
from typing import Optional, AsyncGenerator
import httpx

from app import logger


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

        # Filtrage robuste après récupération
        result = combined_text
        while "<think>" in result:
            start = result.find("<think>")
            end = result.find("</think>")
            if end != -1:
                result = result[:start] + result[end+8:]
            else:
                result = result[:start]
                break
        
        full_text = result.strip()
        logger.debug("OllamaService", f"Analyse terminée. Taille finale: {len(full_text)} car.")
        
        return full_text.strip() if full_text else "Aucune réponse d'Ollama"

    async def generate_stream(
        self,
        prompt: str,
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        options: Optional[dict] = None,
        think: bool = True,
    ) -> AsyncGenerator[dict, None]:
        """
        Génère une réponse en streaming via Ollama (Asynchrone).
        """
        base = (url or "http://ollama:11434").strip().rstrip("/")
        api_url = f"{base}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": True,
        }
        if options is None:
            options = {}
            
        # On n'envoie 'think': False que si l'utilisateur veut explicitement désactiver 
        # le raisonnement. Selon la version d'Ollama, c'est à la racine ou dans options.
        if think is False:
            payload["think"] = False
            options["think"] = False
            
        if options:
            payload["options"] = options

        logger.debug("OllamaService", f"Streaming à {api_url} | modèle={model} | prompt={prompt[:300]}...")
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
