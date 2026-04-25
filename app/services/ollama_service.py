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
        Version asynchrone d'analyse utilisant le streaming pour éviter les timeouts
        sur les longues générations (CPU).
        """
        full_text = ""
        is_thinking = False
        last_log_len = 0
        
        async for chunk in self.generate_stream(prompt, url, model, options, think):
            if "error" in chunk:
                return f"[Erreur Ollama] {chunk['error']}"
            
            text = chunk.get("response", "")
            if not text: continue

            # Filtrage des balises <think>
            if "<think>" in text:
                is_thinking = True
                text = text.split("<think>")[0]
            if "</think>" in text:
                is_thinking = False
                text = text.split("</think>")[-1]

            if not is_thinking and text:
                full_text += text
                
                # Petit log de progression tous les 100 caractères
                if len(full_text) - last_log_len > 100:
                    logger.debug("OllamaService", f"Analyse en cours... ({len(full_text)} car. reçus)")
                    last_log_len = len(full_text)
        
        return full_text if full_text else "Aucune réponse d'Ollama"

    def analyze(
        self,
        prompt: str,
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        timeout: int = 180,
        retries: int = 2,
        retry_delay_s: float = 2.0,
        options: Optional[dict] = None,
        think: bool = True,
    ) -> str:
        """
        Envoie un prompt à Ollama et retourne la réponse (Synchrone).
        """
        base = (url or "http://ollama:11434").strip().rstrip("/")
        api_url = f"{base}/api/generate"
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        # On n'envoie 'think' que si explicitement spécifié (pour éviter les 500 sur modèles non-compatibles)
        if think is not None:
            payload["think"] = think

        if options:
            payload["options"] = options

        attempts = max(1, int(retries) + 1)
        last_err: Optional[str] = None

        logger.debug("OllamaService", f"Appel à {api_url} (Timeout {timeout}s) | modèle={model} | prompt={prompt[:300]}...")

        for attempt in range(1, attempts + 1):
            try:
                # On force un timeout plus long pour éviter les coupures Sentinel
                with httpx.Client(timeout=300) as client:
                    response = client.post(api_url, json=payload)
                    response.raise_for_status()
                    logger.debug("OllamaService", f"Réponse HTTP {response.status_code} reçue en {response.elapsed.total_seconds():.1f}s")
                    result = response.json()
                    answer = result.get("response", "Aucune réponse d'Ollama")
                    return answer

            except httpx.HTTPStatusError as e:
                detail = e.response.text
                last_err = f"[Erreur Ollama] HTTP {e.response.status_code}: {detail}"
                logger.error("OllamaService", last_err)
                if e.response.status_code in (500, 503) and attempt < attempts:
                    time.sleep(retry_delay_s * attempt)
                    continue
                return last_err

            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_err = f"[Erreur Ollama] Connexion impossible ou délai dépassé: {str(e)}"
                logger.error("OllamaService", last_err)
                if attempt < attempts:
                    time.sleep(retry_delay_s * attempt)
                    continue
                return last_err

            except Exception as e:
                last_err = f"[Erreur Ollama] {str(e)}"
                logger.error("OllamaService", last_err)
                return last_err

        return last_err or "[Erreur Ollama] Erreur inconnue"

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
