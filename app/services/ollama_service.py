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
        full_text = ""
        buffer = ""
        is_thinking = False
        last_log_len = 0
        
        async for chunk in self.generate_stream(prompt, url, model, options, think):
            if "error" in chunk:
                return f"[Erreur Ollama] {chunk['error']}"
            
            text = chunk.get("response", "")
            if text:
                logger.debug("OllamaService", f"Chunk reçu ({len(text)} chars)")
            
            if not text:
                if chunk.get("done"): 
                    logger.debug("OllamaService", "Fin du stream (done: true)")
                    break
                continue

            # Filtrage simple
            if not is_thinking:
                if "<think>" in text:
                    is_thinking = True
                    text = text.split("<think>", 1)[0]
                full_text += text
            else:
                if "</think>" in text:
                    is_thinking = False
                    full_text += text.split("</think>", 1)[-1]

            # Log de progression léger
            if len(full_text) - last_log_len > 100:
                logger.debug("OllamaService", f"Analyse en cours... ({len(full_text)} car.)")
                last_log_len = len(full_text)
        
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
        api_url = f"{base}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        # On n'envoie 'think': False que si l'utilisateur veut explicitement désactiver 
        # le raisonnement, car les vieilles versions d'Ollama plantent si elles reçoivent ce champ.
        if think is False:
            payload["think"] = False
            
        if options:
            payload["options"] = options

        logger.debug("OllamaService", f"Streaming à {api_url} | modèle={model} | prompt={prompt[:300]}...")
        try:
            async with httpx.AsyncClient(timeout=None) as client:
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
