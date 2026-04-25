import json
import time
from typing import Optional, AsyncGenerator
import httpx

from app import logger


class OllamaService:
    """
    Service de communication avec Ollama pour l'analyse des logs.
    """

    def analyze(
        self,
        prompt: str,
        url: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        timeout: int = 180,
        retries: int = 2,
        retry_delay_s: float = 2.0,
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

        attempts = max(1, int(retries) + 1)
        last_err: Optional[str] = None

        logger.debug("OllamaService", f"Appel à {api_url} | modèle={model}")

        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(api_url, json=payload)
                    response.raise_for_status()
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

        logger.debug("OllamaService", f"Streaming à {api_url} | modèle={model}")
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", api_url, json=payload) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        logger.error("OllamaService", f"Erreur streaming Ollama ({response.status_code}): {err_body.decode()}")
                        yield {"error": f"Ollama HTTP {response.status_code}"}
                        return

                    async for line in response.aiter_lines():
                        if line:
                            yield json.loads(line)
        except Exception as e:
            logger.error("OllamaService", f"Exception pendant le stream : {str(e)}")
            yield {"error": str(e)}
