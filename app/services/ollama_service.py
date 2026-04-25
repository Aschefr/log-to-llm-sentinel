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
            if not text:
                if chunk.get("done"): break
                continue

            buffer += text
            
            # Gestion robuste des balises <think> et </think>
            # On traite le buffer tant qu'on y trouve des balises
            while True:
                if not is_thinking:
                    if "<think>" in buffer:
                        parts = buffer.split("<think>", 1)
                        full_text += parts[0]
                        buffer = parts[1]
                        is_thinking = True
                        continue
                    else:
                        # On ne peut libérer le buffer que s'il ne contient pas un début de balise
                        # Pour éviter de libérer "<th" par exemple.
                        if "<" in buffer and not buffer.endswith(">"):
                            # On garde ce qui commence par < pour le prochain chunk
                            idx = buffer.find("<")
                            full_text += buffer[:idx]
                            buffer = buffer[idx:]
                            break
                        else:
                            full_text += buffer
                            buffer = ""
                            break
                else:
                    if "</think>" in buffer:
                        parts = buffer.split("</think>", 1)
                        buffer = parts[1]
                        is_thinking = False
                        continue
                    else:
                        # On est en train de penser, on vide le buffer car on ignore tout
                        buffer = ""
                        break

            # Log de progression
            if len(full_text) - last_log_len > 100:
                logger.debug("OllamaService", f"Analyse en cours... ({len(full_text)} car. générés)")
                last_log_len = len(full_text)
        
        # On ajoute le reste du buffer si on n'est pas en train de penser
        if not is_thinking:
            full_text += buffer

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
        # On n'envoie 'think' que si explicitement spécifié
        if think is not None:
            payload["think"] = think
            
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
